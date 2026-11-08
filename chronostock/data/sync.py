"""Incremental, provenance-aware synchronisation into the local warehouse."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from chronostock.config import SETTINGS
from chronostock.data.sources import DataSourceError, fetch_bars, normalize_symbol
from chronostock.data.store import MarketStore


def _d(value: str | date | None, fallback: date) -> date:
    if value is None:
        return fallback
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def sync_symbol(
    store: MarketStore,
    symbol: str,
    start: str | date = SETTINGS.default_start,
    end: str | date | None = None,
    source: str = "auto",
    overlap_days: int = 7,
) -> dict[str, Any]:
    """Fetch only what is missing, with a small overlap for price adjustments."""
    sym = normalize_symbol(symbol)
    d0 = _d(start, date.fromisoformat(SETTINGS.default_start))
    d1 = _d(end, date.today())
    cov = store.coverage(sym)

    if cov and cov["first"] <= d0 and cov["last"] >= d1:
        return {
            "symbol": sym,
            "status": "cached",
            "rows": 0,
            "source": cov["source"],
            "range": [str(cov["first"]), str(cov["last"])],
            "notes": ["本地仓库已覆盖请求区间，跳过网络请求"],
            "synthetic": cov["source"] == "synthetic",
        }

    fetch_start = d0
    if cov and cov["last"] > d0 and cov["last"] < d1:
        fetch_start = max(d0, cov["last"] - timedelta(days=overlap_days))

    result = fetch_bars(sym, fetch_start, d1, source=source)
    bars = result.bars
    rows = store.upsert(bars)
    first, last = bars["date"].min(), bars["date"].max()
    store.log_sync(sym, result.source, rows, first, last, " | ".join(result.notes))
    return {
        "symbol": sym,
        "status": "synced",
        "rows": rows,
        "source": result.source,
        "range": [str(first), str(last)],
        "notes": result.notes,
        "synthetic": result.is_synthetic,
        "warning": SETTINGS.synthetic_warning if result.is_synthetic else None,
    }


def sync_many(
    store: MarketStore,
    symbols: list[str],
    start: str | date = SETTINGS.default_start,
    end: str | date | None = None,
    source: str = "auto",
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for symbol in symbols:
        try:
            results.append(sync_symbol(store, symbol, start, end, source))
        except (DataSourceError, ValueError) as exc:
            results.append({"symbol": symbol, "status": "failed", "error": str(exc)})
    return {
        "results": results,
        "ok": sum(1 for r in results if r["status"] in ("synced", "cached")),
        "failed": sum(1 for r in results if r["status"] == "failed"),
        "synthetic": any(r.get("synthetic") for r in results),
    }
