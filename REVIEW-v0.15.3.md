# Independent review of Market Flow v0.15.3

Reviewed 29 September 2026. Input: the user's v0.15.3 archive. The new package contains v0.15.4 with disclosure integration only; it does not silently replace the existing flow formula or trading-alert strategy. The running Home Assistant installation was not accessed or modified.

## Changes verified

| Earlier finding | v0.15.3 result |
|---|---|
| Flat close counted positive | Fixed in the legacy fallback: unchanged closes produce zero signed contribution. The main formula has changed to a dollar-volume-weighted Chaikin close-location multiplier. |
| Missing ticker incorrectly marked cached | Fixed: coverage is recorded for returned symbols only. Tested partial responses. |
| Single-ticker Yahoo extraction | Fixed for the default ticker-first MultiIndex shape. Synthetic extraction returned AAPL. |
| Missing option open interest poisons max pain | NaN/Inf open interest is converted to zero. The earlier 90-versus-100 reproduction now returns 100. |
| Max-pain history mixes expiries | Comparisons now filter to the same expiry. |
| No RVOL baseline treated as confirmation | Fixed: returns 0 instead of 1. |
| All-positive board labelled broad outflow | Headline improved; the mixed-board top/bottom lists can still include the wrong sign. |
| Custom end date excludes that date | Bare end dates now add one day. Same-day selection spans 24 hours. Timezone-offset handling and inclusive bar boundary remain issues. |
| Futures dollar values omit contract size | Multipliers added for configured GC/SI and others. |
| Central total double-counts theme baskets | Dashboard restricts central total to a named GICS list. This reduces overlap but is still a configured subset of equities, not total market cash flow. |

## Important remaining issues

1. **Stale signals are still possible.** `main.py` calls `compute()` on five days of frames. Freshness is based on the newest bar across every symbol. Fresh futures or one fresh stock can hide stale equities. The existing flow alerts are sent even when the global stale flag is true; only the new price-move alerts check it. No per-symbol session or freshness gate was added. Repeated polling can alert again from the same bars after cooldown.

2. **The economic-release freshness gate is insufficient.** `actuals.py` checks whether a monthly observation is at most 70 days old, rather than whether it is the reference month for this event. Reproduction: with the clock set to 4 September 2026, a July observation passes (65 days old), although an August payroll release would require August data. The 150-day quarterly gate likewise cannot distinguish GDP release vintages. Event feed refresh still rebuilds blank `actual` fields and can erase previously filled values. Broad title matching can assign a rate value to FOMC minutes or a total-payroll value to a different payroll release.

3. **The formula changed meaning.** New net = sum(close-location multiplier × close × volume × contract size). Gross = sum(close × volume × contract size). This is a dollar-weighted close-location indicator; it is not conventional share-volume-weighted CMF and does not observe buyer/seller initiated trades or capital transfers. A stock may gap down and still have positive close-location flow. `Money flow`, `buyers dominated`, and the max-pain `gravitational bias` wording are stronger than the computation establishes.

4. **Invalid inputs can break the advertised [-1,1] bound.** No check enforces low <= close <= high or nonnegative volume. Reproduction: close 102, high 101, low 99 yields ratio 2.0. When old cached bars have no high/low, a rolling window takes only N bars and loses the first fallback contribution; new bars use all N. Historical windows can therefore mix two different methods.

5. **Confidence remains overstated.** Density counts rows, not positive-volume bars or expected timestamps. Missing constituents are excluded from its denominator. A small surviving subset can look fully representative of a sector. RVOL compares the last two sets of bars, not matching session/time-of-day history; the stored `history` and several documented threshold settings are not used by the signal calculation.

6. **New price alerts have misleading reference names.** `pct_changes(prev_close)` uses the last bar before the date of the newest bar, including after-hours data, rather than the previous regular-session closing price. Test: regular close 100, late after-hours 110, next day 111 reports +0.91%, not +11%. `session_open` uses the first available close of the date (potentially premarket), not the actual open of the selected session. The newest bar's date defines 'today', so stale symbols can pass through when another symbol makes global data appear fresh.

7. **Cache and concurrency need more work.** Five warming threads can request overlapping history concurrently; deduplication is per period key, not shared data range. A partial latest hourly/daily candle may be recorded as covered and not revisited at its original timestamp, leaving its volume/close unfinished. MaxPainStore shares a SQLite connection across threads without the lock used in BarStore. JSON files are still overwritten non-atomically. No concurrency failure was asserted as reproduced here.

8. **Date and detail consistency.** `_parse_range` replaces timezone information rather than converting it, so explicit offset datetimes shift. BarStore uses an inclusive end, potentially including the following midnight bar. Sector-detail period tabs read only cached data; switching to an unwarmed resolution does not initiate backfill. The live stock detail window is two hours while the main live flow uses six bars.

9. **Build/docs limitations.** Webull remains a scaffold. README/DOCS describe earlier signal logic and session-aware baselines that the current compute path does not implement. No Docker build or Home Assistant end-to-end deployment was performed during this review.

## New integration scope

The only Market Flow changes in v0.15.4 are: `smart_money_url` configuration, a read-only `/api/politician-trades?ticker=...` proxy, the stock-detail disclosure panel, its scroll handling, and the version number. No Market Flow database/schema changes were introduced by this integration. Smart Money keeps its own SQLite database and does not download prices.

## Validation

- Controlled v0.15.3 formula, extraction, date-boundary and freshness reproductions.
- New tracker automated tests cover normalization, exact ranges, dates, imports, deduplication, filters, report replacement, real official PDF parsing, HTTP API and the Market Flow bridge.
- Six recent official House PDFs exercised; 83 explicit ticker records extracted. This small sample does not establish full coverage. Non-ticker investments, scanned filings and unsupported layouts remain visible as incomplete reports.
- Dashboard DOM interaction tests passed for search, the integrated stock panel, source links and ticker switching; visual browser validation was blocked by a failed browser-binary download.
- Optional FMP Senate adapter checked against the published endpoint and synthetic response tests; no credential was provided for an authenticated live test.

Sources: official House annual index and PTR PDFs at https://disclosures-clerk.house.gov/ ; FMP Senate documentation at https://site.financialmodelingprep.com/developer/docs/stable/senate-latest .
