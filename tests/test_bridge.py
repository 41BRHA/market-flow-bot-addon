import unittest
from unittest.mock import patch

from market_flow_bot.app import politicians
from smart_money_tracker.app.prices import PriceBridge


class BridgeTests(unittest.TestCase):
    def setUp(self):
        politicians._cache.clear()
        politicians._good = None

    def test_cached_enrichment_never_calls_market_flow(self):
        bridge=PriceBridge('http://market-flow')
        trade={'ticker':'AAPL','transaction_date':'2026-01-02','action':'Buy'}
        with patch.object(bridge,'_fetch',side_effect=AssertionError('network called')):
            row=bridge.enrich_cached([trade])[0]
        self.assertTrue(row['price_pending'])

    def test_last_known_host_is_first(self):
        politicians._good='http://working:8098'
        self.assertEqual(politicians._candidates('http://wrong:8098')[0],'http://working:8098')

    def test_temporary_failure_keeps_last_success(self):
        politicians._cache['AAPL']=(0,{'available':True,'trades':[{'ticker':'AAPL'}]})
        with patch.object(politicians,'_try',side_effect=TimeoutError):
            result=politicians.get_trades('http://wrong:8098','AAPL')
        self.assertTrue(result['available'])
        self.assertTrue(result['stale_bridge'])

    def test_market_bridge_marker_is_sent(self):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self,_limit):return b'{"trades":[]}'
        with patch('urllib.request.urlopen',return_value=Response()) as opened:
            politicians._try('http://tracker:8098','AAPL')
        self.assertIn('market_bridge=1',opened.call_args.args[0])


if __name__=='__main__':unittest.main()
