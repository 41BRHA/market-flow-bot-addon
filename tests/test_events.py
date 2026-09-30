from datetime import timezone

from market_flow_bot.app.events import _parse_dt


def test_fair_economy_clock_is_utc():
    event = _parse_dt("09-30-2026", "12:30pm")

    assert event.tzinfo is timezone.utc
    assert event.isoformat() == "2026-09-30T12:30:00+00:00"


def test_fair_economy_clock_accepts_spacing():
    event = _parse_dt("09-30-2026", " 8:30 am ")

    assert event.isoformat() == "2026-09-30T08:30:00+00:00"
