# Claude review handover — Market Flow 0.16.1 / Smart Money 0.2.1

## User-visible changes

- Smart Money shows one row per politician and disclosure date. Expanding it
  shows every matching trade.
- The politician filter is a selector populated from the names already stored.
- Buy/Sell records show an estimated trade-date close, newest price and a
  direction-adjusted return.
- Each politician has a 0–100 Trade Score plus win rate, average return and
  rated/eligible sample count.
- Market Flow's left stock panel shows the full company name and a cached set of
  valuation/quality metrics.
- The politician section inside that same panel uses the grouped disclosure view
  and displays prices and score, so the two add-ons remain visibly connected.
- Current office, party, state/district, leadership and committee assignments
  are refreshed daily from official House Clerk and Senate XML data. Major
  committees are translated into short oversight descriptions.
- The dashboard now shows whether its internal Market Flow price link is
  connected, waiting or unavailable. Missing values display as Pricing/— rather
  than misleading zeroes.

## Price architecture

Smart Money does not download Yahoo prices. It calls Market Flow's internal
`/api/trade-prices` endpoint. `PeriodEngine.trade_prices()` reads the existing
SQLite bar cache immediately and starts a background daily-bar fill when needed.
The HTTP response remains non-blocking and clients retry pending prices.

The “trade price” is explicitly an estimate: the daily close on the disclosed
transaction date, or the next trading session within seven days. It is not the
politician's execution price. Latest price is the newest cached 5-minute close,
falling back to daily data. Values can be delayed by the upstream Yahoo feed.

## Score formula

Only Buy and Sell records with both prices are rated. For a Buy, directional
return is `(current / estimate - 1) * 100`; for a Sell it is the negative of
that market return. All trades have equal weight because reported amounts are
ranges.

`raw = 50 + 0.5 * clamp(avg_return, -50, 50) + 0.5 * (win_rate - 50)`

`score = 50 + (raw - 50) * n / (n + 5)`

The shrink factor keeps one lucky disclosure close to neutral. The API labels
scores partial until every eligible stored trade used for that politician has a
price. `score_started_at` records when this database began tracking scores.

## Calculation and reliability corrections

- Chaikin flow rejects non-positive prices, negative volume and invalid OHLC
  ranges; it uses the pre-window close for the first fallback bar.
- Previous-close and session-open alerts now use regular US session bars rather
  than post-market/premarket bars.
- An options chain with no positive open interest returns no max-pain value.
- Max-pain SQLite access is serialized across worker and web threads.
- Explicit ISO offsets in custom ranges are converted to UTC rather than
  overwritten, and reversed ranges are rejected.
- Stale market data no longer advances direction state/history or sends flow
  alerts.
- Yahoo downloads are serialized inside the provider because yfinance uses
  process-level caches.
- `latest.json` is replaced atomically.

## Compatibility and limits

- `/api/trades` still returns its original flat `trades` list. The dashboard and
  Market Flow bridge add `grouped=1` to receive `disclosures` alongside it.
- Scores reflect collected disclosures and available cached prices. They are a
  tracking statistic, not proof of skill, holdings or profit.
- House coverage remains partial where a PDF is scanned or uses an unsupported
  layout. Senate coverage still needs the optional configured source/import.
- Fundamentals come from Yahoo via yfinance and are cached for 24 hours. Missing
  or non-meaningful ratios display as unavailable.
- Profiles cover current members. Former filers remain explicitly unmatched
  rather than being assigned the current holder of their old seat.

## Verification

- 30 Python tests pass, including the two real official House PDF fixtures.
- Added regression coverage for grouping, people lists, score direction and
  sample shrinkage, official House/Senate profile mapping, price-date fallback,
  invalid flow bars, regular-session move references, empty open interest and
  timezone-offset parsing.
- jsdom integration checks pass for the standalone grouped dashboard and the
  Market Flow stock panel, including expansion, prices, full company name,
  valuation metrics, source links and ticker switching.
