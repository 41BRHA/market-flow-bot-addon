from .yahoo import YahooProvider

__all__ = ["YahooProvider", "make_provider"]


def make_provider(cfg):
    """Factory: pick a provider from config. Falls back to Yahoo."""
    if cfg.data_source == "webull":
        from .webull import WebullProvider  # imported lazily; SDK optional
        return WebullProvider(cfg.webull)
    return YahooProvider()
