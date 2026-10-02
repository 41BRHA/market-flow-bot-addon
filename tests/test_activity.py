from datetime import datetime, timezone
import numpy as np
import pandas as pd

from market_flow_bot.app.activity import combine_score, stock_activity


def _bars(today_multiplier=1.0, rising=True):
    rows, index = [], []
    for day in pd.date_range("2026-09-24", periods=5, freq="B", tz="America/New_York"):
        for slot in range(12):
            when = day + pd.Timedelta(hours=9, minutes=30 + slot * 5)
            current = day.date() == pd.Timestamp("2026-09-30").date()
            volume = 1000.0
            if current:
                volume *= today_multiplier * (1.0 + slot * 0.18 if rising else 1.0)
            price = 100.0 + (slot * 0.15 if current else slot * 0.01)
            rows.append({"open": price - .1, "high": price + .05, "low": price - .2,
                         "close": price, "volume": volume})
            index.append(when)
    return pd.DataFrame(rows, index=pd.DatetimeIndex(index))


def test_stock_activity_uses_same_time_baseline_and_detects_acceleration():
    row = stock_activity("TEST", _bars(today_multiplier=3.0, rising=True))
    assert row is not None
    assert row["rvol"] > 5
    assert row["burst_ratio"] > row["rvol"]
    assert row["acceleration"] > 1
    assert row["direction"] == 1
    assert row["stock_score"] >= 75


def test_bullish_options_can_confirm_but_do_not_create_direction():
    base = {"ticker": "TEST", "stock_score": 80, "direction": 1}
    result = combine_score(base, {"call_put_volume_ratio": 2.5, "score": 80, "total_volume": 100, "asof": datetime.now(timezone.utc).isoformat()})
    assert result["options_confirmed"] is True
    assert result["score"] == 92
    assert result["signal"] == "Potential Buy"

    mixed = combine_score({"ticker": "TEST", "stock_score": 80, "direction": 0},
                          {"call_put_volume_ratio": 4, "score": 100})
    assert mixed["options_confirmed"] is False
    assert mixed["signal"] == "Unusual / Mixed"
