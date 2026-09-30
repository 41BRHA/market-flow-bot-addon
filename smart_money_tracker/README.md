# Smart Money Tracker 0.5.3

Politician-trade disclosures with a separate persistent store, automatic official House PTR collection, optional FMP Senate ingestion, CSV import and a searchable ingress dashboard. Designed to connect to Market Flow's stock detail panel through a read-only HTTP API.

See DOCS.md or START-HERE.md in the bundle for installation, coverage and limitations.

## Selectable new-filing notifications

Open **My alerts** to search the collected politician list and opt in person by
person. Nobody is selected automatically. Each person can be limited to Buys,
Sells, both, or every transaction type, with an optional minimum estimated
filing value. One disclosure creates one combined notification rather than a
push for every line item.

Notifications begin from the day a person is enabled. Existing records are
baselined, manual CSV imports do not alert, and delivered filing identities are
stored in `/data/disclosures.db` so restarts and routine re-parsing cannot send
the same alert again. Set `notify_service` to the same working Home Assistant
`notify.*` service used for Market Flow, then use **Send test notification**.

Displayed operational timestamps default to `Europe/London` and automatically
switch between GMT and BST. Set `display_timezone` to another IANA timezone
such as `Europe/Warsaw` or `America/New_York` in the add-on configuration.

The dashboard groups all trades from one politician on one disclosure date into
an expandable row. It can sort by politician score or estimated filing value,
and filter estimated value either by whole filing or individual transaction.
Expanded rows stay open during the automatic dashboard refresh.

Estimated transaction values use the midpoint of the publicly reported range.
Filing values sum those estimates; open-ended ranges use the disclosed minimum
and are visibly marked. These values are comparison aids, not exact position
sizes.

When Market Flow is running, Smart Money asks its internal API for the daily
close on the disclosed transaction date (or next trading session) and the most
recent cached price. This is an estimate because public filings normally report
an amount range and date, not the actual execution price.

The Trade Score is 0–100 with 50 neutral. Buy returns count positively when the
price rises; Sell returns count positively when the price falls. The score
combines equal-weight average direction-adjusted return and win rate, caps
extreme returns at ±50%, and shrinks small samples toward 50 by `n/(n+5)`.

Current roles and committee assignments are refreshed daily from the official
House Clerk and Senate XML feeds. The dashboard translates major committees
into short oversight descriptions such as defence, banking, energy or
technology. Committee membership is useful context, but is not evidence that a
member had advance knowledge of a particular trade.

The Stock Exposure tab aggregates every collected ticker into estimated Buy,
Sell, gross and net disclosed flow. It supports sector, industry, politician,
chamber, transaction, value, participation and score filters. This is an
inference from collected transactions rather than a verified holdings ledger.

The Politician Flow Map groups those estimates by sector. Market Flow supplies
cache-first current prices, company metadata and nearest-expiry option max pain.
This politician-only market data is queued approximately every eight hours
rather than joining the normal 15-minute flow cycle.

## Notable investors / SEC 13F

The separate **Notable investors** view follows a small curated group of public
13F filers, including Berkshire Hathaway, Pershing Square, Scion, Bridgewater
and Duquesne. It compares reported share counts with the manager's previous
quarter and labels positions New, Increased, Reduced, Exited or Unchanged.

13F is a delayed holdings snapshot, not a transaction feed: reports can be
filed up to 45 days after quarter-end and do not reveal exact trade dates or
prices. The official information table does not provide a dependable ticker,
so the tracker displays issuer, class and CUSIP and never guesses a symbol.

SEC requests require a descriptive user agent with contact information. Set
`sec_user_agent` in add-on configuration, for example
`Adam Smart Money Tracker adam@example.com`. Collection then runs at startup
and approximately every eight hours. Per-manager notifications are off by
default, can filter change types and minimum position value, baseline existing
filings, and send one combined alert per new matching 13F.
