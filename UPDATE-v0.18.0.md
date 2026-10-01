# Market Flow 0.18.0 + Smart Money 0.6.0

## New: Unusual Market Activity

- Separate Activity Alerts window inside Market Flow.
- Time-of-day-adjusted relative stock volume.
- 15-minute volume burst and rising-volume acceleration.
- Price direction, dollar volume and Chaikin flow confirmation.
- Conservative Potential Buy, Watch, Bearish Watch and Reduce/Risk labels.
- Background Yahoo option-chain sampling for the strongest candidates.
- Call/put volume, volume/open-interest and approximate traded-premium context.
- Search, signal and score filters plus sorting by every useful activity measure.
- Moderate signals are highlighted in the app; only strong, liquid signals push
  a Home Assistant phone notification.
- Four-hour per-stock cooldown and the existing global hourly notification cap.

## Important limitations

Yahoo options are delayed snapshots, not tick-level OPRA flow. The scanner cannot
reliably determine whether every contract was opened, closed, bought or sold.
Activity labels are screening signals and are not financial advice or evidence of
inside information. The provider layer can be upgraded later without replacing
the scoring/UI design.

## Install

Replace both add-on folders as before, refresh the Home Assistant add-on store,
then rebuild/reinstall **Market Flow Bot**. Smart Money remains version 0.6.0 but
is included so the two folders stay matched.
