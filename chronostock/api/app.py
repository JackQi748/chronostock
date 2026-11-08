"""FastAPI application: data, analytics, backtesting and the static workbench."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.staticfiles import StaticFiles

from chronostock import __version__
from chronostock.analytics import compute_indicators, indicator_frame_for_json, score_snapshot, seasonality_report
from chronostock.backtest import BacktestConfig, grid_search, list_strategies, run_strategy, walk_forward
from chronostock.config import WEB_DIR, warehouse_path
from chronostock.data.store import MarketStore
from chronostock.data.sync import sync_many
from chronostock.models import (
    AnalyzeRequest,
    BacktestRequest,
    HealthResponse,
    OptimizeRequest,
    SyncRequest,
)
from chronostock.report.builder import build_analysis
from chronostock.report.templates import render_report

app = FastAPI(
    title="ChronoStock API",
    version=__version__,
    description="可复现的历史股票分析、时间点回放与策略回测",
)

_STORES: dict[str, MarketStore] = {}


def get_store() -> MarketStore:
    """One in-memory DuckDB engine per warehouse path (process-local)."""
    path = str(warehouse_path())
    if path not in _STORES:
        _STORES[path] = MarketStore(path)
    return _STORES[path]


def _bars_or_404(
    symbol: str, start: str | None = None, end: str | None = None, as_of: str | None = None
):
    bars = get_store().load_bars(symbol, start=start, end=end, as_of=as_of)
    if bars.height < 2:
        raise HTTPException(404, detail=f"仓库中没有 {symbol} 在此时点之前的历史数据，请先执行 sync / demo")
    return bars


@app.get("/api/health", response_model=HealthResponse, tags=["system"])
def health() -> HealthResponse:
    store = get_store()
    stats = store.stats()
    return HealthResponse(
        status="ok",
        version=__version__,
        warehouse=str(store.path),
        symbols=stats["symbols"],
        bars=stats["bars"],
    )


@app.get("/api/symbols", tags=["data"])
def symbols() -> dict[str, Any]:
    return {"symbols": get_store().list_symbols(), "syncs": get_store().recent_syncs(10)}


@app.get("/api/strategies", tags=["backtest"])
def strategies() -> dict[str, Any]:
    return {"strategies": list_strategies()}


@app.post("/api/sync", tags=["data"])
def sync(req: SyncRequest) -> dict[str, Any]:
    try:
        return sync_many(get_store(), req.symbols, req.start, req.end, req.source)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc


@app.get("/api/bars", tags=["data"])
def bars(
    symbol: str,
    start: str | None = None,
    end: str | None = None,
    as_of: str | None = None,
    vintage: bool = False,
) -> list[dict[str, Any]]:
    df = get_store().load_bars(symbol, start=start, end=end, as_of=as_of, vintage=vintage)
    return [
        {
            "date": str(r["date"]),
            "open": r["open"], "high": r["high"], "low": r["low"],
            "close": r["close"], "volume": r["volume"], "source": r["source"],
        }
        for r in df.iter_rows(named=True)
    ]


@app.get("/api/indicators", tags=["analytics"])
def indicators(symbol: str, as_of: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    df = _bars_or_404(symbol, as_of=as_of)
    return indicator_frame_for_json(df, limit=min(max(limit, 10), 5000))


@app.get("/api/analyze", tags=["analytics"])
def analyze(
    symbol: str,
    as_of: str | None = None,
    start: str | None = None,
    horizon: int = 20,
) -> dict[str, Any]:
    df = _bars_or_404(symbol, start=start, as_of=as_of)
    ind = compute_indicators(df)
    source = str(df["source"][-1])
    return {
        "symbol": symbol,
        "as_of": str(df["date"][-1]),
        "source": source,
        "synthetic": source == "synthetic",
        "snapshot": score_snapshot(ind, source),
        "seasonality": seasonality_report(ind, horizon),
    }


@app.get("/api/seasonality", tags=["analytics"])
def seasonality(symbol: str, as_of: str | None = None, horizon: int = 20) -> dict[str, Any]:
    df = _bars_or_404(symbol, as_of=as_of)
    return {"symbol": symbol, "as_of": str(df["date"][-1]), **seasonality_report(compute_indicators(df), horizon)}


def _cfg(req: BacktestRequest) -> BacktestConfig:
    return BacktestConfig(
        initial_capital=req.initial_capital,
        commission_bps=req.commission_bps,
        slippage_bps=req.slippage_bps,
        allow_short=req.allow_short,
        stop_loss=req.stop_loss,
        take_profit=req.take_profit,
    )


@app.post("/api/backtest", tags=["backtest"])
def backtest(req: BacktestRequest) -> dict[str, Any]:
    df = _bars_or_404(req.symbol, start=req.start, end=req.end, as_of=req.as_of)
    try:
        return run_strategy(df, req.strategy, req.params, _cfg(req)).to_dict(curve_limit=3000)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc


@app.post("/api/optimize", tags=["backtest"])
def optimize(req: OptimizeRequest) -> dict[str, Any]:
    df = _bars_or_404(req.symbol, as_of=req.as_of)
    cfg = BacktestConfig()
    try:
        if req.walk_forward:
            return walk_forward(
                df, req.strategy, req.grid or None, cfg, req.objective,
                req.train_bars, req.test_bars,
            )
        return grid_search(df, req.strategy, req.grid or None, cfg, req.objective, req.top_n)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc


@app.get("/api/report", tags=["report"])
def report(
    symbol: str,
    as_of: str | None = None,
    format: str = Query("html", pattern="^(html|md|markdown)$"),
) -> Response:
    try:
        bundle = build_analysis(get_store(), symbol, as_of=as_of)
    except LookupError as exc:
        raise HTTPException(404, detail=str(exc)) from exc
    text = render_report(bundle, "md" if format in ("md", "markdown") else "html")
    media = "text/html; charset=utf-8" if format == "html" else "text/markdown; charset=utf-8"
    return Response(content=text, media_type=media)


@app.post("/api/report-data", tags=["report"])
def report_data(req: AnalyzeRequest) -> dict[str, Any]:
    try:
        return build_analysis(get_store(), req.symbol, req.as_of, req.start)
    except LookupError as exc:
        raise HTTPException(404, detail=str(exc)) from exc


app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
