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
