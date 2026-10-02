# Market Flow 0.21.1 / Smart Money 0.9.1

This release includes the performance and calculation corrections from the previous package and repairs economic-calendar actual values.

- Removes an accidentally appended second JavaScript payload that mobile browsers displayed as raw source beneath the Market Flow interface.
- Adds a packaging regression check so the Flow Map must contain exactly one complete HTML document with nothing after its closing tag.

- Employment releases now support Non-Farm Payrolls, Unemployment Rate and Average Hourly Earnings from official BLS series.
- Factory Orders uses the Census total-manufacturing-new-orders series distributed by FRED.
- CPI, PCE, employment and GDP values are accepted only when the source period matches the month or quarter represented by the scheduled release. An old observation can no longer be shown as today's actual.
- Government responses are cached for 15 minutes to stay within public API limits while the calendar continues to retry blank released figures.
- Before release, Actual displays `Scheduled`. Supported released events display `Checking…` until the matching official observation appears. Events such as speeches and private ADP reports display `No auto source` because there is no supported free official numeric feed.
- Manual refresh still triggers an immediate calendar and actual-value check.

At 08:01 UK time on 2 October, the 13:30 employment releases and 15:00 Factory Orders shown in the screenshot have not been published, so they should display `Scheduled`. Speeches do not have numeric actual values.
