"""Warehouse: idempotent upsert, point-in-time reads, snapshots, persistence."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from chronostock.data.store import MarketStore, fingerprint
from chronostock.data.sources import normalize_symbol


def test_symbol_normalisation():
    assert normalize_symbol("sh600519") == "600519.SH"
    assert normalize_symbol("HK700") == "00700.HK"
    assert normalize_symbol("600519.ss") == "600519.SH"
    assert normalize_symbol("aapl") == "AAPL"


def test_upsert_is_idempotent(store, demo_bars):
    first = store.stats()["bars"]
    store.upsert(demo_bars)
    assert store.stats()["bars"] == first
    assert store.coverage("AAPL")["bars"] == first
    assert store.list_symbols()[0]["symbol"] == "AAPL"


def test_point_in_time_read(store):
    all_bars = store.load_bars("AAPL")
    replay = store.load_bars("AAPL", as_of="2021-06-30")
    assert replay.height < all_bars.height
    assert str(replay["date"][-1]) <= "2021-06-30"
    assert str(replay["date"][0]) == str(all_bars["date"][0])


def test_start_end_filter(store):
    df = store.load_bars("AAPL", start="2021-01-01", end="2021-12-31")
    assert df.height > 0
    assert str(df["date"][0]) >= "2021-01-01"
    assert str(df["date"][-1]) <= "2021-12-31"


def test_fingerprint_stable_and_sensitive(demo_bars):
    a = fingerprint(demo_bars)
    b = fingerprint(demo_bars)
    assert a == b and len(a) == 16
    # Mutating one price must change the fingerprint.
    changed = demo_bars.with_row_index("idx").with_columns(
        pl.when(pl.col("idx") == 0)
        .then(pl.col("close") + 1.0)
        .otherwise(pl.col("close"))
        .alias("close")
    ).drop("idx")
    assert fingerprint(changed) != a


def test_snapshot_roundtrip(store, tmp_path):
    snap = store.export_snapshot(tmp_path / "snapshot.parquet")
    assert snap.exists()
    fresh = MarketStore(tmp_path / "fresh.parquet")
    total = fresh.import_snapshot(snap)
    assert total == store.stats()["bars"]
    assert fresh.load_bars("AAPL").height == store.load_bars("AAPL").height
    fresh.close()


def test_persistence_across_instances(store):
    path = store.path
    store.log_sync("AAPL", "synthetic", 1, date(2023, 1, 1), date(2023, 1, 2), "test")
    reopened = MarketStore(path)
    assert reopened.stats()["bars"] == store.stats()["bars"]
    assert reopened.recent_syncs(5)[0]["notes"] == "test"
    reopened.close()


def test_local_csv_import(store, tmp_path):
    from chronostock.data.sources import load_csv_bars

    csv = tmp_path / "sample.csv"
    csv.write_text(
        "日期,开盘,最高,最低,收盘,成交量\n"
        "2022-01-04,10,11,9,10.5,1000\n"
        "2022-01-05,10.5,12,10,11.5,1200\n"
        "2022-01-06,11.5,13,11,12.5,1500\n",
        encoding="utf-8",
    )
    bars = load_csv_bars(str(csv), "TEST")
    assert bars.height == 3
    assert bars["source"][0] == "csv"
    assert bars["symbol"][0] == "TEST"
    store.upsert(bars)
    assert store.load_bars("TEST").height == 3


def test_missing_symbol_returns_empty(store):
    assert store.load_bars("NOPE").is_empty()
    assert store.coverage("NOPE") is None
    with pytest.raises(FileNotFoundError):
        store.import_snapshot("/definitely/not/here.parquet")
