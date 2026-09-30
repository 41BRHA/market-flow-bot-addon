# 0.3.0

- Add Stock Exposure: one summarised row per ticker across collected disclosures.
- Filter by sector, industry, politician, chamber, transaction type, value, participation and average score.
- Show estimated Buy, Sell, gross and net disclosed flow with explicit holdings limitations.
- Add per-stock drill-down with politicians, official roles, valuation, transactions and source links.
- Add current price plus nearest-expiry max-pain strike, expiry and distance from price.
- Add a sector-level Politician Flow Map with period and date-basis controls.
- Use an eight-hour background price/option refresh for the politician universe.

# 0.2.3

- Sort disclosures by politician score, filing value or disclosure date.
- Filter by minimum/maximum estimated value at either filing or transaction level.
- Show a midpoint estimate for every transaction and a summed estimate/range before expansion.
- Preserve expanded filings across the 30-second refresh instead of closing them.
- Add a focused-input history guard so a mobile keyboard Back action does not leave the add-on.

# 0.2.1

- Automatic current-member profiles from official House Clerk and Senate XML feeds, refreshed daily and cached locally.
- Shows office, party, state/district, committee leadership, committee assignments and plain-language oversight areas.
- Adds visible Market Flow price-link status and prevents repeated multi-batch timeouts when Market Flow is unavailable.
- Corrects missing prices being rendered as `$0.00`.

# 0.2.0

- One expandable dashboard row per politician and disclosure date.
- Politician selector populated from collected names.
- Estimated transaction-date close, latest Market Flow price and direction-adjusted return.
- Transparent 0-100 Trade Score with win rate, average return and sample size.
- Prices are read from Market Flow's cache through the internal add-on link; Smart Money does not run a second Yahoo downloader.
- Existing flat `/api/trades` response remains available for compatibility; grouped results are opt-in with `grouped=1`.

# 0.1.0

- Official House index and PDF collector with bounded work, persistent retries and explicit coverage gaps.
- Normalized disclosure records, amount ranges, owners, source links and dates.
- Optional Senate provider and validated CSV import.
- Search dashboard and per-ticker HTTP API for Market Flow.
