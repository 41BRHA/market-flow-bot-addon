# 0.16.0

- Added a non-blocking `/api/trade-prices` bridge backed by Market Flow's existing bar store. It estimates a disclosure's trade price from the daily close on the transaction date or next trading session and compares it with the newest cached price.
- Added cached company profiles and valuation metrics to the left stock panel: company name, market cap, trailing/forward P/E, price/book, margins, growth and 52-week range.
- Grouped politician disclosures by person and disclosure date in the stock panel, with expandable trades, estimated/latest prices and performance.
- Corrected regular-session references for move alerts, invalid OHLC/negative-volume handling, zero-open-interest max pain, timezone parsing, stale-data alert/state handling and concurrent Yahoo/SQLite access.
- Latest dashboard snapshots are now written atomically.

# 0.15.4

Adds a politician-disclosure section to the selected stock's detail panel, reading the separate Smart Money Tracker add-on. Adds the `smart_money_url` option and `/api/politician-trades` endpoint. No trading-formula or database changes in this integration release. See the bundle's independent v0.15.3 review for remaining market-data issues.
