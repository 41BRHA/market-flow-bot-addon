# Market Flow Bot (Home Assistant add-on)

Displayed times default to `Europe/London` and automatically follow GMT/BST.
Use the `display_timezone` add-on option for another IANA timezone.

Watches an AI-infrastructure basket against a software basket and alerts you when
capital is rotating between them — a ratio break, a basket divergence, a volume
spike, or a breadth split. Alerts are pushed into Home Assistant, which fans them
out to whatever notifiers you already have (mobile app, Telegram, email…).

**Runs today on free delayed data (Yahoo).** A Webull live path (MQTT) is
scaffolded for real-time pre-market alerts once you add an API key — see DOCS.md.

## Install (local add-on)
1. Copy the `market_flow_bot` folder into the `/addons` share on your HA host
   (Samba/SSH add-on → the `addons` folder).
2. Settings → Add-ons → Add-on Store → ⋮ → **Check for updates**.
3. Open **Market Flow Bot** under *Local add-ons* → **Install** (first build
   pulls Python + pandas, a few minutes).
4. Set options (baskets, thresholds, notify service), **Start**, watch the log.

Not on HAOS/Supervised? The `app/` folder + a tiny `docker-compose.yml` runs the
same thing as a plain container — ask and I'll add the compose file.
