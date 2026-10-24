"""The Data Agent: downloads, checks, stores and serves market data.

    download()    get raw contract bars from the provider (after a cost check), clean them, store them
    continuous()  build the rolled continuous 1-minute series and flag gaps
    bars()        continuous bars in any timeframe (1m, 5m, 15m, 1h, 1d)

Every problem it finds is published as a DataQualityIssue so the Watchdog (Phase 3) can react, and every
choice it makes (download refused for cost, roll dates, ...) is published as a Decision.
"""

from __future__ import annotations

import logging

import pandas as pd

from tradingdesk.config import Settings
from tradingdesk.core.bus import EventBus
from tradingdesk.core.events import DataUpdated, Decision
from tradingdesk.data.providers import HistoricalProvider, make_provider
from tradingdesk.data.providers.base import utc
from tradingdesk.data.resample import resample
from tradingdesk.data.rollover import build_continuous
from tradingdesk.data.store import DataStore
from tradingdesk.data.validate import ValidationReport, clean_bars, find_gaps

log = logging.getLogger(__name__)


class CostLimitExceeded(RuntimeError):
    pass


class DataAgent:
    name = "data"

    def __init__(self, settings: Settings, provider: HistoricalProvider | None = None, store: DataStore | None = None, bus: EventBus | None = None) -> None:
        self.settings = settings
        self.provider = provider or make_provider(settings)
        self.store = store or DataStore(settings.data.store_path)
        self.bus = bus or EventBus()

    def _decide(self, action: str, reason: str, symbol: str | None = None, **details) -> None:
        log.info("%s: %s", action, reason)
        self.bus.publish(Decision(agent=self.name, action=action, reason=reason, symbol=symbol, details=details))

    def _publish(self, rep: ValidationReport) -> None:
        for issue in rep.issues:
            self.bus.publish(issue)

    def estimate_cost(self, root: str, start, end, schema: str | None = None) -> float:
        return self.provider.estimate_cost(root, utc(start), utc(end), schema or self.settings.data.default_schema)

    def download(self, root: str, start, end, schema: str | None = None, max_cost: float | None = None) -> ValidationReport:
        schema = schema or self.settings.data.default_schema
        limit = self.settings.data.max_cost_usd_per_download if max_cost is None else max_cost
        start, end = utc(start), utc(end)
        cost = self.provider.estimate_cost(root, start, end, schema)
        if cost > limit:
            msg = f"{root} {schema} {start.date()} to {end.date()} would cost ${cost:.2f}, above the ${limit:.2f} limit. Nothing was downloaded."
            self._decide("download_refused", msg, root, cost=cost, limit=limit)
            raise CostLimitExceeded(msg)
        self._decide("download", f"Downloading {root} {schema} from {start.date()} to {end.date()} (estimated cost ${cost:.2f}).", root, cost=cost)
        raw = self.provider.fetch_bars(root, start, end, schema)
        clean, rep = clean_bars(raw, root)
        self._publish(rep)
        stored_schema = "ohlcv-1m"  # tick data is turned into 1-minute bars by the provider
        self.store.write_bars(clean, root, stored_schema)
        self.bus.publish(DataUpdated(symbol=root, schema=stored_schema, rows=len(clean),
                                     start=clean["ts"].min() if len(clean) else None, end=clean["ts"].max() if len(clean) else None))
        log.info(rep.summary())
        return rep

    def continuous(self, root: str, start=None, end=None, rebuild: bool = False) -> pd.DataFrame:
        cont = None if rebuild else self.store.read_continuous(root)
        if cont is None:
            raw = self.store.read_bars(root)
            if raw.empty:
                raise LookupError(f"No stored data for {root}. Run a download first.")
            cont = build_continuous(raw, self.settings.data.roll.confirm_days)
            rep = ValidationReport(root=root, rows_in=len(cont), rows_out=len(cont))
            find_gaps(cont, root, rep)
            self._publish(rep)
            rolls = cont[cont["roll"]]
            for ts, row in rolls.iterrows():
                self._decide("roll", f"Rolled {root} to {row['contract']} on {row['trade_date']:%Y-%m-%d} when it became the most traded contract.", root, ts=str(ts))
            self.store.write_continuous(cont, root)
        if start is not None:
            cont = cont[cont.index >= utc(start)]
        if end is not None:
            cont = cont[cont.index < utc(end)]
        return cont

    def bars(self, root: str, timeframe: str = "1m", start=None, end=None) -> pd.DataFrame:
        return resample(self.continuous(root, start, end), timeframe)
