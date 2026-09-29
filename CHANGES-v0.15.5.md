# v0.15.5 — Claude review of ChatGPT's v0.15.4 + fundamentals

## Verified ChatGPT's work
- Politician tracker + integration reviewed: clean, security-conscious, all 21 tests pass.
- ChatGPT's integration into the flow bot is minimal and correct; the flow engine (signals/period/barstore) was left untouched.

## Fixes applied (from ChatGPT's review — all valid)
1. Chaikin multiplier is now clamped to [-1,1]; a bad tick (close printed outside the bar's high/low) can no longer push the ratio past ±1. Negative-volume bars are skipped.
2. Price-move reference now uses the true **regular-session** close/open (09:30–16:00 ET), so "vs prior close" no longer picks up after-hours prints.
3. MaxPainStore now serialises its SQLite connection with a lock (same SQLITE_MISUSE class fixed earlier in BarStore).
4. Flow alerts (flip/strong/summary) now also skip when data is stale — previously only the new price-move alerts checked it.
5. Event-calendar refresh now preserves already-filled `actual` values instead of wiping them.

## New feature (user request)
- Company **name** + **valuation** (market cap, trailing/forward P/E, EPS, dividend yield, 52-week range, beta) on each stock's detail card. Fetched lazily per sector from yfinance in a background worker, cached to /data (atomic writes), refreshed weekly. Best-effort — shows "—" until it warms up.

## Still open (documented, not yet fixed — see REVIEW-v0.15.3.md)
- Economic-release matching is age-based, not reference-period-matched (title matching can still be broad).
- Density/RVOL confidence is coarse (row counts, not session-matched).
- Wording around "money flow"/"gravitational bias" remains a bit stronger than the maths establishes.
