"""Streamlit integration smoke test using an isolated temporary database."""
import os
from pathlib import Path
import tempfile
import unittest
from streamlit.testing.v1 import AppTest
import inventory as inv

def widget_by_label(widgets,label):
    return next(w for w in widgets if w.label==label)

class AppTests(unittest.TestCase):
    def test_login_inventory_forms_and_persistence(self):
        with tempfile.TemporaryDirectory() as td:
            previous=os.environ.get('HOMECARE_DB_PATH')
            os.environ['HOMECARE_DB_PATH']=str(Path(td)/'ui.sqlite3')
            try:
                at=AppTest.from_file(str(Path(__file__).with_name('app.py')),default_timeout=20).run()
                self.assertFalse(at.exception)
                at.text_input[0].set_value('homecare-test-passphrase')
                widget_by_label(at.button,'打开家庭空间').click().run()
                self.assertFalse(at.exception)
                at.radio(key='nav').set_value('新增用品').run()
                self.assertFalse(at.exception)
                for label,value in [('用品名称 *','测试用品'),('规格','小号'),('计数单位 *','包'),('存放位置 *','床头柜')]:
                    widget_by_label(at.text_input,label).set_value(value)
                widget_by_label(at.number_input,'初始数量').set_value(3)
                widget_by_label(at.number_input,'最低库存（自行设定）').set_value(2)
                widget_by_label(at.button,'保存新品与首个批次').click().run()
                self.assertFalse(at.exception)
                store=inv.Store(os.environ['HOMECARE_DB_PATH']);key=inv.space_key('homecare-test-passphrase')
                self.assertEqual(store.read(key)['products'][0]['batches'][0]['qty'],3)
                at.radio(key='nav').set_value('使用与补充').run()
                widget_by_label(at.number_input,'本次数量（包）').set_value(4)
                widget_by_label(at.button,'确认记录使用').click().run()
                self.assertFalse(at.exception)
                self.assertTrue(any('不能扣减' in e.value for e in at.error))
                widget_by_label(at.number_input,'本次数量（包）').set_value(2)
                widget_by_label(at.button,'确认记录使用').click().run()
                self.assertFalse(at.exception)
                self.assertEqual(store.read(key)['products'][0]['batches'][0]['qty'],1)
                for page in ['用品总览','待处理清单','备份与设置']:
                    at.radio(key='nav').set_value(page).run();self.assertFalse(at.exception,page)
                fresh=AppTest.from_file(str(Path(__file__).with_name('app.py')),default_timeout=20).run()
                fresh.text_input[0].set_value('homecare-test-passphrase')
                widget_by_label(fresh.button,'打开家庭空间').click().run()
                self.assertFalse(fresh.exception)
                self.assertTrue(any('测试用品' in e.value for e in fresh.markdown))
                widget_by_label(fresh.text_input,'计数单位').set_value('盒')
                widget_by_label(fresh.button,'保存资料').click().run()
                self.assertTrue(any('请勾选' in e.value for e in fresh.error))
                widget_by_label(fresh.checkbox,'如修改单位：我已核对批次数量和最低库存，知道系统不会自动进行盒、包、片等数量换算。').check()
                widget_by_label(fresh.button,'保存资料').click().run()
                self.assertFalse(fresh.exception)
                self.assertEqual(store.read(key)['products'][0]['unit'],'盒')
                self.assertEqual(store.read(key)['products'][0]['batches'][0]['qty'],1)
            finally:
                if previous is None:os.environ.pop('HOMECARE_DB_PATH',None)
                else:os.environ['HOMECARE_DB_PATH']=previous

if __name__=='__main__':unittest.main(verbosity=2)
