import json,tempfile,unittest
from unittest.mock import patch
from smart_money_tracker.app.core import Store
from smart_money_tracker.app.sync import Collector
class SenateTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.store=Store(self.tmp.name+'/db')
 def tearDown(self):self.store.db.close();self.tmp.cleanup()
 def test_missing_key_explicit(self):
  Collector(self.store,{}).senate();self.assertEqual(self.store.status()['senate']['state'],'not_configured')
 def test_fmp_mapping_and_repeated_sync(self):
  data=[{'symbol':'NVDA','firstName':'Example','lastName':'Senator','type':'Purchase','owner':'Spouse','transactionDate':'2026-08-01','disclosureDate':'2026-09-01','amount':'$1,001 - $15,000','link':'https://example.org/source','assetType':'Stock'}]
  c=Collector(self.store,{'fmp_api_key':'test-secret'})
  with patch('smart_money_tracker.app.sync.download',return_value=json.dumps(data).encode()):c.senate(True);c.senate(True)
  r=self.store.search()['trades'];self.assertEqual(len(r),1);self.assertEqual((r[0]['politician'],r[0]['action'],r[0]['chamber']),('Example Senator','Buy','Senate'))
 def test_provider_error_not_empty_success(self):
  with patch('smart_money_tracker.app.sync.download',return_value=b'{"Error Message":"denied"}'):
   Collector(self.store,{'fmp_api_key':'secret-key'}).senate(True)
  s=self.store.status()['senate'];self.assertEqual(s['state'],'error');self.assertNotIn('secret-key',json.dumps(s))
