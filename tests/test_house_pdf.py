import unittest
from pathlib import Path
from smart_money_tracker.app.house import parse_pdf
class RealPDFTests(unittest.TestCase):
 def report(self,doc,name):
  return dict(id='2026:'+doc,politician=name,disclosure_date='2026-09-22',source_url=f'https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/{doc}.pdf')
 def test_merged_metadata_does_not_contaminate_transactions(self):
  rows,skipped=parse_pdf(Path(__file__).with_name('fixtures').joinpath('house-20035492.pdf').read_bytes(),self.report('20035492','Richard W. Allen'))
  self.assertEqual(skipped,0)
  self.assertEqual([(r['ticker'],r['action'],r['amount_min'],r['amount_max']) for r in rows],[('AVGO','Buy',1001,15000),('ROL','Sell',15001,50000),('TSM','Buy',1001,15000)])
  self.assertTrue(all(r['owner']=='Spouse' and r['transaction_date']=='2026-08-12' for r in rows))
 def test_same_day_trade_and_disclosure(self):
  rows,skipped=parse_pdf(Path(__file__).with_name('fixtures').joinpath('house-20035499.pdf').read_bytes(),self.report('20035499','Pete Sessions'))
  self.assertEqual(len(rows),1);self.assertEqual(rows[0]['action'],'Sell');self.assertEqual(rows[0]['ticker'],'AAPL');self.assertEqual(rows[0]['disclosure_delay_days'],0)
