"""Local market data storage: Parquet files queried with DuckDB.

Why this and not a database server: Parquet is compact and fast, DuckDB reads it directly with SQL, and
there is nothing to install or keep running. Layout:

    data/bars/<schema>/<ROOT>/<year>.parquet     raw outright bars, one file per product per year
    data/continuous/<ROOT>.parquet               the continuous 1-minute series (rebuilt on demand)

Writes merge with what's already there, so re-downloading a range never creates duplicates.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from tradingdesk.data.providers.base import BAR_COLUMNS, empty_bars, utc


class DataStore:
    def __init__(self, root: str | Path = "data") -> None:
        self.root = Path(root)

    def _bars_dir(self, schema: str, product: str) -> Path:
        return self.root / "bars" / schema / product

    def write_bars(self, df: pd.DataFrame, product: str, schema: str = "ohlcv-1m") -> int:
        if df.empty:
            return 0
        d = self._bars_dir(schema, product)
        d.mkdir(parents=True, exist_ok=True)
        df = df[BAR_COLUMNS].copy()
        written = 0
        for year, part in df.groupby(df["ts"].dt.year):
            path = d / f"{year}.parquet"
            if path.exists():
                part = pd.concat([pd.read_parquet(path), part], ignore_index=True)
            part = part.drop_duplicates(["symbol", "ts"], keep="last").sort_values(["ts", "symbol"])
            part.to_parquet(path, index=False)
            written += len(part)
        return written

    def read_bars(self, product: str, start=None, end=None, schema: str = "ohlcv-1m") -> pd.DataFrame:
        d = self._bars_dir(schema, product)
        files = sorted(d.glob("*.parquet"))
        if not files:
            return empty_bars()
        where, params = [], []
        if start is not None:
            where.append("ts >= ?")
            params.append(utc(start).to_pydatetime())
        if end is not None:
            where.append("ts < ?")
            params.append(utc(end).to_pydatetime())
        sql = f"SELECT * FROM read_parquet({[str(f) for f in files]}) {'WHERE ' + ' AND '.join(where) if where else ''} ORDER BY ts, symbol"
        df = duckdb.execute(sql, params).df()
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
        return df

    def coverage(self, product: str, schema: str = "ohlcv-1m") -> tuple[pd.Timestamp, pd.Timestamp, int] | None:
        files = sorted(self._bars_dir(schema, product).glob("*.parquet"))
        if not files:
            return None
        row = duckdb.execute(f"SELECT min(ts), max(ts), count(*) FROM read_parquet({[str(f) for f in files]})").fetchone()
        return pd.Timestamp(row[0]).tz_convert("UTC"), pd.Timestamp(row[1]).tz_convert("UTC"), int(row[2])

    def write_continuous(self, df: pd.DataFrame, product: str) -> Path:
        p = self.root / "continuous" / f"{product}.parquet"
        p.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(p)
        return p

    def read_continuous(self, product: str) -> pd.DataFrame | None:
        p = self.root / "continuous" / f"{product}.parquet"
        return pd.read_parquet(p) if p.exists() else None
