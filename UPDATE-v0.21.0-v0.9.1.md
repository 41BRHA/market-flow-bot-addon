# Market Flow 0.21.0 / Smart Money 0.9.1

This package includes all calculation corrections from 0.21.0/0.9.0 and a performance correction for Politician Trades.

- Opening Disclosures and applying filters now reads the stored database and cached price results only. It no longer makes thousands of blocking Market Flow price requests.
- Historical prices refresh separately in a background thread about every eight hours.
- The historical price cache is saved under the Smart Money add-on `/data` directory and survives add-on rebuilds/restarts.
- Disclosure summaries return only the 25 visible summary rows. Individual transaction rows load when that disclosure is opened, preventing large executive filings with more than 1,000 transactions from slowing every page load.
- Politician scores are calculated once per person for each request instead of once for every filing row.

Install by replacing both matching add-on source folders, refreshing the Home Assistant add-on store and rebuilding/updating both add-ons. Do not uninstall the add-ons or remove `/data`; that directory holds the collected database, saved price cache, alert settings and paper accounts.

The first background price pass after installation may take time, but the page and its filters remain usable while it runs. Existing filings are not recollected merely because the page was opened.
