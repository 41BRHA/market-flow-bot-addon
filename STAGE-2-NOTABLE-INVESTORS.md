# Stage 2 — Notable investors / SEC 13F

This stage adds a separate institutional holdings source to Smart Money Tracker.
It does not mix quarterly 13F holdings with politician transaction scores.

## One-time setup

In the Smart Money Tracker add-on configuration set `sec_user_agent` to a
descriptive application name and a monitored contact email, for example:

`Adam Smart Money Tracker adam@example.com`

Restart the add-on. The Notable Investors tab will show collection state and
the latest available filings. Checks occur on startup, when **Check SEC now**
is pressed, and approximately every eight hours.

## Interpretation

- Direction is based on reported share-count difference, not value difference.
- Value may rise or fall because the market price changed during the quarter.
- Filing date and quarter-end report date are both displayed.
- Amendments compare with the previous distinct quarter.
- 13F does not reveal exact execution prices or dates.
- No ticker is inferred from an issuer name or CUSIP.
- Notifications are opt-in and historical filings are baselined when enabled.
