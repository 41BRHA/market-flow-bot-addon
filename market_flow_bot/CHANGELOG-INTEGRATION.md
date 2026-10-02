# v0.20.4

- Activity relative volume now blends same-time-of-day median baselines over
  5, 20 and up to 40 prior trading sessions when cached history is available.
- The monthly baseline carries the greatest weight; robust medians prevent one
  exceptional market day from distorting the following session's score.
- Activity scoring now reads up to 60 days from the persistent bar cache without
  adding market-data requests. Shorter histories remain supported automatically.

# 0.17.3

- Prevent intermittent per-stock “Smart Money Tracker not reachable” cards by marking Market Flow bridge requests and avoiding a circular pricing callback.
- Try the last-known working add-on hostname first and retain the last successful stock disclosure response during a temporary timeout.

# 0.17.2

- Display dashboard timestamps in a configured IANA timezone instead of the phone/browser timezone.
- Default to `Europe/London`, including automatic GMT/BST daylight-saving transitions.
- Apply the timezone to flow updates, sector updates, economic-calendar dates/times and max-pain calculation times.
- Preserve UTC internally for storage, comparisons and market calculations.

# 0.17.1

- Add quiet notification mode: no routine summaries, stronger conviction/volume/data-density/breadth requirements, four-hour signal cooldowns, eight-hour move cooldowns and a rolling four-alert hourly cap.
- Raise quiet-mode price thresholds to 10% for a stock and 3% for a sector.
- Include the full company name alongside the ticker in single-stock notifications when metadata is available.

# 0.17.0

- Add cache-first stock-universe snapshots for the linked Smart Money exposure page.
- Refresh politician-only prices and max pain approximately every eight hours, outside the 15-minute flow cycle.
- Expose current price, sector/industry, valuation and nearest-expiry max-pain data through the internal link.
- Queue company metadata sequentially to avoid a burst of Yahoo requests on first backfill.
- Remember unsuccessful/no-options max-pain attempts for the same eight-hour window.

# 0.16.3

- Increased sector names, arrow flow/cash labels and end-ball values for readability.
- Show per-transaction and filing-level estimated politician trade values in the stock panel.
- Preserve open politician disclosures while pending prices refresh.
- Add a focused-input history guard for mobile keyboard Back actions.

# 0.16.1

- Display current official congressional role, party, state/district, committee assignments, leadership positions and relevant oversight areas beside disclosure names.
- Missing prices and valuation fields now display as pending/unavailable instead of `$0.00` or `0.0×`.
- Smart Money now reports whether its Market Flow price link is connected, waiting or unavailable.

# 0.16.0

- Added a non-blocking `/api/trade-prices` bridge backed by Market Flow's existing bar store. It estimates a disclosure's trade price from the daily close on the transaction date or next trading session and compares it with the newest cached price.
- Added cached company profiles and valuation metrics to the left stock panel: company name, market cap, trailing/forward P/E, price/book, margins, growth and 52-week range.
- Grouped politician disclosures by person and disclosure date in the stock panel, with expandable trades, estimated/latest prices and performance.
- Corrected regular-session references for move alerts, invalid OHLC/negative-volume handling, zero-open-interest max pain, timezone parsing, stale-data alert/state handling and concurrent Yahoo/SQLite access.
- Latest dashboard snapshots are now written atomically.

# 0.15.4

Adds a politician-disclosure section to the selected stock's detail panel, reading the separate Smart Money Tracker add-on. Adds the `smart_money_url` option and `/api/politician-trades` endpoint. No trading-formula or database changes in this integration release. See the bundle's independent v0.15.3 review for remaining market-data issues.
- **0.17.4** — Correct Fair Economy calendar times (the feed clock is GMT/UTC,
  not New York local time) and expose the display timezone as a configuration
  dropdown.
- **0.18.0** — Add Unusual Market Activity: time-of-day relative volume,
  15-minute bursts and acceleration, price/flow confirmation, paced delayed
  options snapshots, configurable high-conviction phone alerts, and a searchable
  sortable Activity Alerts dashboard.
- **0.17.5** — Preserve legitimate calendar actuals across schedule refreshes,
  report the last actual-data check separately, use current BEA release pages
  for GDP/PCE where available, and prevent ADP/GDP-price events from receiving
  unrelated government-series values.
## 0.19.0

- Adds a stable **All Stock Scores** page with live search, sector/signal filters,
  sortable columns, full component data and a manual new-data handoff.
- Adds two persistent, broker-free paper accounts: automatic strategy and manual
  orders, editable rules, staged exits, score exit, stops and decision history.
- Expands the universe with upgrade-safe 60-name sector scans. Core names refresh
  every cycle and broad names every fourth cycle while cached bars remain usable.
- Keeps activity scores available outside the regular session using the last
  completed comparable session.
## 0.20.3

- Fixed the Mock 2 manual-order controls appearing while Mock 1 automatic was selected.

## 0.20.2

- Added persistent pending-buy confirmation to Mock 1: two qualifying scans in regular hours and three in pre/after-hours by default.
- Pending candidates must retain the configured score and relative-volume requirement.
- Pending entries cancel when score falls below 75 or price rises more than 3% before entry.
- Added an on-screen pending-candidate table and editable confirmation rules.

## 0.20.1

- Fixed Mock 1 silently skipping 80+ activity signals outside the regular US session when overnight positions are allowed.
- Automatic buys default to $500 in pre-market/after-hours and $3,000 in regular hours; both amounts are editable on screen.
- Mock 2 manual buys now accept a dollar spend amount (default $3,000) and calculate fractional shares automatically; manual sells still use shares.

## 0.20.0

- Economic-calendar actual values are retried for eight days instead of only six hours.
- Manual refresh now revisits yesterday's releases and records calendar-source errors.
- Added calendar update diagnostics to the API and visible calendar footer.
- Paper accounts now show planned value per purchase and maximum planned allocation.
