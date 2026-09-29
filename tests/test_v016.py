import tempfile,unittest
from datetime import datetime,timezone
import pandas as pd

from smart_money_tracker.app.core import Store,normalise
from smart_money_tracker.app.prices import PriceBridge,politician_score
from market_flow_bot.app.barstore import BarStore
from market_flow_bot.app.maxpain import max_pain_from_chain
from market_flow_bot.app.period import PeriodEngine
from market_flow_bot.app.signals import _stock_flow,pct_changes
from market_flow_bot.app.webserver import _parse_range


def row(source_id,ticker='AAPL',person='Test Person',action='Buy',trade='2026-09-01',disclosed='2026-09-20'):
    return normalise(dict(ticker=ticker,politician=person,chamber='House',action=action,
        transaction_date=trade,disclosure_date=disclosed,amount='$1,001 - $15,000'), 'test',source_id)


class V016Tests(unittest.TestCase):
    def test_disclosures_group_person_and_date(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(d+'/d.db');store.add_rows([row('1'),row('2','MSFT'),row('3',person='Other')])
            result=store.disclosures()
            self.assertEqual(result['disclosures_total'],2)
            group=next(g for g in result['disclosures'] if g['politician']=='Test Person')
            self.assertEqual({t['ticker'] for t in group['trades']},{'AAPL','MSFT'})
            self.assertEqual([p['name'] for p in store.people()],['Other','Test Person'])
            store.db.close()

    def test_score_direction_and_small_sample_shrink(self):
        trades=[{'action':'Buy','directional_return_pct':20},{'action':'Sell','directional_return_pct':10}]
        score=politician_score(trades)
        self.assertEqual(score['win_rate'],100.0)
        self.assertEqual(score['average_directional_return_pct'],15.0)
        self.assertGreater(score['score'],50)
        self.assertLess(score['score'],82.5)  # reliability shrink from the raw score

    def test_price_bridge_buy_and_sell_direction(self):
        bridge=PriceBridge('http://unused')
        bridge._fetch=lambda pairs:{p:{'estimated_price':100,'price_date':'2026-09-01','current_price':120,'current_asof':'now','pending':False} for p in pairs}
        enriched=bridge.enrich([row('b'),row('s',action='Sell')])
        self.assertEqual(enriched[0]['directional_return_pct'],20.0)
        self.assertEqual(enriched[1]['directional_return_pct'],-20.0)

    def test_trade_price_uses_requested_or_next_session(self):
        with tempfile.TemporaryDirectory() as d:
            store=BarStore(d+'/bars.db')
            daily=pd.DataFrame({'close':[101.0,103.0],'volume':[1,1]},index=pd.to_datetime(['2026-09-07T20:00:00Z','2026-09-08T20:00:00Z']))
            recent=pd.DataFrame({'close':[111.0],'volume':[1]},index=pd.to_datetime([datetime.now(timezone.utc).timestamp()-60],unit='s',utc=True))
            store.put_bars('1d','AAPL',daily);store.put_bars('5m','AAPL',recent)
            engine=PeriodEngine(lambda:[],[],None,store)
            got=engine.trade_prices([('AAPL','2026-09-06')])['prices']['AAPL|2026-09-06']
            self.assertEqual((got['estimated_price'],got['price_date'],got['current_price']),(101.0,'2026-09-07',111.0))
            store.conn.close()

    def test_bad_ohlc_and_negative_volume_cannot_break_flow_bounds(self):
        df=pd.DataFrame({'close':[100,110,105],'volume':[10,-20,10],'high':[101,105,106],'low':[99,100,104]})
        net,gross=_stock_flow(df,None)
        self.assertGreaterEqual(gross,0)
        self.assertLessEqual(abs(net),gross)

    def test_move_reference_uses_regular_close_and_open(self):
        idx=pd.to_datetime(['2026-09-28T19:55:00Z','2026-09-28T21:00:00Z','2026-09-29T12:00:00Z','2026-09-29T13:30:00Z','2026-09-29T14:00:00Z'])
        frame=pd.DataFrame({'close':[100,110,90,105,111]},index=idx)
        self.assertAlmostEqual(pct_changes({'A':frame},'prev_close')['A'],11.0)
        self.assertAlmostEqual(pct_changes({'A':frame},'session_open')['A'],111/105*100-100)

    def test_empty_open_interest_has_no_max_pain(self):
        self.assertIsNone(max_pain_from_chain([(100,0),(110,float('nan'))],[(100,0)]))

    def test_range_preserves_explicit_offset(self):
        start,end=_parse_range('2026-09-01T12:00:00+02:00','2026-09-01T13:00:00+02:00')
        self.assertEqual(end-start,3600)
        self.assertEqual(datetime.fromtimestamp(start,timezone.utc).hour,10)


if __name__=='__main__':unittest.main()
