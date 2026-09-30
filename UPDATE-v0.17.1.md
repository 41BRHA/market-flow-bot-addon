# Update 0.17.1 / Smart Money 0.3.0

## Quieter notifications

- Quiet mode is enabled by default and applies conservative floors to older retained Home Assistant options.
- Routine flow summaries are off in quiet mode.
- Flow alerts require at least 0.55 flip / 0.75 strong conviction, 1.5x relative volume, 75% data density and 70% aligned sector breadth.
- Signal repeats wait at least four hours; stock and sector move repeats wait eight hours.
- At most four notifications are sent in any rolling hour.
- Single-stock notifications include the full company name when the metadata service can resolve it.

## Politician-stock views from 0.17.0

- **Stock Exposure** summarises every collected ticker across politician disclosures.
- **Politician Flow Map** groups estimated disclosed Buy/Sell flow by sector.
- Clicking a sector opens the filtered stock list; clicking a stock opens its politicians and transactions.
- Exposure includes estimated values, current price, valuation data and nearest-expiry max pain.

Update/rebuild Market Flow. Smart Money remains version 0.3.0 and does not need
rebuilding solely for this notification refinement. Existing `/data` databases
and Home Assistant options are retained.
