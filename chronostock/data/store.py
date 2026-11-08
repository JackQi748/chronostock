"""Local analytical warehouse: DuckDB SQL engine + Parquet persistence.

Architecture note
-----------------
Durable state lives in a portable Parquet file; DuckDB runs as an embedded
in-process SQL engine over that state. This avoids file locks / WAL cleanup,
works fully offline, and makes a snapshot literally one file you can diff or
archive. Every bar keeps its provenance (``source``) and ingest timestamp so a
run can be audited later.

On every write the in-memory tables are flushed back to Parquet. The store is
therefore process-local: run one CLI command or one web process at a time, and
use ``export_snapshot`` / ``import_snapshot`` to move data between machines.
"""

from __future__ import annotations

import hashlib
import warnings
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from chronostock.config import warehouse_path
from chronostock.data.sources import BAR_COLUMNS

STORE_COLUMNS = BAR_COLUMNS + ["ingested_at"]
SYNC_COLUMNS = ["symbol", "source", "bar_count", "first_date", "last_date", "synced_at", "notes"]

_DDL_BARS = """
CREATE TABLE bars (
    symbol      VARCHAR NOT NULL,
    date        DATE    NOT NULL,
    open        DOUBLE,
    high        DOUBLE,
    low         DOUBLE,
    close       DOUBLE,
    volume      DOUBLE,
    source      VARCHAR,
    ingested_at TIMESTAMP,
    PRIMARY KEY (symbol, date)
)
"""
_DDL_SYNC = """
CREATE TABLE sync_runs (
    symbol      VARCHAR,
    source      VARCHAR,
    bar_count   BIGINT,
    first_date  DATE,
    last_date   DATE,
    synced_at   TIMESTAMP,
    notes       VARCHAR
)
"""


def empty_bars() -> pl.DataFrame:
    """Empty frame with the canonical bar schema."""
    return pl.DataFrame(
        schema={
            "symbol": pl.Utf8,
            "date": pl.Date,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Float64,
            "source": pl.Utf8,
            "ingested_at": pl.Datetime("us"),
        }
    )


def empty_syncs() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "symbol": pl.Utf8,
            "source": pl.Utf8,
            "bar_count": pl.Int64,
            "first_date": pl.Date,
            "last_date": pl.Date,
            "synced_at": pl.Datetime("us"),
            "notes": pl.Utf8,
        }
    )


def fingerprint(bars: pl.DataFrame) -> str:
    """Short, stable hash of the exact input rows used by a run."""
    payload = "|".join(
        f"{r['symbol']},{r['date']},{r['open']:.6f},{r['high']:.6f},"
        f"{r['low']:.6f},{r['close']:.6f},{r['volume']:.2f},{r['source']}"
        for r in bars.iter_rows(named=True)
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


class MarketStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or warehouse_path()).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.sync_path = self.path.with_name(self.path.stem + ".sync.parquet")
        self._con = duckdb.connect(":memory:")
        self._run(_DDL_BARS)
        self._run(_DDL_SYNC)
        self._load()

    # ------------------------------------------------------------ duckdb helpers
    def _run(self, sql: str, params: list[Any] | None = None) -> None:
        """Execute and fully consume the result (DuckDB 2.x requires this)."""
        rel = self._con.execute(sql) if params is None else self._con.execute(sql, params)
        rel.fetchall()

    def _rows(self, sql: str, params: list[Any] | None = None) -> list[tuple[Any, ...]]:
        rel = self._con.execute(sql) if params is None else self._con.execute(sql, params)
        return rel.fetchall()

    def _frame(self, sql: str, params: list[Any] | None = None) -> pl.DataFrame:
        rel = self._con.execute(sql) if params is None else self._con.execute(sql, params)
        # DuckDB 1.x/2.x differ: 2.x returns the connection and renamed the
        # Arrow accessor. Try each supported shape explicitly.
        if hasattr(rel, "to_arrow_table"):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                return pl.from_arrow(rel.to_arrow_table())
        if hasattr(rel, "fetch_arrow_table"):
            return pl.from_arrow(rel.fetch_arrow_table())
        return rel.pl()

    def close(self) -> None:
        self._con.close()

    # ------------------------------------------------------------- persistence
    def _load(self) -> None:
        if self.path.exists():
            df = pl.read_parquet(self.path)
            if not df.is_empty():
                self._insert(self._normalise(df), persist=False)
        if self.sync_path.exists():
            syncs = pl.read_parquet(self.sync_path)
            if not syncs.is_empty():
                self._con.register("_sync_in", syncs.to_arrow())
                self._run("INSERT INTO sync_runs SELECT symbol, source, bar_count, first_date, "
                          "last_date, synced_at, notes FROM _sync_in")
                self._con.unregister("_sync_in")

    def _persist(self) -> None:
        self._frame("SELECT * FROM bars ORDER BY symbol, date").write_parquet(self.path)
        syncs = self._frame("SELECT * FROM sync_runs ORDER BY synced_at DESC")
        syncs.write_parquet(self.sync_path)

    # ------------------------------------------------------------------- write
    def upsert(self, bars: pl.DataFrame) -> int:
        """Idempotent upsert keyed by (symbol, date). Returns affected row count."""
        if bars.is_empty():
            return 0
        return self._insert(self._normalise(bars), persist=True)

    def _insert(self, df: pl.DataFrame, persist: bool) -> int:
        self._con.register("_incoming", df.to_arrow())
        columns = ", ".join(STORE_COLUMNS)
        try:
            self._run(f"INSERT OR REPLACE INTO bars SELECT {columns} FROM _incoming")
        except duckdb.Error:
            self._run(
                "DELETE FROM bars USING _incoming "
                "WHERE bars.symbol = _incoming.symbol AND bars.date = _incoming.date"
            )
            self._run(f"INSERT INTO bars SELECT {columns} FROM _incoming")
        finally:
            self._con.unregister("_incoming")
        if persist:
            self._persist()
        return df.height

    @staticmethod
    def _normalise(bars: pl.DataFrame) -> pl.DataFrame:
        df = bars.clone()
        if "ingested_at" not in df.columns:
            df = df.with_columns(pl.lit(datetime.now()).alias("ingested_at"))
        if "source" not in df.columns:
            df = df.with_columns(pl.lit("unknown").alias("source"))
        if "symbol" not in df.columns:
            raise ValueError("行情数据缺少 symbol 列")
        df = df.with_columns(
            pl.col("date").cast(pl.Date),
            pl.col("open").cast(pl.Float64),
            pl.col("high").cast(pl.Float64),
            pl.col("low").cast(pl.Float64),
            pl.col("close").cast(pl.Float64),
            pl.col("volume").cast(pl.Float64),
            pl.col("source").cast(pl.Utf8),
            pl.col("symbol").cast(pl.Utf8),
            pl.col("ingested_at").cast(pl.Datetime("us")),
        )
        return df.select(STORE_COLUMNS)

    def log_sync(
        self, symbol: str, source: str, count: int, first: date, last: date, notes: str = ""
    ) -> None:
        self._run(
            "INSERT INTO sync_runs (symbol, source, bar_count, first_date, last_date, synced_at, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [symbol, source, count, first, last, datetime.now(), notes],
        )
        self._persist()

    # -------------------------------------------------------------------- read
    def load_bars(
        self,
        symbol: str,
        start: str | date | None = None,
        end: str | date | None = None,
        as_of: str | date | None = None,
        vintage: bool = False,
    ) -> pl.DataFrame:
        """Point-in-time read: ``as_of`` truncates the series to that trading day.

        ``vintage=True`` additionally hides rows ingested after ``as_of`` (a true
        as-known-then view); it is off by default because a snapshot imported
        today is still legitimate data for replaying an older date.
        """
        sql = "SELECT * FROM bars WHERE symbol = ?"
        params: list[Any] = [symbol]
        if start:
            sql += " AND date >= ?"
            params.append(_d(start))
        if end:
            sql += " AND date <= ?"
            params.append(_d(end))
        if as_of:
            sql += " AND date <= ?"
            params.append(_d(as_of))
            if vintage:
                sql += " AND CAST(ingested_at AS DATE) <= ?"
                params.append(_d(as_of))
        sql += " ORDER BY date"
        return self._frame(sql, params)

    def list_symbols(self) -> list[dict[str, Any]]:
        rows = self._rows(
            "SELECT symbol, COUNT(*) AS bars, MIN(date) AS first_date, MAX(date) AS last_date, "
            "MIN(source) AS source FROM bars GROUP BY symbol ORDER BY symbol"
        )
        return [
            {"symbol": r[0], "bars": int(r[1]), "first_date": str(r[2]),
             "last_date": str(r[3]), "source": r[4]}
            for r in rows
        ]

    def coverage(self, symbol: str) -> dict[str, Any] | None:
        rows = self._rows(
            "SELECT COUNT(*), MIN(date), MAX(date), MIN(source) FROM bars WHERE symbol = ?",
            [symbol],
        )
        row = rows[0] if rows else None
        if not row or not row[0]:
            return None
        return {"bars": int(row[0]), "first": row[1], "last": row[2], "source": row[3]}

    def stats(self) -> dict[str, int]:
        bars = self._rows("SELECT COUNT(*) FROM bars")[0][0]
        symbols = self._rows("SELECT COUNT(DISTINCT symbol) FROM bars")[0][0]
        return {"bars": int(bars), "symbols": int(symbols)}

    def recent_syncs(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._rows(
            "SELECT symbol, source, bar_count, first_date, last_date, synced_at, notes "
            "FROM sync_runs ORDER BY synced_at DESC, symbol DESC LIMIT ?",
            [limit],
        )
        return [
            {"symbol": r[0], "source": r[1], "bars": int(r[2]),
             "first_date": str(r[3]), "last_date": str(r[4]),
             "synced_at": str(r[5]), "notes": r[6]}
            for r in rows
        ]

    # --------------------------------------------------------------- snapshots
    def export_snapshot(self, path: str | Path) -> Path:
        """Write the whole warehouse to a portable Parquet snapshot."""
        out = Path(path).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        self._frame("SELECT * FROM bars ORDER BY symbol, date").write_parquet(out)
        return out

    def import_snapshot(self, path: str | Path) -> int:
        p = Path(path).expanduser()
        if not p.exists():
            raise FileNotFoundError(p)
        df = pl.read_parquet(p)
        if not df.is_empty():
            self._insert(self._normalise(df), persist=True)
        return self.stats()["bars"]


def _d(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def overlap_start(last: date, lookback_days: int = 7) -> date:
    """Refetch a small overlap so split/dividend adjustments can be reconciled."""
    return last - timedelta(days=lookback_days)
