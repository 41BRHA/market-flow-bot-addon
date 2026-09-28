"""Webull OpenAPI provider — SCAFFOLD.

Status: this path is scaffolded but NOT end-to-end tested. It needs a real
Webull developer key (app_key/app_secret from developer.webull.com) and a
2FA approval on your phone, neither of which can be exercised from a build
sandbox. Wire + test this once your key is issued.

Two integration surfaces on the Webull OpenAPI:
  * HTTP Market Data  -> snapshots, historical OHLCV, tick, depth (used by
    get_bars() below to stay drop-in compatible with the Yahoo provider).
  * MQTT streaming    -> the real upgrade. A persistent subscription pushes
    live quotes/ticks the instant they print, including pre-market — which is
    exactly the lag you hit on delayed data. See stream() for where to hook it.

Note on L2: the 50-level order-book depth is entitlement-gated (likely an
institutional tier). The Level 2 you pay for *in the app* is a display
entitlement and may not carry into the API. On a retail key expect real-time
L1 + tick + volume, which is enough for cross-basket rotation signals.
"""
from __future__ import annotations

import logging

import pandas as pd

log = logging.getLogger("provider.webull")


class WebullProvider:
    name = "webull"

    def __init__(self, cfg):
        self.cfg = cfg
        self._client = None
        if not (cfg.app_key and cfg.app_secret):
            raise RuntimeError(
                "Webull selected but app_key/app_secret are empty. "
                "Add them in the add-on config, or switch data_source to 'yahoo'."
            )

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        try:
            # Official SDK. Add the exact package to requirements.txt once you
            # confirm the module name from Webull's SDK download for your region.
            from webullsdkmdata.quotes.client import Client  # type: ignore
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "Webull SDK not installed. Add it to requirements.txt and rebuild. "
                f"Import error: {exc}"
            )
        # TODO: initialise with app_key/app_secret + region, run the auth/2FA
        # handshake, cache the 15-day token. Left explicit rather than guessed.
        raise NotImplementedError(
            "Webull client init not wired yet — fill in once your API key is issued."
        )

    def get_bars(self, symbols: list[str], interval: str = "5m", lookback: str = "5d") -> dict[str, pd.DataFrame]:
        """Return {symbol: DataFrame[close, volume]} — same shape as YahooProvider.

        Implement using the HTTP Market Data OHLCV endpoint. Map the response
        rows to a DataFrame indexed by timestamp with 'close' and 'volume'.
        """
        self._ensure_client()
        raise NotImplementedError("Map Webull OHLCV response to DataFrame here.")

    # --- live path (preferred) --------------------------------------------
    def stream(self, symbols: list[str], on_tick):
        """Subscribe to live quotes/ticks over MQTT and call on_tick(symbol, tick).

        This replaces polling: the anomaly engine reacts the moment a bar prints,
        pre-market included. Hook paho-mqtt (already in requirements) to Webull's
        MQTT broker using the credentials/topics from the SDK docs.
        """
        raise NotImplementedError("Wire Webull MQTT subscription here.")
