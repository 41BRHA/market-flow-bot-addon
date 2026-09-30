# Stage 2.1 — Display timezones

Both add-ons now default to:

`display_timezone: "Europe/London"`

This follows UK daylight saving automatically: GMT in winter and BST in
summer. To use another zone, enter its IANA name in each add-on configuration,
for example `Europe/Warsaw`, `America/New_York` or `Asia/Tokyo`, then restart
that add-on.

UTC remains the database and API storage format. Only presentation changes,
so timezone changes cannot shift historical calculations or filing dates.
