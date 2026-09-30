# Update 0.17.4 / 0.5.4

This package updates both Home Assistant add-ons.

## Changes

- Market Flow economic-calendar times are now parsed from the feed as GMT/UTC
  before being converted to the selected display timezone.
- `display_timezone` is now a dropdown in both add-on configuration pages.
- `Europe/London` remains the default and automatically follows GMT/BST.
- Smart Money Stock Exposure background refreshes no longer collapse a stock
  row while it is open.

## Install

Replace the two add-on folders in the repository with the folders from this
archive, refresh the add-on store, rebuild/update both add-ons, and restart
them. Existing options and collected data under `/data` are retained by Home
Assistant.

After updating, select `Europe/London` from each add-on's Configuration page
and save/restart once. Previously cached calendar entries will be corrected
when Market Flow next refreshes its calendar; the calendar refresh button can
be used to do this immediately.
