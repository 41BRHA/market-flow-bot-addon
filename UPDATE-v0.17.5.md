# Market Flow 0.17.5 / Smart Money 0.6.0

## Main changes

- Universal filing alerts can notify for any politician above a chosen value,
  such as $1,000,000, without selecting every name.
- Current Holdings estimates each selected politician's likely remaining
  positions from the complete collected buy/sell history.
- Disclosure headings sort by politician, date, number of trades, estimated
  value and score; clicking again reverses the order.
- Politician and ticker fields suggest matching collected values as you type.
- Executive Branch OGE collection is enabled by default and includes directly
  downloadable Donald Trump 278-T reports when the official index exposes
  them. Assets without an explicit ticker remain visible in Disclosures but
  cannot be priced or shown in ticker-only views.
- Economic-calendar refresh keeps valid actuals, clears the previously
  incorrect ADP/payroll mapping, uses current BEA GDP/PCE release pages where
  possible, and shows a separate “actuals checked” time after manual refresh.

## Important accuracy notes

Current Holdings is an estimate, not a brokerage statement. Disclosed dollar
ranges, unknown opening balances, partial sales and missing tickers limit its
precision. “Medium” and “Low” confidence labels make these limitations visible.

The free calendar does not publish actual values itself. The add-on fills only
events matched safely to an official government source. Private releases such
as ADP stay blank rather than displaying unrelated payroll data.
