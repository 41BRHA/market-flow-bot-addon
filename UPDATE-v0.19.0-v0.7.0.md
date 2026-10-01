# Market Flow 0.19.0 + Smart Money 0.7.0

Upload both add-on folders from this package over the corresponding local add-on
folders, reload the Home Assistant add-on store, then rebuild/restart both add-ons.

Market Flow now contains **All Stock Scores** and **Paper Accounts** tabs. Paper
accounts are simulations only and contain no broker integration. Smart Money adds
stable Stock Exposure controls and estimated Current Holdings performance fields.

Existing databases are retained. The new `paper.db` is created automatically in
Market Flow's `/data` directory and is included in Home Assistant backups.
