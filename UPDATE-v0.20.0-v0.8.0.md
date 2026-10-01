# Market Flow 0.20.0 + Smart Money 0.8.0

Copy both add-on folders into the Home Assistant add-ons directory, refresh the
local add-on store, then rebuild and restart both add-ons.

Key fixes:

- Donald J. Trump Executive/OGE reports use OGE's current public collection API.
- Executive collection status shows real document and parsed-trade counts.
- Table headings sort the visible result immediately; current holdings and 13F
  tables now have sortable columns too.
- Disclosure pages no longer redraw every 30 seconds while being inspected.
- Calendar actual values are retried for eight days, including yesterday's
  releases, and the calendar reports its last source error.
- Paper trading displays the planned amount per buy and maximum allocation.

After first start, press **Check for new filings** once. Executive PDF parsing
can take several minutes because the official reports are large. The status must
show more than zero Executive documents/trades before Donald J. Trump appears.
