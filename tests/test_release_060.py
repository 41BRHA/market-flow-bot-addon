import tempfile

from smart_money_tracker.app.core import Store, normalise
from smart_money_tracker.app.main import estimate_holdings
from market_flow_bot.app.actuals import _signed_percent, MATCHERS, enrich
from smart_money_tracker.app.executive import _kind_match, _ticker


def test_global_value_alert_matches_any_person_from_enable_date():
    store = Store(tempfile.NamedTemporaryFile().name)
    saved = store.set_global_alert(True, ["Buy", "Sell"], 1_000_000)

    rules = store.matching_alerts("Any Politician", saved["start_date"])
    assert rules[0]["politician"] == "*"
    assert rules[0]["min_value"] == 1_000_000


def test_estimated_holdings_uses_trade_price_and_current_price():
    rows = []
    for index, (action, amount, trade_price) in enumerate([
        ("Buy", "$10,000 - $10,000", 100.0),
        ("Sell", "$2,000 - $2,000", 100.0),
    ]):
        row = normalise({"ticker": "ABC", "politician": "Test Person", "chamber": "House",
                         "action": action, "transaction_date": f"09/{index + 1:02d}/2026",
                         "disclosure_date": "09/30/2026", "amount": amount}, "csv_import", str(index))
        row.update(estimated_price=trade_price, current_price=120.0)
        rows.append(row)

    holding = estimate_holdings(rows)[0]
    assert holding["estimated_shares"] == 80
    assert holding["estimated_current_value"] == 9_600
    assert holding["status"] == "Likely held"


def test_bea_percent_parsing_and_unsafe_calendar_matches():
    pattern = r"real gross domestic product[^.]*?\b(increased|decreased)\s+at an annual rate of\s+([\d.]+)\s+percent"
    assert _signed_percent("Real gross domestic product increased at an annual rate of 2.2 percent", pattern) == 2.2
    assert _signed_percent("Real gross domestic product decreased at an annual rate of 0.5 percent", pattern) == -0.5
    assert not any(predicate("adp non-farm employment change") for predicate, _ in MATCHERS)
    assert not any(predicate("final gdp price index q/q") for predicate, _ in MATCHERS)


def test_executive_ocr_and_exact_ticker_aliases():
    assert _kind_match("PALANTIR TECHNOLOGIES INC CL A lourchaso 7/17/2026")
    assert _ticker("PALANTIR TECHNOLOGIES INC CL A") == "PLTR"
    assert _ticker("HERSHEY COMPANY (THE)") == ""


def test_actual_enrichment_revisits_yesterday(monkeypatch):
    from datetime import datetime, timedelta, timezone
    event={"title":"Core PCE Price Index m/m","datetime_utc":(datetime.now(timezone.utc)-timedelta(hours=30)).isoformat(),"actual":""}
    monkeypatch.setattr("market_flow_bot.app.actuals.actual_for",lambda title:"+0.2% MoM")
    assert enrich([event]) is True
    assert event["actual"] == "+0.2% MoM"
