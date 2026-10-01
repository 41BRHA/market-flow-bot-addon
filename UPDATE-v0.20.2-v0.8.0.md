# Market Flow 0.20.2 + Smart Money 0.8.0

Install both folders over the corresponding existing add-on folders, then rebuild/reinstall and restart the add-ons in Home Assistant.

## Confirmed automatic paper buys

Mock 1 no longer buys immediately from a single brief activity spike.

- Regular hours require 2 consecutive qualifying scans.
- Pre-market and after-hours require 3 consecutive qualifying scans.
- Relative volume must remain at least 1.5x.
- Pending entry cancels if the score falls below 75.
- Pending entry cancels if the price rises more than 3% from the initial signal.
- Pending candidates and their confirmation progress are displayed on the Paper Accounts page.
- All thresholds and confirmation counts are editable on screen.
- Confirmed buys remain $3,000 in regular hours and $500 in pre/after-hours by default.
- Mock 2 manual buys accept a dollar spend amount and calculate fractional shares.

This release includes all prior Market Flow 0.20.x and Smart Money 0.8.0 changes.
