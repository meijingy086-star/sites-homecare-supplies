import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest
import inventory as inv

class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'test.sqlite3'
        self.store=inv.Store(self.path)
        self.key='test-space'
        self.pid=self.store.mutate(self.key,lambda d:inv.add_product(d,'护理垫','规格A','包','其他','卧室',3,'',3,(inv.today()+timedelta(days=100)).isoformat(),'旧批次'))
        self.bid=self.store.read(self.key)['products'][0]['batches'][0]['id']
    def tearDown(self):
        self.temp.cleanup()
    def data(self):return self.store.read(self.key)
    def stock(self):return self.data()['products'][0]['batches'][0]['qty']
    def test_overdraw_rolls_back(self):
        before=self.store.export(self.key)
        with self.assertRaises(ValueError):self.store.mutate(self.key,lambda d:inv.change_stock(d,self.pid,self.bid,4,'记录使用'))
        self.assertEqual(self.store.export(self.key),before)
    def test_use_and_undo(self):
        self.store.mutate(self.key,lambda d:inv.change_stock(d,self.pid,self.bid,2,'记录使用'))
        self.assertEqual(self.stock(),1)
        eid=self.data()['events'][-1]['id']
        self.store.mutate(self.key,lambda d:inv.undo_stock(d,eid))
        self.assertEqual(self.stock(),3)
        with self.assertRaises(ValueError):self.store.mutate(self.key,lambda d:inv.undo_stock(d,eid))
    def test_undo_rejects_newer_change(self):
        self.store.mutate(self.key,lambda d:inv.change_stock(d,self.pid,self.bid,1,'记录使用'))
        eid=self.data()['events'][-1]['id']
        self.store.mutate(self.key,lambda d:inv.change_stock(d,self.pid,self.bid,1,'记录使用'))
        with self.assertRaises(ValueError):self.store.mutate(self.key,lambda d:inv.undo_stock(d,eid))
    def test_batches_not_mixed(self):
        expiry=(inv.today()+timedelta(days=200)).isoformat()
        self.store.mutate(self.key,lambda d:inv.add_batch(d,self.pid,6,expiry,'新批次'))
        p=self.data()['products'][0]
        self.assertEqual([b['qty'] for b in p['batches']],[3,6])
        self.assertEqual(p['batches'][1]['expiry'],expiry)
        self.assertNotEqual(p['batches'][0]['expiry'],expiry)
    def test_expired_stock_excluded(self):
        self.store.mutate(self.key,lambda d:inv.add_batch(d,self.pid,99,(inv.today()-timedelta(days=1)).isoformat(),'过期'))
        s=inv.summary(self.data()['products'][0])
        self.assertEqual(s['current'],3);self.assertEqual(s['total'],102)
    def test_expired_use_rejected_and_discard_works(self):
        self.store.mutate(self.key,lambda d:inv.edit_batch(d,self.pid,self.bid,(inv.today()-timedelta(days=1)).isoformat(),''))
        with self.assertRaises(ValueError):self.store.mutate(self.key,lambda d:inv.change_stock(d,self.pid,self.bid,1,'记录使用'))
        self.store.mutate(self.key,lambda d:inv.change_stock(d,self.pid,self.bid,3,'清理出库'))
        self.assertEqual(self.stock(),0)
    def test_expiry_boundaries(self):
        b={'expiry':inv.today().isoformat()}
        self.assertEqual(inv.status(b,30),'今日到期')
        self.assertEqual(inv.status({'expiry':None},30),'有效期待核对')
        self.assertEqual(inv.status({'expiry':(inv.today()+timedelta(days=30)).isoformat()},30),'即将到期')
        self.assertEqual(inv.status({'expiry':(inv.today()+timedelta(days=31)).isoformat()},30),'有效期内')
    def test_persistence_and_isolation(self):
        self.assertEqual(inv.Store(self.path).read(self.key),self.data())
        self.assertEqual(self.store.read('different-space')['products'],[])
    def test_import_export_and_invalid_import_rollback(self):
        backup=self.store.export(self.key)
        self.store.restore('another',backup)
        self.assertEqual(self.store.read('another')['products'][0]['name'],'护理垫')
        before=self.store.export(self.key)
        for bad in [b'no json',b'[]',b'{"schema":1}',b'0'*2_000_001]:
            with self.assertRaises(ValueError):self.store.restore(self.key,bad)
            self.assertEqual(self.store.export(self.key),before)
        data=json.loads(backup);data['products'][0]['batches'][0]['qty']=-1
        with self.assertRaises(ValueError):self.store.restore(self.key,json.dumps(data).encode())
        self.assertEqual(self.store.export(self.key),before)
    def test_duplicate_and_negative_validation(self):
        with self.assertRaises(ValueError):self.store.mutate(self.key,lambda d:inv.add_product(d,'护理垫','规格A','包','其他','卧室',0,'',1,None))
        with self.assertRaises(ValueError):self.store.mutate(self.key,lambda d:inv.add_batch(d,self.pid,-1,None))
    def test_archive_restore(self):
        self.store.mutate(self.key,lambda d:inv.archive(d,self.pid))
        self.assertTrue(self.data()['products'][0]['archived'])
        with self.assertRaises(ValueError):self.store.mutate(self.key,lambda d:inv.change_stock(d,self.pid,self.bid,1,'记录使用'))
        self.store.mutate(self.key,lambda d:inv.archive(d,self.pid,False))
        self.assertFalse(self.data()['products'][0]['archived'])
    def test_stocktake_requires_reason(self):
        with self.assertRaises(ValueError):self.store.mutate(self.key,lambda d:inv.change_stock(d,self.pid,self.bid,0,'盘点校正'))
        self.store.mutate(self.key,lambda d:inv.change_stock(d,self.pid,self.bid,0,'盘点校正','重新盘点'))
        self.assertEqual(self.stock(),0)
    def test_concurrent_use_never_goes_negative(self):
        def use_one(_):
            try:self.store.mutate(self.key,lambda d:inv.change_stock(d,self.pid,self.bid,1,'记录使用'));return True
            except ValueError:return False
        with ThreadPoolExecutor(max_workers=5) as pool:results=list(pool.map(use_one,range(8)))
        self.assertEqual(sum(results),3);self.assertEqual(self.stock(),0)
    def test_demo_has_eight_products_nine_batches(self):
        self.store.mutate('demo',inv.load_demo)
        d=self.store.read('demo')
        self.assertEqual(len(d['products']),8)
        self.assertEqual(sum(len(p['batches']) for p in d['products']),9)
        inv.validate(d)
    def test_quantities_reject_boolean_and_decimal(self):
        for bad in [True,1.5,'3']:
            with self.assertRaises(ValueError):inv.integer(bad,'数量')
    def test_backup_duplicate_ids_rejected(self):
        d=self.data();d['products'].append(copy.deepcopy(d['products'][0]))
        with self.assertRaises(ValueError):inv.validate(d)
    def test_edit_unit_preserves_batch_quantity_and_history(self):
        before=self.data();old_event=before['events'][0]
        self.store.mutate(self.key,lambda d:inv.edit_product(d,self.pid,'护理垫','规格A','卧室',3,'其他','',unit='盒'))
        after=self.data();p=after['products'][0]
        self.assertEqual(p['unit'],'盒');self.assertEqual(p['batches'][0]['qty'],3)
        self.assertEqual(p['minimum'],3);self.assertEqual(after['events'][0]['unit'],old_event['unit'])
        self.assertIn('未自动换算',after['events'][-1]['note'])
    def test_unit_rejects_number(self):
        before=self.store.export(self.key)
        with self.assertRaises(ValueError):self.store.mutate(self.key,lambda d:inv.edit_product(d,self.pid,'护理垫','规格A','卧室',3,'其他','',unit='5盒'))
        self.assertEqual(self.store.export(self.key),before)

if __name__=='__main__':unittest.main(verbosity=2)
