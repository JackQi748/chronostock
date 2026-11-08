"""Assemble one reproducible point-in-time research bundle.

Everything needed to re-run the analysis is recorded: the exact bar range, the
data fingerprint, the strategy parameters and the backtest configuration.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import polars as pl

from chronostock.analytics import compute_indicators, score_snapshot, seasonality_report
from chronostock.backtest import BacktestConfig, compare_strategies, run_strategy
from chronostock.config import DISCLAIMER, SETTINGS
from chronostock.data.store import MarketStore, fingerprint

COMPARE_SET = ["dual_momentum", "ma_cross", "rsi_reversion", "donchian_breakout", "vol_target"]


def _downsample(values: list[float], limit: int = 260) -> list[float]:
    if len(values) <= limit:
        return values
    step = max(1, len(values) // limit)
    return values[::step] + [values[-1]]


def _safe_list(series: pl.Series) -> list[float]:
    return [float(v) if v is not None else 0.0 for v in series.to_list()]


def build_analysis(
    store: MarketStore,
    symbol: str,
    as_of: str | None = None,
    start: str | None = None,
    horizon: int = 20,
    config: BacktestConfig | None = None,
) -> dict[str, Any]:
    cfg = config or BacktestConfig()
    bars = store.load_bars(symbol, start=start, as_of=as_of)
    if bars.height < 60:
        raise LookupError(f"{symbol} 在指定时点前仅有 {bars.height} 根 K 线，至少需要 60 根")

    ind = compute_indicators(bars)
    snapshot = score_snapshot(ind, source=str(bars["source"][-1]))
    stats = seasonality_report(ind, horizon)
    comparison = compare_strategies(bars, COMPARE_SET, cfg)
    ranked = [c for c in comparison if "metrics" in c]
    best = max(ranked, key=lambda c: c["metrics"]["sharpe"], default=None)
    best_result = None
    if best:
        best_result = run_strategy(bars, best["strategy"], best["params"], cfg)

    sources = sorted(set(bars["source"].drop_nulls().to_list()))
    price_tail = ind.tail(250)
    curve = [p["equity"] for p in best_result.equity_curve] if best_result else []
    bench_curve = [p["benchmark"] for p in best_result.equity_curve] if best_result else []

    return {
        "symbol": symbol,
        "as_of": str(bars["date"][-1]),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "data": {
            "first_date": str(bars["date"][0]),
            "last_date": str(bars["date"][-1]),
            "bars": bars.height,
            "sources": sources,
            "fingerprint": fingerprint(bars.select("symbol", "date", "open", "high", "low", "close", "volume", "source")),
            "synthetic": "synthetic" in sources,
        },
        "warning": SETTINGS.synthetic_warning if "synthetic" in sources else None,
        "snapshot": snapshot,
        "seasonality": stats,
        "strategy_comparison": comparison,
        "best_strategy": best,
        "backtest_config": cfg.as_dict(),
        "equity_curve": _downsample(curve),
        "benchmark_curve": _downsample(bench_curve),
        "price_chart": {
            "close": _downsample(_safe_list(price_tail["close"])),
            "sma20": _downsample(_safe_list(price_tail["sma20"])),
            "sma60": _downsample(_safe_list(price_tail["sma60"])),
        },
        "methodology": [
            "指标全部为向后窗口计算；as_of 之后的数据不参与任何计算（point-in-time）。",
            "策略信号在 bar i 收盘产生、bar i+1 开盘成交，含手续费与滑点。",
            "事件研究统计的是条件出现后 N 根 K 线的收盘价变动，不含交易成本。",
            "评分是确定性规则，不含任何 LLM 或新闻情绪输入。",
        ],
        "disclaimer": DISCLAIMER,
    }
