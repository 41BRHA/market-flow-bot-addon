# Market Flow Bot — configuration (multi-sector)

## What it measures
Every cycle it builds a **relative-strength leaderboard** of all configured
sectors and reads rotation off it. You can't watch money move between sectors
directly, so these are the proxies:

| Signal | Speed | Fires when |
|---|---|---|
| Rotation summary | digest | periodic "into <leader>, out of <laggard>" with top/bottom 3 |
| Rank jump | fast | a sector moves ≥ `rank_jump` places on the board = active rotation |
| Money in/out | fast | short-term move ≥ `flow_return_pct` **and** volume z ≥ `volume_z` |
| Leadership cross | regime | a sector's ratio vs the benchmark (SPY) crosses its `rel_ma_window` MA |

The leaderboard is also written to **`sensor.market_flow_leaderboard`** (state =
current leader; `attributes.leaderboard` = full ranked table) so you can drop it
straight onto a dashboard.

## Sectors shipped
The 11 GICS sector ETFs — Technology (XLK), Financials (XLF), Energy (XLE),
Health Care (XLV), Industrials (XLI), Consumer Discretionary (XLY), Consumer
Staples (XLP), Utilities (XLU), Materials (XLB), Real Estate (XLRE),
Communication Services (XLC) — plus two theme baskets: **AI-Infra** (NVDA, AMD,
AVGO, ANET, VRT, SMCI, MU, SMH, EQIX, DLR) and **Software** (IGV, MSFT, CRM, NOW,
PANW, CRWD, ORCL, ADBE). Benchmark: **SPY**.

A "sector" is just a `{name, symbols}` entry — one ETF or a basket. Edit, add, or
remove freely in the add-on options. Themes overlapping a GICS sector (NVDA sits
in both AI-Infra and Technology) is fine and intended — they're separate lenses.

## Key options
- `data_source` — `yahoo` (free, ~15-min delayed, works now) / `webull` (live, needs key).
- `benchmark` — market reference for leadership crosses (default `[SPY]`).
- `sectors` — the list of `{name, symbols}` baskets to rank.
- `thresholds` — `rel_ma_window`, `recent_bars`, `flow_return_pct`, `volume_z`, `rank_jump`, `breadth_ma_window`.
- `cooldown_minutes` / `summary_cooldown_minutes` — anti-spam; the summary reads as a slower digest.
- `publish_sensor` — write the leaderboard sensor into HA (default on).
- `notify.ha_service` — e.g. `notify.notify` or `notify.mobile_app_yourphone`; HA routes onward.
- `notify.telegram_*` — optional direct Telegram fallback.
- `webull.*` — key/secret/region for the live path.

## Quiet notifications (v0.17.1)

`alerts.quiet_mode` defaults to `true`. It disables routine flow summaries and
enforces conservative minimums even if Home Assistant retained older settings:
0.55 conviction for a flip, 0.75 for strong flow, 1.5x relative volume, 75%
data density and 70% aligned breadth for multi-stock sectors. Stock moves need
10%, sector averages need 3%, signal repeats wait four hours and move repeats
wait eight hours. A rolling cap permits at most four push notifications per
hour. The leaderboard sensor and dashboard continue updating normally.

Set `quiet_mode: false` only if you want every individual threshold and summary
switch to be honoured without those safety floors. Single-stock move alerts use
the metadata cache to show both ticker and full company name.

## Webull live path (scaffolded, not yet tested)
Unchanged from before — see `app/providers/webull.py`. Once your key is issued,
wiring `get_bars()` (HTTP OHLCV) and `stream()` (MQTT live push) makes every
sector update in real time, pre-market included. App-tier L2 depth likely won't
carry into the API (probably institutional); a retail key's real-time L1 + tick +
volume is enough for these cross-sector signals.

## Not tested from the build sandbox
The signal maths, ranking, rank-jump, flow detection, cooldowns, session gating,
and config parsing are unit-tested on synthetic data. Live data *fetch* (Yahoo
and Webull) can't be reached from the sandbox — validate that on first run.

## Sector constituents (v0.3)
Each GICS sector is now populated with its **biggest constituents**, not just the
ETF:
- With `auto_constituents: true` (default), on startup and weekly the add-on
  downloads that sector's SPDR Select Sector ETF **daily holdings** from State
  Street and takes the cap-weighted **top `top_n`** (default 30, set 20–50). That
  is literally the N biggest companies in the sector, kept current automatically.
- If the SSGA fetch/parse is unavailable, it falls back to real **S&P 500 sector
  membership** baked into `app/fallback.py` (accurate names, but not cap-ranked).
- Set `auto_constituents: false` to use each sector's ETF alone (no breadth).
- Theme baskets (AI-Infra, Software) use their explicit `symbols` and ignore this.

`top_n` across ~11 sectors is a few hundred symbols; `poll_interval_seconds`
now defaults to 300s to match the 5-minute bars and stay light on the data source.

**Validate on first run:** the SSGA holdings URL/worksheet layout could not be
tested from the build sandbox. Check the log on start — you'll see either
"N holdings from SSGA (XLK)" per sector (working) or "SSGA fetch failed … using
fallback" (still functional on S&P 500 membership). Tell me what the log says and
I'll adjust the parser if needed.

## Learned history & vigilance (v0.4)
The add-on now keeps a persistent SQLite store (`/data/history.db`) — one row per
sector per cycle, tagged with the US session. Baselines are computed from this
accumulated history **filtered by session**, so:

- **Session-aware volume.** A pre-market bar is judged against prior *pre-market*
  volume, not all-day averages — the same share count that's alarming pre-open is
  ignored mid-session. (Fixes the earlier weakness where pre-market volume never
  cleared the threshold.)
- **Percentile "vigilance" alert.** Fires when a sector's current volume lands in
  the top `percentile_alert` (default 98th) of its own session history — catches
  genuinely unusual activity even when the z-score is borderline.
- **Cold start.** Until a sector/session has `min_history` samples (default 30),
  it falls back to the in-window estimate, so it works from day one and sharpens
  as it learns. At 5-min bars that's ~a week or two of that session accumulating.

Retention is `history_days` (default 180); older rows are pruned daily. `/data`
is included in HA backups, so the learned baselines survive restarts and moves.
The raw table is also there for you to query/chart later if you want.
