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
