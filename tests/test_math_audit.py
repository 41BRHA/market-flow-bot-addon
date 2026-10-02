from datetime import datetime, timedelta, timezone
import zipfile
import types
from pathlib import Path

from market_flow_bot.app.paper import PaperLedger
from market_flow_bot.app.activity import combine_score
from smart_money_tracker.app.prices import PriceBridge

NOW = datetime(2026, 10, 1, 14, 0, tzinfo=timezone.utc)

def signal(minute=0, **changes):
    return dict(ticker='ABC', price=100, score=90, direction=1, rvol=2,
                asof=(NOW+timedelta(minutes=minute)).isoformat(), **changes)

def run(ledger, row, minute=0):
    ledger.process([row], 'regular', now=NOW+timedelta(minutes=minute+5))

def test_repeated_observation_does_not_confirm(tmp_path):
    ledger=PaperLedger(str(tmp_path/'p.db'))
    for _ in range(6): run(ledger, signal())
    assert ledger.snapshot('auto')['positions']==[]
    assert ledger.snapshot('auto')['pending'][0]['confirmations']==1
    run(ledger, signal(5),5)
    assert len(ledger.snapshot('auto')['positions'])==1

def test_bearish_and_stale_observations_cannot_buy(tmp_path):
    ledger=PaperLedger(str(tmp_path/'p.db'))
    ledger.save_rules({'confirm_regular_scans':1}, 'auto')
    run(ledger,{**signal(),'direction':-1})
    run(ledger,signal(),90)
    assert ledger.snapshot('auto')['positions']==[]

def test_one_confirmation_and_all_gap_targets(tmp_path):
    ledger=PaperLedger(str(tmp_path/'p.db'))
    ledger.save_rules({'confirm_regular_scans':1,'target_fractions':[.3333,.3333,.3333]},'auto')
    run(ledger,signal())
    assert len(ledger.snapshot('auto')['positions'])==1
    run(ledger,{**signal(5),'price':125},5)
    snap=ledger.snapshot('auto')
    assert snap['positions']==[]
    assert snap['realised']==750
    assert snap['cash']==100750

def test_missing_quote_does_not_fake_zero_return(tmp_path):
    ledger=PaperLedger(str(tmp_path/'p.db'))
    ledger.manual_trade({'ticker':'ABC','side':'buy','amount':3000,'price':100})
    snap=ledger.snapshot('manual')
    assert snap['equity'] is None
    assert snap['positions'][0]['return_pct'] is None
    assert snap['cash']==97000

def test_realised_uses_entire_history(tmp_path):
    ledger=PaperLedger(str(tmp_path/'p.db'))
    for _ in range(160):
        ledger.manual_trade({'ticker':'ABC','side':'buy','shares':1,'price':100})
        ledger.manual_trade({'ticker':'ABC','side':'sell','shares':1,'price':101})
    snap=ledger.snapshot('manual')
    assert len(snap['events'])==300
    assert snap['realised']==160
    assert snap['cash']==100160

def test_upgrade_preserves_old_positions_and_cash(tmp_path):
    archive=Path(__file__).parents[1]/'market-flow-v0.20.4-smart-money-v0.8.2.zip'
    if not archive.exists():
        import pytest
        pytest.skip('Previous release archive needed only for migration validation')
    with zipfile.ZipFile(archive) as z:
        member=next(n for n in z.namelist() if n.endswith('market_flow_bot/app/paper.py'))
        old=types.ModuleType('old_paper')
        old.__file__=str(tmp_path/'paper.py')
        exec(compile(z.read(member),member,'exec'),old.__dict__)
    filename=str(tmp_path/'p.db')
    ledger=old.PaperLedger(filename)
    ledger.manual_trade({'ticker':'ABC','side':'buy','amount':3000,'price':100})
    ledger.db.close()
    upgraded=PaperLedger(filename)
    snap=upgraded.snapshot('manual')
    assert snap['cash']==97000
    assert snap['positions'][0]['shares']==30
    assert len(snap['events'])==1
    upgraded.db.close()
    assert PaperLedger(filename).snapshot('manual')['cash']==97000

def test_stale_options_cannot_boost_score():
    row=combine_score({'ticker':'ABC','stock_score':80,'direction':1},
        {'asof':'2000-01-01T00:00:00+00:00','total_volume':100,'score':100,'call_put_volume_ratio':5})
    assert not row['options_confirmed']
    assert row['score']==80

def test_price_cache_survives_tracker_restart(tmp_path):
    path=tmp_path/'prices.json'
    bridge=PriceBridge('',path)
    bridge.cache['ABC|2026-09-01']=(1000,{'estimated_price':25,'current_price':30})
    bridge._save_cache()
    restarted=PriceBridge('',path)
    row=restarted.enrich_cached([{'ticker':'ABC','transaction_date':'2026-09-01',
                                  'action':'Buy','asset_type':'ST'}])[0]
    assert row['estimated_price']==25
    assert row['current_price']==30
    assert row['directional_return_pct']==20

def test_flow_map_is_one_complete_html_document():
    page=(Path(__file__).parents[1]/'market_flow_bot/app/flow_map.html').read_text()
    lower=page.lower()
    assert lower.count('<!doctype html>')==1
    assert lower.count('<script>')==1
    assert lower.count('</script>')==1
    assert lower.count('</html>')==1
    assert not page[lower.index('</html>')+len('</html>'):].strip()
