from tradingdesk.data.providers.base import HistoricalProvider, ProviderError

__all__ = ["HistoricalProvider", "ProviderError", "make_provider"]


def make_provider(settings):
    """Build the historical data provider named in the settings."""
    if settings.data.provider == "synthetic":
        from tradingdesk.data.providers.synthetic import SyntheticProvider

        return SyntheticProvider()
    from tradingdesk.data.providers.databento_provider import DatabentoProvider

    return DatabentoProvider(dataset=settings.data.dataset)
