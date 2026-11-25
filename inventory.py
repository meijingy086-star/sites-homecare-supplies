"""Household inventory domain and transactional SQLite storage. Standard library only."""
from __future__ import annotations
import copy
from contextlib import contextmanager
import hashlib
import json
import sqlite3
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SCHEMA = 1
MAX_QTY = 1_000_000
TZ = timezone(timedelta(hours=8))

def today():
    return datetime.now(TZ).date()

def uid():
    return uuid.uuid4().hex

def now():
    return datetime.now(TZ).isoformat(timespec='seconds')

def space_key(passphrase):
    if not isinstance(passphrase, str) or len(passphrase.strip()) < 10:
        raise ValueError('家庭口令至少10个字符，请妥善记住。')
    if len(passphrase) > 200:
        raise ValueError('家庭口令不能超过200个字符。')
    return hashlib.pbkdf2_hmac('sha256', passphrase.strip().encode(), b'homecare-supplies-space-v1', 180_000).hex()

def empty():
    return {'schema': SCHEMA, 'products': [], 'events': [], 'settings': {'expiry_days': 30}}

def integer(n, label, minimum=0):
    if type(n) is not int or not minimum <= n <= MAX_QTY:
        raise ValueError(f'{label}必须是{minimum}至{MAX_QTY}的整数。')
    return n

def string(s, label, limit=160, required=True):
    if not isinstance(s, str) or len(s) > limit or (required and not s.strip()):
        raise ValueError(f'{label}不能为空且不能超过{limit}个字符。' if required else f'{label}不能超过{limit}个字符。')
    return s.strip()

def expiry_date(s):
    if s is None or s == '':
        return None
    if not isinstance(s, str):
        raise ValueError('有效期格式应为YYYY-MM-DD。')
    try:
        d = date.fromisoformat(s)
        if d.isoformat() != s or not 1900 <= d.year <= 2200:
            raise ValueError()
        return s
    except ValueError:
        raise ValueError('有效期格式应为YYYY-MM-DD，年份应在1900至2200之间。') from None

def count_unit(s):
    s = string(s, '计数单位', 12)
    if any(c.isdigit() for c in s):
        raise ValueError('计数单位只填写“盒、包、个”等文字，数量请单独填写；例如填“盒”，不要填“5盒”。')
    return s

def product(state, pid):
    for p in state['products']:
        if p['id'] == pid:
            return p
    raise ValueError('未找到该用品，请刷新页面重试。')

def batch(state, pid, bid):
    p = product(state, pid)
    for b in p['batches']:
        if b['id'] == bid:
            return p, b
    raise ValueError('未找到该批次，请刷新页面重试。')

def event(state, kind, p=None, b=None, before=None, after=None, note=''):
    e = {'id': uid(), 'at': now(), 'kind': kind, 'product_id': p['id'] if p else '',
         'product_name': p['name'] if p else '', 'unit': p['unit'] if p else '',
         'batch_id': b['id'] if b else '', 'before': before, 'after': after,
         'after_revision': b['revision'] if b else None, 'note': note, 'undone': False}
    state['events'].append(e)
    state['events'] = state['events'][-500:]
    return e

def add_product(state, name, spec, unit, category, location, minimum, note, qty, expiry, lot=''):
    name = string(name, '用品名称', 80)
    unit = count_unit(unit)
    spec = string(spec, '规格', 120, False)
    # Same name/spec/unit should be one product with multiple batches.
    if any(not p['archived'] and (p['name'], p['spec'], p['unit']) == (name, spec, unit) for p in state['products']):
        raise ValueError('已有相同名称、规格和单位的用品，请到“使用与补充”为它新增批次。')
    p = {'id': uid(), 'name': name, 'spec': spec, 'unit': unit,
         'category': string(category, '分类', 40), 'location': string(location, '存放位置', 120),
         'minimum': integer(minimum, '最低库存'), 'note': string(note, '备注', 1000, False),
         'archived': False, 'batches': []}
    qty = integer(qty, '初始数量')
    b = {'id': uid(), 'lot': string(lot, '批次标记', 80, False), 'qty': qty,
         'expiry': expiry_date(expiry), 'revision': 1, 'created_at': now()}
    p['batches'].append(b)
    state['products'].append(p)
    event(state, '新增用品', p, b, 0, qty)
    return p['id']

def add_batch(state, pid, qty, expiry, lot=''):
    p = product(state, pid)
    if p['archived']:
        raise ValueError('用品已归档，请先恢复。')
    qty = integer(qty, '补充数量', 1)
    b = {'id': uid(), 'lot': string(lot, '批次标记', 80, False), 'qty': qty,
         'expiry': expiry_date(expiry), 'revision': 1, 'created_at': now()}
    p['batches'].append(b)
    event(state, '补充入库', p, b, 0, qty)
    return b['id']

def change_stock(state, pid, bid, amount, kind, note=''):
    p, b = batch(state, pid, bid)
    if p['archived']:
        raise ValueError('用品已归档，请先恢复。')
    note = string(note, '变动备注', 500, False)
    before = b['qty']
    if kind in ('记录使用', '清理出库'):
        amount = integer(amount, '数量', 1)
        if amount > before:
            raise ValueError(f'当前批次仅剩{before}{p["unit"]}，不能扣减{amount}{p["unit"]}。')
        if kind == '记录使用' and b['expiry'] and date.fromisoformat(b['expiry']) < today():
            raise ValueError('该批次已过有效期，不能登记为正常使用；请核对后登记清理出库或盘点校正。')
        after = before - amount
    elif kind == '盘点校正':
        if not note:
            raise ValueError('盘点校正请填写原因，便于以后核对。')
        after = integer(amount, '盘点后数量')
    else:
        raise ValueError('不支持的库存操作。')
    if before == after:
        raise ValueError('库存未发生变化，无需保存。')
    b['qty'] = after
    b['revision'] += 1
    event(state, kind, p, b, before, after, note)

def edit_product(state, pid, name, spec, location, minimum, category, note, unit=None):
    p = product(state, pid)
    newname, newspec = string(name, '用品名称', 80), string(spec, '规格', 120, False)
    newunit = count_unit(unit) if unit is not None else p['unit']
    oldunit = p['unit']
    if any(q['id'] != pid and not q['archived'] and (q['name'], q['spec'], q['unit']) == (newname, newspec, newunit) for q in state['products']):
        raise ValueError('修改后与现有用品重复，请使用不同名称或规格。')
    p.update(name=newname, spec=newspec, unit=newunit, location=string(location, '存放位置', 120),
             minimum=integer(minimum, '最低库存'), category=string(category, '分类', 40), note=string(note, '备注', 1000, False))
    if newunit != oldunit:
        for b in p['batches']:
            b['revision'] += 1
    event(state, '编辑资料', p, note=f'计数单位由“{oldunit}”改为“{newunit}”；各批次数量未自动换算。' if newunit != oldunit else '')

def edit_batch(state, pid, bid, expiry, lot):
    p, b = batch(state, pid, bid)
    b['expiry'] = expiry_date(expiry)
    b['lot'] = string(lot, '批次标记', 80, False)
    b['revision'] += 1
    event(state, '编辑批次', p, b, note='修改有效期或批次标记；未改变数量。')

def archive(state, pid, archived=True):
    p = product(state, pid)
    p['archived'] = bool(archived)
    event(state, '归档用品' if archived else '恢复用品', p)

def undo_stock(state, eid):
    e = next((e for e in state['events'] if e['id'] == eid), None)
    if not e or e['undone'] or e['kind'] not in ('记录使用', '清理出库', '盘点校正', '补充入库'):
        raise ValueError('该记录无法撤销，或已撤销。')
    p, b = batch(state, e['product_id'], e['batch_id'])
    if p['archived'] or b['revision'] != e['after_revision'] or b['qty'] != e['after']:
        raise ValueError('该批次之后已发生变动，不能直接撤销；请核对实际数量后使用盘点校正。')
    before = b['qty']
    b['qty'] = e['before']
    b['revision'] += 1
    e['undone'] = True
    event(state, '撤销变动', p, b, before, b['qty'], '撤销：'+e['kind'])

def status(b, days, on=None):
    on = on or today()
    if not b['expiry']:
        return '有效期待核对'
    remaining = (date.fromisoformat(b['expiry']) - on).days
    if remaining < 0:
        return '已过期'
    if remaining == 0:
        return '今日到期'
    if remaining <= days:
        return '即将到期'
    return '有效期内'

def summary(p, days=30, on=None):
    on = on or today()
    total = sum(b['qty'] for b in p['batches'])
    expired = sum(b['qty'] for b in p['batches'] if status(b, days, on) == '已过期')
    current = total - expired
    return {'total': total, 'expired': expired, 'current': current,
            'low': current < p['minimum'], 'need': max(0, p['minimum']-current),
            'attention': any(b['qty'] > 0 and status(b, days, on) != '有效期内' for b in p['batches'])}

def validate(data):
    if not isinstance(data, dict) or type(data.get('schema')) is not int or data['schema'] != SCHEMA:
        raise ValueError('不是本工具支持的备份格式。')
    if not isinstance(data.get('products'), list) or len(data['products']) > 1000:
        raise ValueError('备份用品列表不正确或超过1000种。')
    settings = data.get('settings')
    if not isinstance(settings, dict) or type(settings.get('expiry_days')) is not int or not 0 <= settings['expiry_days'] <= 365:
        raise ValueError('临期提醒设置不正确。')
    events = data.get('events')
    if not isinstance(events, list) or len(events) > 500:
        raise ValueError('操作历史不正确。')
    ids = set()
    def ident(v):
        if not isinstance(v, str) or len(v) != 32 or any(c not in '0123456789abcdef' for c in v) or v in ids:
            raise ValueError('备份中有重复或无效的记录编号。')
        ids.add(v)
    count = 0
    signatures = set()
    for p in data['products']:
        if not isinstance(p, dict):
            raise ValueError('备份用品记录不正确。')
        ident(p.get('id'))
        for k, limit, required in [('name',80,True),('spec',120,False),('unit',12,True),('category',40,True),('location',120,True),('note',1000,False)]:
            p[k] = string(p.get(k), k, limit, required)
        integer(p.get('minimum'), '最低库存')
        if type(p.get('archived')) is not bool or not isinstance(p.get('batches'), list) or not p['batches']:
            raise ValueError('用品归档状态或批次格式不正确。')
        signature = (p['name'], p['spec'], p['unit'])
        if not p['archived']:
            if signature in signatures:
                raise ValueError('备份中有重复用品，应将同名同规格同单位用品合并为批次后再导入。')
            signatures.add(signature)
        count += len(p['batches'])
        if count > 10000:
            raise ValueError('备份最多支持10000个批次。')
        for b in p['batches']:
            if not isinstance(b, dict):
                raise ValueError('批次格式不正确。')
            ident(b.get('id')); integer(b.get('qty'), '批次数量')
            b['expiry'] = expiry_date(b.get('expiry'))
            b['lot'] = string(b.get('lot'), '批次标记', 80, False)
            integer(b.get('revision'), '批次版本', 1)
            string(b.get('created_at'), '批次时间', 60)
    for e in events:
        if not isinstance(e, dict):
            raise ValueError('历史记录格式不正确。')
        ident(e.get('id'))
        for k, limit in [('at',60),('kind',40),('product_id',32),('batch_id',32),('product_name',80),('unit',12),('note',500)]:
            e[k] = string(e.get(k), k, limit, False)
        if type(e.get('undone')) is not bool:
            raise ValueError('历史撤销状态不正确。')
        for k in ['before','after','after_revision']:
            if e.get(k) is not None:
                integer(e[k], k)
    return data

def decode_backup(content):
    if len(content) > 2_000_000:
        raise ValueError('备份不能超过2MB。')
    try:
        return validate(json.loads(content.decode('utf-8-sig')))
    except (UnicodeError, json.JSONDecodeError, TypeError, KeyError, AttributeError, RecursionError):
        raise ValueError('备份格式不正确，请使用本工具导出的JSON文件。') from None

class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS spaces (space TEXT PRIMARY KEY, payload TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0)')

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=20)
        try:
            conn.execute('PRAGMA journal_mode=WAL')
            with conn:
                yield conn
        finally:
            conn.close()

    def read(self, key):
        with self.connect() as conn:
            row = conn.execute('SELECT payload FROM spaces WHERE space=?', (key,)).fetchone()
        return json.loads(row[0]) if row else empty()

    def mutate(self, key, function):
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT payload FROM spaces WHERE space=?', (key,)).fetchone()
            data = json.loads(row[0]) if row else empty()
            result = function(data)
            validate(data)
            payload = json.dumps(data, ensure_ascii=False)
            if len(payload.encode('utf-8')) > 2_000_000:
                raise ValueError('当前空间已超过2MB容量，请先导出备份，并整理为更小的家庭空间。')
            conn.execute('INSERT INTO spaces(space,payload,revision) VALUES(?,?,1) ON CONFLICT(space) DO UPDATE SET payload=excluded.payload,revision=spaces.revision+1', (key,payload))
        return result

    def restore(self, key, content):
        replacement = decode_backup(content)
        def action(data):
            data.clear();data.update(copy.deepcopy(replacement))
            event(data, '导入备份', note='用备份替换当前家庭数据。')
            # Old undo actions must not affect a restored state.
            for p in data['products']:
                for b in p['batches']:
                    b['revision'] += 1
        self.mutate(key, action)

    def export(self, key):
        return json.dumps(self.read(key), ensure_ascii=False, indent=2).encode('utf-8')

    def clear(self, key):
        with self.connect() as conn:
            conn.execute('DELETE FROM spaces WHERE space=?', (key,))

def load_demo(state):
    if state['products']:
        raise ValueError('示例仅可载入空的家庭空间，避免覆盖已有用品。')
    d = today()
    demos=[('一次性手套','M号','盒','防护用品','玄关柜第二层',2,1,120),
           ('护理垫','60×90cm','包','日常照护用品','卧室柜第二层',3,2,240),
           ('清洁湿巾','无香型','包','清洁用品','床头柜',2,4,18),
           ('纱布敷料','独立包装','包','其他','专用收纳盒',5,6,200),
           ('纸巾','抽取式','包','日常照护用品','储物柜',2,0,None),
           ('口罩','成人款','盒','防护用品','玄关柜第一层',1,2,-3),
           ('收纳袋','中号','个','其他','储物柜',2,8,None),
           ('擦拭巾','小号','包','清洁用品','浴室干燥柜',2,3,0)]
    for name,spec,unit,cat,loc,minimum,qty,offset in demos:
        add_product(state,name,spec,unit,cat,loc,minimum,'虚构展示记录，不是购买或使用建议。',qty,
                    (d+timedelta(days=offset)).isoformat() if offset is not None else None,'示例批次A')
    p=state['products'][1]
    add_batch(state,p['id'],1,(d+timedelta(days=60)).isoformat(),'示例批次B')
    event(state,'载入示例',note='所有记录均为虚构。')
