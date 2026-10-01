# 0.5.3

- Pause background Stock Exposure refreshes while any stock detail is expanded, preventing open panels from collapsing or moving the page.
- Manual filter changes still refresh immediately; pending background data retries after the panel is closed.

# 0.5.2

- Add a cache-only response mode for requests originating inside Market Flow.
- Break the Market Flow → Smart Money → Market Flow circular wait that could exceed the bridge timeout on uncached stocks.

# 0.5.1

- Display operational timestamps in a configured IANA timezone rather than the viewing device's timezone.
- Default to `Europe/London`, with automatic GMT/BST daylight-saving transitions.
- Format collection checks and market-price timestamps consistently while retaining UTC in persistent storage.

# 0.5.0

- Add a separate Notable Investors area backed by official SEC 13F filings.
- Start with a curated set of Berkshire Hathaway, Pershing Square, Scion, Bridgewater and Duquesne filing entities.
- Compare share counts against the prior distinct quarter and label New, Increased, Reduced, Exited and Unchanged positions.
- Handle same-quarter amendments without mistaking the original report for the prior quarter.
- Filter by manager, issuer/CUSIP, change type and minimum position value; sort by value, portfolio weight or absolute value change.
- Add opt-in per-manager notifications with change-type/value thresholds, historical baselining and persistent duplicate protection.
- Require a configurable descriptive SEC user agent and collect on a low-frequency eight-hour cycle.
- Preserve official identifiers and never guess absent 13F tickers.

# 0.4.0

- Add an opt-in **My alerts** view for selecting individual politicians.
- Allow Buy only, Sell only, Buy/Sell or all-transaction alerts plus a minimum estimated filing value.
- Combine matching transactions into one notification per newly stored politician filing batch.
- Baseline existing records when alerts are enabled and persist delivery identities, preventing historical floods and duplicate notifications after restarts or re-parsing.
- Deliver through a configurable Home Assistant `notify.*` service. CSV imports never create push alerts.

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
- **0.5.4** — Expose the display timezone as a configuration dropdown and keep
  expanded Stock Exposure rows open during background refreshes.
- **0.6.0** — Add universal minimum-filing-value alerts, estimated current
  holdings by politician, sortable disclosure headings, autocomplete inputs,
  and direct-download Executive Branch OGE collection including Donald Trump.
# 0.6.1

- Fix add-on startup after adding the Executive/OGE collector by explicitly
  installing its `requests` HTTP dependency.
## 0.7.0

- Adds live ticker/company search and clickable per-column sorting to Stock Exposure.
- Stops visual background refreshes from replacing the Stock Exposure screen;
  users receive a new-data notice and refresh it manually.
- Adds estimated weighted buy price, current return and unrealised gain/loss to
  Current Holdings with matching sort options and confidence warnings.
