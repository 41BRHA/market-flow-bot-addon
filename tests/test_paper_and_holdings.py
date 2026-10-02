import tempfile

from datetime import datetime, timedelta, timezone
from market_flow_bot.app.paper import PaperLedger as RealLedger

class PaperLedger(RealLedger):
    """Supply distinct completed observations, as the production scanner does."""
    tick = 0
    def process(self, rows, session=None):
        self.tick += 1
        hour = 12 if session == 'premarket' else 14
        self.test_now = datetime(2026,10,1,hour,tzinfo=timezone.utc)+timedelta(minutes=self.tick*5)
        data = [dict(r, direction=1, asof=(self.test_now-timedelta(minutes=5)).isoformat()) for r in rows]
        return super().process(data,session,now=self.test_now)
    def snapshot(self, account, prices=None):
        now = getattr(self,'test_now',datetime(2026,10,1,14,tzinfo=timezone.utc))
        prices = {k:dict(v,asof=now.isoformat()) for k,v in (prices or {}).items()}
        return super().snapshot(account,prices,now=now)
from smart_money_tracker.app.core import normalise
from smart_money_tracker.app.main import estimate_holdings


def test_auto_paper_targets_then_score_exit():
    ledger = PaperLedger(tempfile.NamedTemporaryFile(suffix='.db').name)
    ledger.save_rules({'position_size': 3000, 'buy_score': 80, 'score_exit': 75,
                       'targets': [5, 12.5, 20], 'target_fractions': [1/3, 1/3, 1/3]}, 'auto')
    ledger.process([{'ticker': 'ABC', 'price': 100, 'score': 85, 'rvol': 2}], session='regular')
    assert ledger.snapshot('auto')['positions'] == []
    ledger.process([{'ticker': 'ABC', 'price': 100, 'score': 85, 'rvol': 2}], session='regular')
    ledger.process([{'ticker': 'ABC', 'price': 105, 'score': 85, 'rvol': 2}], session='regular')
    position = ledger.snapshot('auto', {'ABC': {'price': 105}})['positions'][0]
    assert 19.9 < position['shares'] < 20.1
    ledger.process([{'ticker': 'ABC', 'price': 106, 'score': 74, 'rvol': 2}], session='regular')
    assert ledger.snapshot('auto', {'ABC': {'price': 106}})['positions'] == []


def test_auto_paper_uses_smaller_extended_hours_allocation():
    ledger = PaperLedger(tempfile.NamedTemporaryFile(suffix='.db').name)
    ledger.save_rules({'position_size': 3000, 'extended_hours_position_size': 500,
                       'buy_score': 80, 'allow_overnight': True}, 'auto')
    signal = [{'ticker': 'PRE', 'price': 100, 'score': 88, 'rvol': 2}]
    ledger.process(signal, session='premarket')
    ledger.process(signal, session='premarket')
    assert ledger.snapshot('auto')['positions'] == []
    ledger.process(signal, session='premarket')
    pre = ledger.snapshot('auto', {'PRE': {'price': 100}})
    assert pre['market_value'] == 500
    regular_signal = [{'ticker': 'REG', 'price': 100, 'score': 88, 'rvol': 2}]
    ledger.process(regular_signal, session='regular')
    ledger.process(regular_signal, session='regular')
    regular = ledger.snapshot('auto', {'PRE': {'price': 100}, 'REG': {'price': 100}})
    assert regular['market_value'] == 3500


def test_pending_buy_cancels_short_spike_and_price_chase():
    ledger = PaperLedger(tempfile.NamedTemporaryFile(suffix='.db').name)
    ledger.process([{'ticker': 'SPIKE', 'price': 100, 'score': 85, 'rvol': 2}], session='regular')
    assert len(ledger.snapshot('auto')['pending']) == 1
    ledger.process([{'ticker': 'SPIKE', 'price': 100, 'score': 74, 'rvol': 2}], session='regular')
    assert ledger.snapshot('auto')['pending'] == []
    ledger.process([{'ticker': 'CHASE', 'price': 100, 'score': 85, 'rvol': 2}], session='regular')
    ledger.process([{'ticker': 'CHASE', 'price': 104, 'score': 85, 'rvol': 2}], session='regular')
    assert ledger.snapshot('auto')['pending'] == []
    assert ledger.snapshot('auto')['positions'] == []


def test_manual_paper_buy_accepts_dollar_spend():
    ledger = PaperLedger(tempfile.NamedTemporaryFile(suffix='.db').name)
    ledger.manual_trade({'ticker': 'XYZ', 'side': 'buy', 'amount': 3000, 'price': 120})
    snap = ledger.snapshot('manual', {'XYZ': {'price': 120}})
    assert snap['market_value'] == 3000
    assert snap['positions'][0]['shares'] == 25


def test_holdings_estimated_buy_price_return_and_gain():
    rows = []
    for index, (action, amount) in enumerate([('Buy', '$10,000 - $10,000'),
                                               ('Sell', '$2,000 - $2,000')]):
        row = normalise({'ticker': 'ABC', 'politician': 'Test Person', 'chamber': 'House',
                         'action': action, 'transaction_date': f'09/{index + 1:02d}/2026',
                         'disclosure_date': '09/30/2026', 'amount': amount},
                        'csv_import', str(index))
        row.update(estimated_price=100.0, current_price=120.0)
        rows.append(row)
    holding = estimate_holdings(rows)[0]
    assert holding['estimated_avg_buy_price'] == 100
    assert holding['estimated_return_pct'] == 20
    assert holding['estimated_unrealised_gain'] == 1600
