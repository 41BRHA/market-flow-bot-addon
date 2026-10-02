# Market Flow 0.21.3 / Smart Money 0.9.1

- Includes the 0.21.2 sparse extended-hours scoring correction.
- Paper positions with no recent pre-market trade now use the latest available
  market close for display, return and equity calculations instead of `—`.
- Last-close prices are labelled `last available` in the Current column.
- The latest-close fallback is display-only. Automatic buys, sells, stops and
  profit targets still require a fresh quote no older than 30 minutes.
- Full suite: 36 tests passing.
