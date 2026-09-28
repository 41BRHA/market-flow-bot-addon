#!/usr/bin/env bash
# HA writes the add-on options to /data/options.json; the app reads them directly.
set -e
cd /opt/bot
exec python -m app.main
