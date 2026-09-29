# Update 0.16.3 / Smart Money 0.2.3

This update adds:

- politician-score and estimated-filing-value sorting;
- minimum/maximum estimated-value filters for whole filings or single transactions;
- a midpoint estimate on each transaction and a summed estimate on the collapsed filing row;
- expanded filings that remain open during automatic data refreshes;
- a mobile input guard so the keyboard Back action stays inside the add-on;
- larger Market Flow sector names, arrow ratio/cash labels and end-ball values;
- the same estimated trade values and persistent expansion in Market Flow's linked stock panel.

Estimated values are derived from public disclosure ranges. A bounded range uses
its midpoint. An open-ended range uses its disclosed minimum and is marked
`at least`. These are comparison/filtering aids, not exact trade or current
position values.

Update/rebuild both local add-ons, then start Market Flow first and Smart Money
second. Existing `/data` databases and add-on options are not replaced by these
source files.
