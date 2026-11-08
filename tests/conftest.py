"""Shared fixtures.

The sandbox used for local development blocks deletion of temp probe files, so
we pin ``tempfile.tempdir`` to a project-local directory up front. This keeps
pytest's ``tmp_path`` fixture usable without touching system temp dirs.
"""

from __future__ import annotations

import os
import tempfile
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

_WORK = Path(__file__).resolve().parent / "_work"
_WORK.mkdir(exist_ok=True)
tempfile.tempdir = str(_WORK)
os.environ.setdefault("TMPDIR", str(_WORK))

from chronostock.data.sources import fetch_bars  # noqa: E402
from chronostock.data.store import MarketStore  # noqa: E402


@pytest.fixture(scope="session")
def demo_bars():
    return fetch_bars("AAPL", "2019-01-01", "2023-12-31", source="synthetic").bars


@pytest.fixture
def store(tmp_path, demo_bars, monkeypatch):
    path = tmp_path / "warehouse.parquet"
    monkeypatch.setenv("CHRONOSTOCK_DB", str(path))
    s = MarketStore(path)
    s.upsert(demo_bars)
    yield s
    s.close()


@pytest.fixture
def api_client(tmp_path, demo_bars, monkeypatch):
    import importlib

    from fastapi.testclient import TestClient

    app_module = importlib.import_module("chronostock.api.app")
    path = tmp_path / "api.parquet"
    monkeypatch.setenv("CHRONOSTOCK_DB", str(path))
    app_module._STORES.clear()
    app_module.get_store().upsert(demo_bars)
    client = TestClient(app_module.app)
    yield client
    app_module._STORES.clear()


def constant_bars(n: int = 160, price: float = 100.0) -> pl.DataFrame:
    """Flat OHLCV series - useful for zero-return / no-cost engine assertions."""
    dates = (
        pl.DataFrame({
            "date": pl.date_range(date(2020, 1, 1), date(2020, 1, 1) + timedelta(days=n * 2), "1d", eager=True)
        })
        .filter(pl.col("date").dt.weekday() <= 5)
        .head(n)
        .to_series()
        .to_list()
    )
    return pl.DataFrame(
        {
            "date": dates,
            "open": [price] * len(dates),
            "high": [price] * len(dates),
            "low": [price] * len(dates),
            "close": [price] * len(dates),
            "volume": [1000.0] * len(dates),
            "source": ["synthetic"] * len(dates),
            "symbol": ["TEST"] * len(dates),
        },
        schema={
            "date": pl.Date, "open": pl.Float64, "high": pl.Float64, "low": pl.Float64,
            "close": pl.Float64, "volume": pl.Float64, "source": pl.Utf8, "symbol": pl.Utf8,
        },
    )
