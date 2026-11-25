"""家护物品管家 · Run with: streamlit run app.py"""
import csv
import html
import io
import json
import os
import sqlite3
from datetime import date
from pathlib import Path
import uuid
import streamlit as st
import inventory as inv

st.set_page_config(page_title='家护物品管家', page_icon='🌿', layout='centered', initial_sidebar_state='collapsed')
BASE = Path(__file__).resolve().parent
DATA_PATH = os.environ.get('HOMECARE_DB_PATH', str(BASE / 'data' / 'inventory.sqlite3'))
store = inv.Store(DATA_PATH)
CATEGORIES = ['日常照护用品', '清洁用品', '防护用品', '其他']

st.markdown('''<style>
.stApp{font-family:"Microsoft YaHei",system-ui,sans-serif}
.block-container{max-width:1050px;padding-top:4.5rem;padding-bottom:4rem}
h1{font-size:2.25rem!important;letter-spacing:-.04em;margin-bottom:.25rem!important}
h2{font-size:1.45rem!important}h3{font-size:1.15rem!important}
.stButton>button,.stDownloadButton>button{min-height:46px;border-radius:9px;font-size:16px}
.stTextInput input,.stNumberInput input,.stTextArea textarea{font-size:16px!important}
[data-testid="stMarkdownContainer"] p,[data-testid="stWidgetLabel"] p{font-size:16px}
div[role=radiogroup]{flex-wrap:wrap;gap:.5rem 1rem!important}
.intro{color:#526c6d;line-height:1.8;margin:0 0 1.25rem}
.kicker{color:#087b70;font-weight:700;font-size:14px;letter-spacing:.1em;margin-bottom:7px}
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:20px 0 26px}
.stat{background:#e3f1ec;border-radius:12px;padding:16px 18px}.stat b{font-size:28px;display:block;color:#075f55}.stat span{font-size:14px;color:#345c59}
.item-title{font-size:20px;font-weight:700;margin:0 0 4px}.muted{color:#597574;font-size:15px}
.badge{font-size:14px;display:inline-block;background:#e5f4ed;color:#0c6457;padding:3px 9px;border-radius:5px;margin-right:7px;margin-top:5px}
.badge.warn{background:#fff0d8;color:#87530c}.badge.danger{background:#fde5e2;color:#9c3129}
.stock{font-size:27px;font-weight:700;margin:12px 0 2px}.location{font-size:17px;color:#1d5f59}
.empty{padding:28px 6px;color:#526c6d;line-height:1.9}.foot{font-size:14px;color:#607978;border-top:1px solid #d8e7e2;padding-top:18px;margin-top:28px}
@media(max-width:600px){.block-container{padding:4.3rem 1rem 3rem}h1{font-size:1.8rem!important}.stats{gap:7px}.stat{padding:12px 10px}.stat b{font-size:24px}.stat span{font-size:13px}div[role=radiogroup] label p{font-size:14px!important}.stButton>button,.stDownloadButton>button{width:100%}}
</style>''', unsafe_allow_html=True)

def esc(s):
    return html.escape(str(s))

def flash(message):
    st.session_state['flash'] = message
    st.rerun()

def commit(fn, message):
    try:
        result = store.mutate(st.session_state['space'], fn)
    except (ValueError, sqlite3.Error, OSError) as e:
        st.error(str(e) if isinstance(e, ValueError) else '数据保存失败，请检查存储权限并稍后重试。')
        return None
    flash(message)
    return result

def expire_input(prefix, default=None):
    d = st.date_input('包装标注的有效期（未标注或暂未确认时留空）',
                      value=date.fromisoformat(default) if default else None,
                      min_value=date(1900,1,1), max_value=date(2200,12,31), key=prefix+'_date')
    st.caption('按包装填写日期。不猜测有效期；留空保存为“有效期待核对”。')
    return d.isoformat() if d else None

def active(data):
    return [p for p in data['products'] if not p['archived']]

def label_product(p):
    return p['name'] + (f' · {p["spec"]}' if p['spec'] else '') + f'（{p["unit"]}）'

def batch_label(b, unit):
    return f'{b["lot"] or "未标记批次"} · 剩余{b["qty"]}{unit} · {b["expiry"] or "有效期待核对"} · {b["id"][-4:]}'

def render_batch_list(p, days):
    ordered = sorted(p['batches'], key=lambda b:(b['expiry'] is None, b['expiry'] or '9999', b['created_at']))
    for b in ordered:
        state = inv.status(b, days)
        if b['qty'] == 0:
            continue
        st.write(f'**{b["lot"] or "未标记批次"}** · {b["qty"]}{p["unit"]}')
        st.caption(f'有效期：{b["expiry"] or "待核对"}　｜　{state}')

def csv_bytes(rows):
    output = io.StringIO(newline='')
    def safe(v):
        s = str(v)
        return "'"+s if s.lstrip().startswith(('=','+','-','@','\t','\r')) else s
    w = csv.writer(output)
    for row in rows:
        w.writerow([safe(v) for v in row])
    return ('\ufeff'+output.getvalue()).encode('utf-8')

def shopping_rows(data):
    rows = [['用品名称','规格','单位','未过期记录数量','用户设定最低库存','补足最低库存差额','存放位置']]
    for p in active(data):
        s = inv.summary(p, data['settings']['expiry_days'])
        if s['low']:
            rows.append([p['name'],p['spec'],p['unit'],s['current'],p['minimum'],s['need'],p['location']])
    return rows

def printable(data):
    rows = shopping_rows(data)
    head = ''.join('<th>'+esc(c)+'</th>' for c in rows[0])
    body = ''.join('<tr>'+''.join('<td>'+esc(c)+'</td>' for c in r)+'</tr>' for r in rows[1:])
    if not body:
        body='<tr><td colspan="7">暂无低于设定最低库存的用品。</td></tr>'
    due=[]
    days=data['settings']['expiry_days']
    for p in active(data):
        for b in p['batches']:
            if b['qty']>0 and inv.status(b,days)!='有效期内':
                due.append(f'<li>{esc(p["name"])} · {esc(b["lot"] or "未标记批次")} · {b["qty"]}{esc(p["unit"])} · {esc(b["expiry"] or "日期待核对")} · {inv.status(b,days)}</li>')
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>家护用品待处理清单</title><style>body{{font:18px/1.8 system-ui,sans-serif;max-width:1100px;margin:32px auto;padding:0 20px;color:#163c3d}}table{{border-collapse:collapse;width:100%;font-size:16px}}td,th{{border:1px solid #aac3bf;padding:10px;text-align:left}}th{{background:#e7f4ed}}.scroll{{overflow:auto}}button{{padding:12px 22px;background:#087b70;color:white;border:0;border-radius:6px;font-size:18px}}@media print{{button{{display:none}}body{{margin:0;font-size:14px}}td,th{{padding:7px}}}}</style><h1>家庭护理用品待处理清单</h1><p>整理日期：{inv.today().isoformat()} · 请按家庭实际情况核对。</p><button onclick="window.print()">打印 / 存为 PDF</button><h2>库存补充</h2><div class="scroll"><table><tr>{head}</tr>{body}</table></div><h2>有效期待处理</h2><ul>{''.join(due) or '<li>暂无待处理记录。</li>'}</ul><p>差额仅按用户设定的最低库存计算，不是医学用量或购买建议。未确认有效期的记录需自行核对。</p><p>辅助学习／科普用途，不替代专业医疗判断。</p></html>'''

st.title('家护物品管家')

if 'space' not in st.session_state:
    st.subheader('打开你的家庭空间')
    st.write('第一次使用时设置一个至少10个字符的家庭口令。以后在同一服务地址输入相同口令，即可找回记录。')
    with st.form('enter_space'):
        code=st.text_input('家庭口令', type='password', max_chars=200, help='请使用不易猜测的长口令。输入不同口令会打开不同空间；本版没有找回口令功能。')
        st.caption('持有相同口令的人可查看和修改同一空间。仅记录用品，不填写患者姓名、病历或证件。')
        enter=st.form_submit_button('打开家庭空间', type='primary', use_container_width=True)
    if enter:
        try:
            st.session_state['space']=inv.space_key(code)
            st.session_state['demo']=False
            st.rerun()
        except ValueError as e:
            st.error(str(e))
    st.write('')
    if st.button('先体验8种虚构用品', use_container_width=True):
        key=uuid.uuid4().hex
        store.mutate(key, inv.load_demo)
        st.session_state['space']=key
        st.session_state['demo']=True
        st.rerun()
    st.info('电脑本地运行时，记录写入该电脑的数据库，关闭再启动仍可恢复。云端演示请定期下载备份，免费托管磁盘不作为长期保存保证。')
    st.caption('本版为库存管理工具，未接入实时AI。不提供用品选择、治疗或用量建议。')
    st.stop()

key=st.session_state['space']
data=store.read(key)
products=active(data)
days=data['settings']['expiry_days']
if 'flash' in st.session_state:
    st.success(st.session_state.pop('flash'))
if st.session_state.get('demo'):
    st.info('当前是虚构示例空间，包含低库存、多批次和有效期待核对等情况。刷新或退出后可重新体验。')
with st.sidebar:
    st.subheader('家庭空间')
    st.write('示例空间' if st.session_state.get('demo') else '用品数据按家庭口令分开保存。')
    st.caption('提醒在打开页面时更新，不提供网页关闭后的定时推送。')
    if st.button('退出家庭空间', use_container_width=True):
        st.session_state.clear();st.rerun()

nav=st.radio('选择功能', ['用品总览','新增用品','使用与补充','待处理清单','备份与设置'], horizontal=True, key='nav', label_visibility='collapsed')
st.divider()

if nav=='用品总览':
    low=sum(inv.summary(p,days)['low'] for p in products)
    due=sum(inv.summary(p,days)['attention'] for p in products)
    st.markdown(f'<div class="stats"><div class="stat"><b>{len(products)}</b><span>在管用品种类</span></div><div class="stat"><b>{low}</b><span>低于设定库存</span></div><div class="stat"><b>{due}</b><span>有效期待核对</span></div></div>', unsafe_allow_html=True)
    st.subheader('用品在哪里，一查就知道')
    query=st.text_input('搜索名称、规格或存放位置', placeholder='例如：护理垫、床头柜', key='search')
    f1,f2=st.columns(2)
    cat=f1.selectbox('用品分类',['全部分类']+sorted(set(p['category'] for p in products)), key='filter_cat')
    mode=f2.selectbox('显示范围',['全部用品','低库存','有效期待处理'], key='filter_mode')
    visible=[]
    for p in products:
        s=inv.summary(p,days)
        if query.casefold() not in (p['name']+' '+p['spec']+' '+p['location']).casefold():continue
        if cat!='全部分类' and p['category']!=cat:continue
        if mode=='低库存' and not s['low']:continue
        if mode=='有效期待处理' and not s['attention']:continue
        visible.append(p)
    if not products:
        st.info('还没有用品。请切换到“新增用品”，或在“备份与设置”载入虚构示例。不同口令会打开不同空间，找不到旧记录时请核对口令和服务地址。')
    elif not visible:
        st.info('没有符合条件的用品，试试其他关键词或筛选条件。')
    for p in visible:
        s=inv.summary(p,days)
        with st.container(border=True):
            st.markdown(f'<div class="item-title">{esc(p["name"])}</div><div class="muted">{esc(p["spec"] or "未填规格")} · 计数单位：{esc(p["unit"])}</div>', unsafe_allow_html=True)
            tags=f'<span class="badge">{esc(p["category"])}</span>'
            if s['low']:tags+='<span class="badge warn">低于设定库存</span>'
            if s['expired']:tags+='<span class="badge danger">含过期记录</span>'
            st.markdown(tags, unsafe_allow_html=True)
            st.markdown(f'<div class="stock">{s["current"]} <small>{esc(p["unit"])}</small></div><div class="location">存放：{esc(p["location"])}</div>', unsafe_allow_html=True)
            st.caption(f'未过期记录数量（含有效期待核对批次） · 设定最低库存：{p["minimum"]}{p["unit"]} · 含过期的账面总数：{s["total"]}{p["unit"]}')
            with st.expander('查看批次与备注'):
                render_batch_list(p,days)
                if p['note']:st.write('备注：'+p['note'])
                if not any(b['qty'] for b in p['batches']):st.caption('所有批次数量均为0。')
            with st.expander('编辑资料或归档'):
                with st.form('edit_'+p['id']):
                    name=st.text_input('用品名称', p['name'], max_chars=80)
                    spec=st.text_input('规格', p['spec'], max_chars=120)
                    editunit=st.text_input('计数单位',p['unit'],max_chars=12,help='只填写“盒、包、个”等，不要把数量填入单位，例如填写“盒”而不是“5盒”。')
                    loc=st.text_input('存放位置', p['location'], max_chars=120)
                    minimum=st.number_input('用户设定最低库存',0,inv.MAX_QTY,p['minimum'],step=1)
                    categories=list(dict.fromkeys(CATEGORIES+[p['category']]))
                    category=st.selectbox('分类',categories,index=categories.index(p['category']))
                    note=st.text_area('备注',p['note'],max_chars=1000)
                    unitack=st.checkbox('如修改单位：我已核对批次数量和最低库存，知道系统不会自动进行盒、包、片等数量换算。')
                    if st.form_submit_button('保存资料',type='primary'):
                        if editunit.strip()!=p['unit'] and not unitack:
                            st.error('修改计数单位前，请勾选数量核对确认。修改后各批次数量不会自动换算。')
                        else:
                            commit(lambda d:inv.edit_product(d,p['id'],name,spec,loc,int(minimum),category,note,unit=editunit),'资料已保存。修改计数单位不会自动换算库存数量。')
                ack=st.checkbox('确认将此用品移入归档（可恢复）',key='archive_ack_'+p['id'])
                if st.button('归档这件用品',key='archive_'+p['id'],disabled=not ack):
                    commit(lambda d:inv.archive(d,p['id']),'用品已归档，可在设置中恢复。')

elif nav=='新增用品':
    st.subheader('把一件用品登记清楚')
    st.caption('仅录入你已经确定需要管理的用品；同名同规格同单位用品的不同有效期，请在“使用与补充”新增批次。')
    with st.form('new_product',clear_on_submit=False):
        name=st.text_input('用品名称 *',max_chars=80,placeholder='按包装填写')
        spec=st.text_input('规格',max_chars=120,placeholder='例如尺寸、型号；不确定可留空')
        c1,c2=st.columns(2)
        unit=c1.text_input('计数单位 *',max_chars=12,placeholder='只填包 / 盒 / 个，不填数量')
        category=c2.selectbox('分类',CATEGORIES)
        location=st.text_input('存放位置 *',max_chars=120,placeholder='例如：卧室柜子第二层')
        c1,c2=st.columns(2)
        qty=c1.number_input('初始数量',min_value=0,max_value=inv.MAX_QTY,value=0,step=1)
        minimum=c2.number_input('最低库存（自行设定）',min_value=0,max_value=inv.MAX_QTY,value=0,step=1)
        expiry=expire_input('new_expiry')
        lot=st.text_input('批次标记',max_chars=80,placeholder='例如：11月购入 / 包装批号')
        note=st.text_area('备注',max_chars=1000,placeholder='可记录已交代的事项，不填写患者隐私。')
        submitted=st.form_submit_button('保存新品与首个批次',type='primary',use_container_width=True)
    if submitted:
        commit(lambda d:inv.add_product(d,name,spec,unit,category,location,int(minimum),note,int(qty),expiry,lot),'新品和首个批次已保存，请到用品总览查看。')

elif nav=='使用与补充':
    st.subheader('每次变动，都有记录')
    if not products:
        st.info('请先在“新增用品”登记用品。')
    else:
        mapping={p['id']:p for p in products}
        pid=st.selectbox('选择用品',list(mapping),format_func=lambda v:label_product(mapping[v]),key='op_product')
        p=mapping[pid]
        action=st.radio('操作类型',['记录使用','补充入库','盘点校正','清理出库','编辑批次'],horizontal=True,key='op_action')
        if action=='补充入库':
            with st.form('restock_'+pid):
                st.caption('每次补充建立独立批次，不与旧批次混合。计数单位沿用当前用品。')
                qty=st.number_input(f'新增数量（{p["unit"]}）',1,inv.MAX_QTY,1,step=1)
                expiry=expire_input('restock_'+pid)
                lot=st.text_input('批次标记',max_chars=80)
                if st.form_submit_button('确认补充入库',type='primary',use_container_width=True):
                    commit(lambda d:inv.add_batch(d,pid,int(qty),expiry,lot),'新批次已入库，原有批次保持独立。')
        else:
            ordered=sorted(p['batches'],key=lambda b:(b['expiry'] is None,b['expiry'] or '9999',b['created_at']))
            bm={b['id']:b for b in ordered}
            bid=st.selectbox('选择实际变动的批次',list(bm),format_func=lambda v:batch_label(bm[v],p['unit']),key='batch_'+pid)
            b=bm[bid]
            st.write(f'当前批次库存：**{b["qty"]}{p["unit"]}** · {inv.status(b,days)}')
            if action=='编辑批次':
                with st.form('batch_edit_'+bid):
                    expiry=expire_input('be_'+bid,b['expiry'])
                    lot=st.text_input('批次标记',value=b['lot'],max_chars=80)
                    if st.form_submit_button('保存批次信息',type='primary'):
                        commit(lambda d:inv.edit_batch(d,pid,bid,expiry,lot),'批次信息已更新，数量未改变。')
            else:
                with st.form('stock_'+bid+'_'+action):
                    qty=st.number_input(('盘点后的实际数量' if action=='盘点校正' else '本次数量')+f'（{p["unit"]}）',min_value=0 if action=='盘点校正' else 1,max_value=inv.MAX_QTY,value=b['qty'] if action=='盘点校正' else 1,step=1)
                    note=st.text_input('原因或备注（盘点校正必填）',max_chars=500)
                    if st.form_submit_button('确认'+action,type='primary',use_container_width=True):
                        commit(lambda d:inv.change_stock(d,pid,bid,int(qty),action,note),'库存变动已保存。误操作可在下方操作记录中尝试撤销。')
    st.divider();st.subheader('最近操作')
    events=list(reversed(data['events']))[:30]
    if events:
        for e in events[:10]:
            change='' if e['before'] is None else f' · {e["before"]} → {e["after"]}{e["unit"]}'
            st.write(f'**{e["kind"]}** · {e["product_name"]}{change}'+('（已撤销）' if e['undone'] else ''))
            st.caption(e['at'].replace('T',' ')[:19]+(' · '+e['note'] if e['note'] else ''))
        options=[e for e in events if not e['undone'] and e['kind'] in ['记录使用','清理出库','盘点校正','补充入库']]
        if options:
            em={e['id']:e for e in options}
            eid=st.selectbox('选择需要撤销的库存变动',list(em),format_func=lambda v:em[v]['at'][5:19].replace('T',' ')+' '+em[v]['product_name']+' '+em[v]['kind'])
            st.caption('仅当该批次后续没有发生其他变动时可撤销；否则请进行盘点校正。')
            if st.button('撤销所选变动'):
                commit(lambda d:inv.undo_stock(d,eid),'变动已撤销，库存已恢复。')
    else:
        st.caption('暂无操作记录。')

elif nav=='待处理清单':
    st.subheader('出门补充前，先看这张单')
    st.caption('按你设定的最低库存计算。已过期批次不计入未过期数量；未确认有效期的批次单独提醒，仍需人工核对。')
    rows=shopping_rows(data)
    if len(rows)==1:
        st.success('目前没有低于设定最低库存的用品。')
    for r in rows[1:]:
        with st.container(border=True):
            st.write('**'+r[0]+'**'+(' · '+r[1] if r[1] else ''))
            st.write(f'未过期记录数量 {r[3]}{r[2]} · 最低库存 {r[4]}{r[2]}')
            st.write(f'补足到设定最低库存，差额为 **{r[5]}{r[2]}**')
            st.caption('存放位置：'+r[6])
    st.download_button('下载补充清单 CSV',csv_bytes(rows),'家庭用品补充清单.csv','text/csv',use_container_width=True)
    st.download_button('下载可打印清单',printable(data).encode('utf-8'),'家庭用品待处理清单.html','text/html',use_container_width=True)
    st.caption('打开下载的HTML文件，点击“打印 / 存为 PDF”。电脑与手机的打印入口可能不同。')
    st.divider();st.subheader('有效期待处理')
    any_due=False
    for p in products:
        for b in p['batches']:
            status=inv.status(b,days)
            if b['qty']>0 and status!='有效期内':
                any_due=True
                with st.container(border=True):
                    st.write(f'**{p["name"]} · {status}**')
                    st.write(f'{b["lot"] or "未标记批次"} · {b["qty"]}{p["unit"]} · {b["expiry"] or "有效期待核对"}')
                    st.caption('存放：'+p['location'])
    if not any_due:st.caption('暂无有效期待处理记录。')

else:
    st.subheader('保存一份备份，换设备也方便')
    st.write('日常变动已自动写入运行本应用的服务器数据库；不是仅存在网页会话里。下载JSON备份后，可在另一部署地址或另一个家庭空间导入。')
    st.warning('Streamlit免费云端的本地磁盘可能因重启、重建等丢失，不保证永久保存。重要记录请定期下载JSON备份。本地电脑版的数据保存在项目的data文件夹。')
    st.download_button('下载完整 JSON 备份',store.export(key),f'家护用品备份_{inv.today().isoformat()}.json','application/json',type='primary',use_container_width=True)
    st.caption('备份包含所有用品、批次、设置、归档和最近500条操作历史，不含家庭口令。')
    with st.form('import_backup'):
        upload=st.file_uploader('导入本工具的 JSON 备份（不超过2MB）',type=['json'])
        confirmed=st.checkbox('我已备份当前记录，确认用导入文件替换当前家庭空间的全部记录。')
        restore=st.form_submit_button('验证并恢复备份',use_container_width=True)
    if restore:
        if not upload:st.error('请先选择JSON备份文件。')
        elif not confirmed:st.error('请先确认替换当前记录。')
        else:
            try:
                store.restore(key,upload.getvalue())
                flash('备份已恢复，请核对用品与批次。')
            except (ValueError,sqlite3.Error,OSError) as e:
                st.error(str(e) if isinstance(e,ValueError) else '恢复失败，当前数据未被修改。')
    st.divider();st.subheader('提醒设置')
    with st.form('settings'):
        nd=st.number_input('提前多少天列入“即将到期”',0,365,days,step=1)
        if st.form_submit_button('保存提醒设置'):
            commit(lambda d:d['settings'].update(expiry_days=int(nd)),'提醒天数已保存。')
    st.caption('只有包装明确标注的日期才用于计算；日期为当天单列“今日到期”。提醒仅在打开应用时刷新。')
    archived=[p for p in data['products'] if p['archived']]
    with st.expander(f'已归档用品（{len(archived)}种，可恢复）'):
        for p in archived:
            st.write(label_product(p))
            if st.button('恢复用品',key='restore_'+p['id']):
                commit(lambda d:inv.archive(d,p['id'],False),'用品已恢复。')
        if not archived:st.caption('暂无归档用品。')
    with st.expander('虚构示例与清空'):
        st.caption('示例是演示数据，不是建议购买的用品清单。载入示例仅限空空间。')
        if st.button('载入8种虚构示例',disabled=bool(data['products'])):
            commit(inv.load_demo,'已载入虚构示例。')
        clearack=st.checkbox('确认删除当前家庭空间的全部记录（无法撤销，请先下载备份）。')
        if st.button('清空当前家庭空间',disabled=not clearack):
            store.clear(key);flash('当前家庭空间已清空。其他空间未受影响。')

st.markdown('<div class="foot">辅助学习／科普用途，不替代专业医疗判断。</div>',unsafe_allow_html=True)
