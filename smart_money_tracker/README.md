# Smart Money Tracker 0.3.0

Politician-trade disclosures with a separate persistent store, automatic official House PTR collection, optional FMP Senate ingestion, CSV import and a searchable ingress dashboard. Designed to connect to Market Flow's stock detail panel through a read-only HTTP API.

See DOCS.md or START-HERE.md in the bundle for installation, coverage and limitations.

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
