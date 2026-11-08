"""Performance metrics. All ratios use 252 trading days and the same equity base."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

TRADING_DAYS = 252


def _safe(value: float) -> float:
    return float(value) if math.isfinite(value) else 0.0


def performance_metrics(
    equity: np.ndarray,
    benchmark: np.ndarray | None = None,
    trades: list[dict[str, Any]] | None = None,
    positions: np.ndarray | None = None,
    turnover: float = 0.0,
    initial_capital: float = 100_000.0,
) -> dict[str, Any]:
    eq = np.asarray(equity, dtype=float)
    if eq.size < 2:
        raise ValueError("净值序列至少需要 2 个点")
    eq = np.where(np.isfinite(eq), eq, initial_capital)
    rets = eq[1:] / eq[:-1] - 1.0
    n = rets.size
    total = eq[-1] / eq[0] - 1.0
    years = n / TRADING_DAYS
    cagr = (eq[-1] / eq[0]) ** (1.0 / years) - 1.0 if years > 0 and eq[0] > 0 else 0.0
    peak = np.maximum.accumulate(eq)
    dd = eq / peak - 1.0
    mdd = float(dd.min())
    std = float(rets.std(ddof=1)) if n > 1 else 0.0
    sharpe = float(rets.mean() / std * math.sqrt(TRADING_DAYS)) if std > 0 else 0.0
    downside = rets[rets < 0]
    dstd = float(downside.std(ddof=1)) if downside.size > 1 else 0.0
    sortino = float(rets.mean() / dstd * math.sqrt(TRADING_DAYS)) if dstd > 0 else 0.0
    calmar = cagr / abs(mdd) if mdd < 0 else 0.0

    pnls = [t["pnl"] for t in (trades or []) if t.get("pnl") is not None and abs(t["pnl"]) > 1e-9]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_profit, gross_loss = sum(wins), abs(sum(losses))

    bench_return = None
    if benchmark is not None and len(benchmark) == eq.size and benchmark[0] > 0:
        bench_return = float(benchmark[-1] / benchmark[0] - 1.0)

    return {
        "total_return_pct": round(_safe(total * 100), 2),
        "cagr_pct": round(_safe(cagr * 100), 2),
        "max_drawdown_pct": round(_safe(mdd * 100), 2),
        "annual_vol_pct": round(_safe(std * math.sqrt(TRADING_DAYS) * 100), 2),
        "sharpe": round(_safe(sharpe), 3),
        "sortino": round(_safe(sortino), 3),
        "calmar": round(_safe(calmar), 3),
        "trades": len(trades or []),
        "round_trips": len(pnls),
        "win_rate_pct": round(len(wins) / len(pnls) * 100, 1) if pnls else None,
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss > 0 else None,
        "turnover_x": round(_safe(turnover), 2),
        "exposure_pct": round(float(np.mean(np.abs(positions))) * 100, 1) if positions is not None and positions.size else 0.0,
        "benchmark_return_pct": round(bench_return * 100, 2) if bench_return is not None else None,
        "excess_return_pct": round((total - bench_return) * 100, 2) if bench_return is not None else None,
        "final_equity": round(float(eq[-1]), 2),
        "bars": int(eq.size),
    }


def metrics_from_curve(curve: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    eq = np.asarray([p["equity"] for p in curve], dtype=float)
    bench = np.asarray([p["benchmark"] for p in curve], dtype=float)
    pos = np.asarray([p["position"] for p in curve], dtype=float)
    return performance_metrics(eq, bench, positions=pos, **kwargs)
