import pandas as pd
import pytest

from tests.conftest import raw_bars
from tradingdesk.core.bus import EventBus
from tradingdesk.core.events import DataQualityIssue, DataUpdated, Decision
from tradingdesk.data.agent import CostLimitExceeded, DataAgent
from tradingdesk.data.providers.base import HistoricalProvider
from tradingdesk.data.providers.databento_provider import DatabentoProvider
from tradingdesk.data.store import DataStore


class FakeProvider(HistoricalProvider):
    def __init__(self, cost=0.0, rows=None):
        self.cost, self.rows, self.fetched = cost, rows, False

    def estimate_cost(self, root, start, end, schema):
        return self.cost

    def fetch_bars(self, root, start, end, schema="ohlcv-1m"):
        self.fetched = True
        return raw_bars(self.rows)


def _rows():
    ts = pd.date_range("2024-03-05 14:30", periods=30, freq="1min", tz="UTC")
    rows = [(t, "MESH4", 5000.0, 5000.5, 4999.5, 5000.0, 100) for t in ts]
    rows.append(rows[3])  # a duplicate to be reported
    return rows


def test_download_refused_when_too_expensive(settings, tmp_path):
    bus = EventBus()
    prov = FakeProvider(cost=12.5, rows=_rows())
    agent = DataAgent(settings, prov, DataStore(tmp_path), bus)
    with pytest.raises(CostLimitExceeded):
        agent.download("MES", "2024-03-01", "2024-03-06")
    assert not prov.fetched
    assert any(d.action == "download_refused" for d in bus.of_type(Decision))


def test_download_cleans_stores_and_reports(settings, tmp_path):
    bus = EventBus()
    agent = DataAgent(settings, FakeProvider(rows=_rows()), DataStore(tmp_path), bus)
    rep = agent.download("MES", "2024-03-01", "2024-03-06")
    assert rep.rows_out == 30
    assert any(i.check == "duplicates" for i in bus.of_type(DataQualityIssue))
    assert bus.of_type(DataUpdated)[0].rows == 30
    assert len(agent.bars("MES", "5m")) == 6


class _Store:
    def __init__(self, df):
        self.df = df

    def to_df(self):
        return self.df


class _Client:
    def __init__(self):
        self.calls = []
        idx = pd.DatetimeIndex(["2024-03-05 14:30", "2024-03-05 14:31"], tz="UTC", name="ts_event")
        self.df = pd.DataFrame({"rtype": 33, "instrument_id": [1, 2], "open": [5000.0, 1.0], "high": [5001.0, 1.0], "low": [4999.0, 1.0],
                                "close": [5000.5, 1.0], "volume": [10, 3], "symbol": ["MESH4", "MESH4-MESM4"]}, index=idx)

        class TS:
            def get_range(inner, **kw):
                self.calls.append(kw)
                return _Store(self.df)

        class Meta:
            def get_cost(inner, **kw):
                return 0.42

        self.timeseries, self.metadata = TS(), Meta()


def test_databento_requests_all_outrights_and_drops_spreads():
    client = _Client()
    p = DatabentoProvider(client=client)
    assert p.estimate_cost("MES", "2024-03-01", "2024-03-06") == 0.42
    df = p.fetch_bars("MES", "2024-03-05", "2024-03-06")
    assert list(df["symbol"]) == ["MESH4"]
    assert client.calls[0]["symbols"] == ["MES.FUT"] and client.calls[0]["stype_in"] == "parent"
    assert client.calls[0]["dataset"] == "GLBX.MDP3"


def test_databento_needs_a_key(monkeypatch):
    monkeypatch.delenv("DATABENTO_API_KEY", raising=False)
    from tradingdesk.data.providers import ProviderError

    with pytest.raises(ProviderError, match="DATABENTO_API_KEY"):
        _ = DatabentoProvider().client
