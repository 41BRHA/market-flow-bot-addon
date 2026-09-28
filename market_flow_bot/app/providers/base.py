"""Data provider interface.

A provider returns, for each requested symbol, a pandas DataFrame indexed by
timestamp with at least the columns: ``close`` and ``volume``. Bars should be
ordered oldest -> newest and include pre/post-market where the source allows.
"""
from __future__ import annotations

from typing import Protocol

import pandas as pd


class Provider(Protocol):
    name: str

    def get_bars(self, symbols: list[str], interval: str, lookback: str) -> dict[str, pd.DataFrame]:
        """Return {symbol: DataFrame[close, volume]} for the lookback window."""
        ...
