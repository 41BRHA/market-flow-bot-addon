# Stage 1 — selectable politician filing alerts

Smart Money Tracker 0.4.0 adds a **My alerts** tab. Search for a politician,
choose the transaction types and an optional minimum estimated filing value,
switch notifications on, and save. Nobody is selected by default.

The first enable operation baselines records already in the database. Only a
later successfully stored matching filing can alert. One politician/filing
batch produces one combined Home Assistant notification, and its identity is
stored permanently to prevent repeats after restarts or scheduled re-parsing.
Manual CSV imports do not send notifications.

The default service is `notify.adam_mobile`. If that entity has a different
name in Home Assistant, change `notify_service` in the Smart Money Tracker
configuration and restart the add-on. Use **Send test notification** before
selecting people.

Stage 2 will add notable institutional investors using SEC 13F filings. Stage 3
will add corporate-insider Form 4 filings. Those datasets remain separate from
politician scoring because their reporting rules and delays differ.
