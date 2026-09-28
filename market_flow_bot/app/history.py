"""Persistent observation history (SQLite in /data).

Every cycle writes one row per sector, tagged with the US session. Baselines are
then computed from the accumulated history *filtered by session*, so pre-market
is compared against prior pre-market — not against all-day averages. Survives
restarts; /data is included in HA backups.
"""
from __future__ import annotations

import os
import sqlite3
import time

_FIELDS = {"volume", "ret", "ret_recent", "breadth"}  # whitelist for series()


class History:
    def __init__(self, path: str | None = None):
        base = "/data" if os.path.isdir("/data") else os.path.dirname(__file__)
        self.path = path or os.path.join(base, "history.db")
        self.conn = sqlite3.connect(self.path)
        self._init()

    def _init(self) -> None:
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS observations(
                 ts REAL, session TEXT, sector TEXT,
                 ret REAL, ret_recent REAL, volume REAL, breadth REAL, rank INTEGER)"""
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_obs ON observations(sector, session, ts)")
        self.conn.commit()

    def insert(self, ts: float, session: str, rows: list[dict]) -> None:
        self.conn.executemany(
            "INSERT INTO observations VALUES(?,?,?,?,?,?,?,?)",
            [(ts, session, r["sector"], r["ret"], r["ret_recent"],
              r["volume"], r["breadth"], r["rank"]) for r in rows],
        )
        self.conn.commit()

    def series(self, sector: str, session: str, field: str, limit: int = 750) -> list[float]:
        """Most-recent `limit` non-null values of `field` for this sector+session."""
        if field not in _FIELDS:
            raise ValueError(f"bad field {field!r}")
        cur = self.conn.execute(
            f"SELECT {field} FROM observations "
            f"WHERE sector=? AND session=? AND {field} IS NOT NULL "
            f"ORDER BY ts DESC LIMIT ?",
            (sector, session, limit),
        )
        return [row[0] for row in cur.fetchall()]

    def count(self, sector: str, session: str) -> int:
        cur = self.conn.execute(
            "SELECT COUNT(*) FROM observations WHERE sector=? AND session=?",
            (sector, session))
        return int(cur.fetchone()[0])

    def prune(self, max_age_days: int) -> None:
        cutoff = time.time() - max_age_days * 86400
        self.conn.execute("DELETE FROM observations WHERE ts < ?", (cutoff,))
        self.conn.commit()
