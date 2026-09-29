# Smart Money Tracker 0.2.1

Politician-trade disclosures with a separate persistent store, automatic official House PTR collection, optional FMP Senate ingestion, CSV import and a searchable ingress dashboard. Designed to connect to Market Flow's stock detail panel through a read-only HTTP API.

See DOCS.md or START-HERE.md in the bundle for installation, coverage and limitations.

The dashboard groups all trades from one politician on one disclosure date into
an expandable row. Filters include a ticker, a collected-politician selector,
transaction type and disclosure date.

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
