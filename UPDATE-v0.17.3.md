# Update 0.17.3 / Smart Money 0.5.2

## Intermittent politician-card fix

- Market Flow identifies its internal requests to Smart Money.
- Smart Money serves those requests from its price cache instead of calling
  back into Market Flow and creating a circular wait.
- The last-known working internal hostname is attempted first.
- A temporary timeout preserves the last successful stock disclosure result
  and retries shortly instead of replacing the panel with an error.

## Display timezone

- Both dashboards default to `Europe/London` and automatically follow GMT/BST.
- `display_timezone` accepts another IANA name in each add-on configuration.
- UTC remains the internal database format.

Replace and rebuild both add-ons. Existing `/data` databases and Home Assistant
options are retained.
