# Market Flow 0.21.0 / Smart Money 0.9.0

## Install

1. Back up both installed add-ons in Home Assistant.
2. Extract this ZIP. Copy its `market_flow_bot` and `smart_money_tracker` folders into your existing add-on repository root, replacing source files in the matching folders. Do not put an extra enclosing folder inside either add-on.
3. Commit/push if your installation uses your Git repository. Refresh the Home Assistant add-on store, then update/rebuild both add-ons and restart them.
4. Reload the browser page after the updates.

Do not uninstall the add-ons or delete their data directories. The ZIP contains no runtime databases. Normal in-place updates retain `/data`, including paper buys, sells, cash, positions and settings. An additive migration was tested against the previous release. Old pending confirmations are intentionally cleared; historical trades are retained rather than rewritten.

## Corrected calculations

- Automatic paper entries require a bullish score and distinct fresh observations. Repeated cached bars do not confirm a signal. Session-mismatched and stale quotes cannot trigger entries. Regular/extended allocations remain $3,000/$500 unless changed in settings.
- A one-observation setting works immediately. Confirmation counts are capped; session changes reset pending entries. Missing signals break confirmation.
- Profit tranches use the original quantity and a saved exit plan. Rounded thirds are normalised; a price gap through several targets processes every crossed tranche and closes the final remainder.
- Realised profit, winning-sale percentage and realised drawdown use the entire trade history, while the screen shows the latest 300 events. Missing fresh prices produce unavailable valuation/return rather than an invented zero return.
- Volume uses comparable completed five-minute observations at the same session/time. Incomplete grids are excluded. Twenty- and forty-session baselines activate only when enough comparable sessions exist. Available 5/20/40-session medians are blended; sparse extended-hours data can legitimately produce no score.
- Old/empty options data cannot increase a score. Max pain is labelled as a calculation, not a predicted price destination.
- Estimated politician holdings keep owners separate while reconstructing positions, reduce cost basis after sales and withhold unreliable values for unknown opening holdings, missing prices and unreconciled amendments. Unsupported instruments, including options, are not valued as common shares.
- Duplicate 13F rows are aggregated. Explicit restatements and additional-holdings amendments are distinguished; unknown amendments produce a review warning. Missing comparison periods do not imply new purchases.
- Numeric validation rejects nonfinite/invalid amounts; the 16:00 bar is excluded from regular-session comparisons. Stale or wrong-session activity alerts are suppressed.
- Calendar matching no longer assigns a monthly core-CPI value to a yearly event, or an interest-rate value to a speech.

## Validation and practical limits

30 automated regression tests passed, including migration from the previous release, full-history accounting, repeated-observation protection, stale/bearish signals, tranche rounding and gap exits. Python compilation and both frontend JavaScript syntax checks passed. This package has not been run on your Home Assistant machine or verified against live provider responses.

Existing simulated fills are preserved, including fills made by earlier rules; this update cannot retrospectively make them accurate. Paper fills use observed closing prices and do not model bid/ask spread, slippage, commissions or guaranteed execution. Corporate actions and incomplete disclosure histories can still limit portfolio estimates. Scores are heuristic signals, not calibrated success probabilities. Calendar publication matching and source availability are not guaranteed by a successful refresh. A full 40-session intraday baseline depends on retained/provider history and is not backfilled by inventing data.

Profit-target changes apply to new positions; existing positions retain their saved target plan. The displayed winning-sale percentage counts sell executions, including partial exits, rather than completed round trips. Unrealised equity drawdown is not the displayed realised drawdown.

Run tests from this directory with `PYTHONPATH=. python3 -m pytest -q tests`. The migration test additionally needs the previous release ZIP and skips if it is absent.
