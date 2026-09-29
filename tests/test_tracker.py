import io,json,tempfile,threading,unittest,urllib.request,urllib.error,zipfile
from pathlib import Path
from http.server import ThreadingHTTPServer
from smart_money_tracker.app.core import normalise,amount_range,import_csv,Store
from smart_money_tracker.app.house import parse_index,parse_pdf
from smart_money_tracker.app.main import make_handler
from market_flow_bot.app.politicians import get_trades

ROW=dict(ticker='BRK.B',politician='Test Person',chamber='House',action='S (partial)',
 transaction_date='09/01/2026',disclosure_date='2026-09-20',amount='$15,001 - $50,000',source_url='https://example.org/filing',owner='SP')

class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.store=Store(self.tmp.name+'/test.db')
 def tearDown(self):self.store.db.close();self.tmp.cleanup()
 def test_normalise(self):
  r=normalise(ROW,'test','1');self.assertEqual((r['ticker'],r['action'],r['owner'],r['disclosure_delay_days']),('BRK-B','Sell','Spouse',19))
  self.assertEqual((r['amount_min'],r['amount_max']),(15001,50000))
 def test_unknown_owner(self):self.assertEqual(normalise({**ROW,'owner':''},'test','1')['owner'],'Not stated')
 def test_bad_dates_rejected(self):
  with self.assertRaises(ValueError):normalise({**ROW,'transaction_date':'2026-10-01'},'test','1')
 def test_ticker_not_guessed(self):
  with self.assertRaises(ValueError):normalise({**ROW,'ticker':'','asset':'Apple Inc'},'test','1')
 def test_open_amount(self):self.assertEqual(amount_range('Over $50,000,000')[1:],(50000000,None))
 def test_unknown_amount(self):self.assertEqual(amount_range('unknown')[1:],(None,None))
 def test_unsafe_link(self):self.assertEqual(normalise({**ROW,'source_url':'javascript:alert(1)'},'test','1')['source_url'],'')
 def test_duplicate_upsert(self):
  r=normalise(ROW,'test','1');self.store.add_rows([r,r]);self.assertEqual(self.store.search()['total'],1)
 def test_same_report_legitimate_repeats_preserved(self):
  self.store.add_rows([normalise(ROW,'test','1'),normalise(ROW,'test','2')]);self.assertEqual(self.store.search()['total'],2)
 def test_filter_and_pagination(self):
  self.store.add_rows([normalise(ROW,'test',str(i)) for i in range(3)])
  r=self.store.search('BRK.B',person='person',action='Sell',limit=2,offset=2)
  self.assertEqual(r['total'],3);self.assertEqual(len(r['trades']),1)
 def test_sql_input(self):self.assertEqual(self.store.search(person="' OR 1=1 --")['total'],0)
 def test_csv_atomic_validation(self):
  text='ticker,politician,chamber,action,transaction_date,disclosure_date,amount,source_url\nAAPL,Test,House,Buy,2026-01-01,2026-01-02,"$1,001 - $15,000",https://example.org\nINVALID!,Test,House,Buy,2026-01-01,2026-01-02,,\n'
  with self.assertRaises(ValueError):import_csv(text)
  self.assertEqual(self.store.search()['total'],0)
 def test_index_filters(self):
  buf=io.BytesIO()
  with zipfile.ZipFile(buf,'w') as z:z.writestr('2026FD.txt','First\tLast\tFilingType\tDocID\tFilingDate\tStateDst\nTest\tPerson\tP\t123\t9/1/2026\tCA12\nTest\tPerson\tO\t456\t9/1/2026\tCA12\n')
  r=parse_index(buf.getvalue(),2026,'2026-01-01');self.assertEqual(len(r),1);self.assertEqual(r[0]['id'],'2026:123')
 def test_report_retry_and_replacement(self):
  self.store.register([{'id':'2026:1','disclosure_date':'2026-09-01'}]);self.assertEqual(len(self.store.due(10)),1)
  self.store.save_report('2026:1',[normalise(ROW,'house_official','1')],0,'abc');self.assertEqual(len(self.store.due(10)),0)
  self.store.save_report('2026:1',[normalise({**ROW,'amount':'$1,001 - $15,000'},'house_official','1')],0,'xyz')
  self.assertEqual(self.store.search()['total'],1);self.assertEqual(self.store.search()['trades'][0]['amount_min'],1001)
 def test_api_bridge(self):
  class Collector:
   def request(self):pass
  self.store.add_rows([normalise(ROW,'test','1')])
  srv=ThreadingHTTPServer(('127.0.0.1',0),make_handler(self.store,Collector()))
  thread=threading.Thread(target=srv.serve_forever,daemon=True);thread.start()
  base=f'http://127.0.0.1:{srv.server_port}'
  try:
   result=get_trades(base,'BRK.B');self.assertTrue(result['available']);self.assertEqual(result['total'],1)
   req=urllib.request.Request(base+'/api/refresh',data=b'',method='POST')
   with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(req)
   self.assertEqual(e.exception.code,403)
   req=urllib.request.Request(base+'/api/refresh',data=b'',headers={'X-Tracker-Request':'1'},method='POST')
   self.assertEqual(urllib.request.urlopen(req).status,202)
  finally:srv.shutdown();srv.server_close();thread.join()
 def test_missing_service(self):self.assertFalse(get_trades('http://127.0.0.1:1','AAPL')['available'])

if __name__=='__main__':unittest.main()
