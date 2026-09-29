# Market Flow + Politician Trades — linked build

This bundle contains:

- **Market Flow Bot 0.16.3**: market flow, company valuation and the linked politician-disclosure panel in each stock detail view.
- **Smart Money Tracker 0.2.3**: a linked Home Assistant add-on with its own disclosure database and dashboard.

## Install locally in Home Assistant

1. Make a Home Assistant backup that includes Market Flow and retain your v0.15.3 source ZIP.
2. Extract this bundle on your computer. Copy `smart_money_tracker/` into your existing `/addons/` share. Replace the source files in `/addons/market_flow_bot/` with the included `market_flow_bot/` folder. Do not copy the outer bundle folder as an add-on.
3. Refresh/check for updates in the Home Assistant add-on store. Install **Smart Money Tracker** from Local add-ons, start it, and enable its sidebar entry.
4. Update/rebuild the existing local **Market Flow Bot** add-on using Home Assistant's add-on controls. Keep the existing add-on installed so its configuration and `/data` are retained. Preserve your existing options.
5. The new Market Flow option defaults to:

   `smart_money_url: "http://local-smart-money-tracker:8098"`

   Smart Money defaults to the reverse link:

   `market_flow_url: "http://local-market-flow-bot:8099"`

   This is the internal DNS name for a locally installed add-on. If installed from a repository rather than Local add-ons, substitute that installation's actual hostname. Do not use a Nabu Casa or ingress browser URL here.
6. Open **Politician Trades**. Collection starts automatically. Then open Market Flow, enter a sector, select a stock, and scroll its details to **Politician trades**.

No external port mapping is required. Access the dashboards through Home Assistant ingress. The internal API relies on the trusted add-on network; do not publish port 8098 to the internet.

## What works now

- Official House PTR index downloaded from the House Clerk, without a paid data service or API key.
- Default disclosure lookback of 365 days; newest reports processed first in small batches. The first backfill may take hours, depending on the backlog and PDF sizes.
- Conservative PDF parsing using transaction-column geometry. Explicit tickers only: company names are never guessed.
- Per-stock purchases, sales, exchanges, reported amount ranges, owner, transaction date, filing date, disclosure delay and original filing link.
- A grouped searchable dashboard: ticker, politician selector, action and disclosure date filters. One politician/disclosure row expands to all its trades and stays open during automatic refreshes.
- Score/date/value sorting plus minimum/maximum estimated-value filters. Value filters can apply to a whole filing or to each individual transaction.
- A clearly labelled midpoint estimate for each bounded transaction range and a summed estimate/range on every collapsed filing row. Open-ended ranges use and identify their minimum.
- Persistent SQLite storage, repeat-download/import deduplication, transactional report replacement, retryable errors and visible incomplete reports.
- Existing cached records remain available if a source is down. Check the last source check and source status before treating coverage as current.
- CSV import for additional House/Senate records. The dashboard provides the template. Dates are YYYY-MM-DD. Quote values containing commas. The entire file is validated before anything is saved.
- A two-way internal link: Market Flow displays grouped disclosures; Smart Money reads cached historical/latest prices from Market Flow. Smart Money runs no second Yahoo downloader.
- Estimated trade-date close, latest price, direction-adjusted performance and a transparent politician Trade Score.
- Full company name, market cap, P/E, price/book, margin, growth and 52-week range in Market Flow's left stock panel.

## Senate connection

Without a key, Senate is explicitly **not configured**; this does not mean no Senate trades occurred.

The optional `fmp_api_key` enables Financial Modeling Prep's `stable/senate-latest` endpoint. The account must have access to that endpoint, which may require a paid plan. No account or subscription is included or purchased. Set the key in Smart Money's configuration and restart that add-on. `senate_pages` controls how many latest pages are collected per check (default 5 x 100 records). This is bounded recent coverage, not a complete historical backfill. The adapter has synthetic tests but has not been authenticated live in this environment.

CSV import works without that service. Imported records stay labelled `csv_import`, separate from official House and FMP records. Importing a record already present under another source can create a visible duplicate; no cross-source trade count or dollar total is calculated.

## Options

| Option | Default | Purpose |
|---|---:|---|
| house_enabled | true | Enable automatic official House collection |
| lookback_days | 365 | House filing-date window when discovering reports; existing records are retained |
| refresh_hours | 6 | Normal source-index/API refresh interval |
| reports_per_cycle | 30 | House PDF batch size; backlog resumes after a short pause |
| fmp_api_key | blank | Optional Senate provider credential |
| senate_pages | 5 | Bounded latest-page Senate collection |
| market_flow_url | http://local-market-flow-bot:8099 | Internal price-cache link to Market Flow |

Manual source checks are limited to once per minute. Failed/partial House reports retry after one day; fully parsed reports are rechecked after seven days as the backlog allows. A restart resumes the same database. Data is in the new add-on's `/data/disclosures.db`, independent of Market Flow's databases.

## Reading the results correctly

- These are public disclosures, which may be published well after the transaction. The app uses the index filing date for House; the PDF's notification date is not substituted for it.
- Amounts are ranges, not exact executed values. Midpoints are displayed only as estimates for comparison and filtering; open-ended ranges use their lower bound and are marked **at least**.
- Transactions can belong to a spouse, joint account or dependent; blank ownership is shown as **Not stated**.
- Stock, option and other asset types remain visible. A ticker match for an option does not mean an outright share purchase.
- No current portfolio or exact profit is inferred. The displayed result uses an estimated market close, and the score is a dataset tracking statistic rather than proof of skill.
- No matching record means none in the collected dataset. It does not establish absence of trading.
- Filings with no explicit ticker, scanned pages and unsupported layouts are incomplete. Parsing is not OCR. Some legitimate non-listed investments therefore appear in the report-review count.
- Amendments are flagged when detectable, but are not reconciled with original disclosures. Check the source PDF.
- The House index can include former members and other filers. The extracted filer status is displayed when available; current elected-office status is not independently verified.
- Only House periodic transaction reports are collected automatically in the free path. Annual holdings, corporate insiders, 13F institutions and 13D/13G filings are future extensions.

## Review and validation

During this build, 21 Python regression tests passed, including two official PDF fixtures. Focused tests also covered midpoint/range totals, both value-filter modes, global score sorting, persistent expansion and the mobile Back guard. Both dashboard scripts pass JavaScript syntax checks, and DOM interaction tests cover the linked stock panel. Browser screenshot validation could not run because the browser binary is not installed in the build environment. A Home Assistant/Docker deployment and authenticated Senate fetch still need testing on your installation.

To roll back the Market Flow UI integration, restore/rebuild the v0.15.3 source folder while retaining the existing add-on data. Smart Money can remain separately installed. No Market Flow DB migration is introduced by this integration.

## Sources

- House index: `https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{YEAR}FD.zip`
- House PTR: `https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{YEAR}/{DocID}.pdf`
- FMP Senate docs: https://site.financialmodelingprep.com/developer/docs/stable/senate-latest
- Home Assistant internal naming: https://developers.home-assistant.io/docs/apps/communication/
