"""Parameter grid search, rolling walk-forward validation and strategy comparison."""

from __future__ import annotations

import itertools
from typing import Any

import numpy as np
import polars as pl

from chronostock.backtest.engine import BacktestConfig, run_backtest, run_strategy
from chronostock.backtest.metrics import performance_metrics
from chronostock.backtest.strategies import get_strategy, signals_for

OBJECTIVES = ["sharpe", "sortino", "calmar", "total_return_pct", "cagr_pct", "-max_drawdown_pct"]


def objective_value(metrics: dict[str, Any], objective: str) -> float:
    if objective.startswith("-"):
        value = metrics.get(objective[1:], 0.0)
        return -float(value or 0.0)
    return float(metrics.get(objective, 0.0) or 0.0)


def _combos(grid: dict[str, list[Any]], max_combos: int) -> tuple[list[str], list[tuple[Any, ...]]]:
    keys = [k for k, v in grid.items() if v]
    values = [grid[k] for k in keys]
    total = 1
    for v in values:
        total *= len(v)
    pairs = list(itertools.islice(itertools.product(*values), max_combos))
    return keys, pairs


def grid_search(
    bars: pl.DataFrame,
    strategy: str,
    grid: dict[str, list[Any]] | None = None,
    config: BacktestConfig | None = None,
    objective: str = "sharpe",
    top_n: int = 8,
    max_combos: int = 240,
) -> dict[str, Any]:
    """Exhaustive (capped) grid search. All combos see exactly the same bars."""
    spec = get_strategy(strategy)
    if objective not in OBJECTIVES:
        raise ValueError(f"未知目标函数: {objective}，可选: {OBJECTIVES}")
    effective = {k: list(v) for k, v in (grid or spec.space).items() if k in spec.defaults and v}
    if not effective:
        effective = {"_default": [None]}
    keys, pairs = _combos(effective, max_combos)
    cfg = config or BacktestConfig()
    results: list[dict[str, Any]] = []
    for combo in pairs:
        overrides = {k: v for k, v in zip(keys, combo) if k != "_default" and v is not None}
        try:
            result = run_strategy(bars, strategy, overrides, cfg)
        except (ValueError, ZeroDivisionError):
            continue
        score = objective_value(result.metrics, objective)
        results.append({
            "params": result.params,
            "score": round(score, 4),
            "metrics": result.metrics,
        })
    results.sort(key=lambda r: r["score"], reverse=True)
    return {
        "strategy": strategy,
        "objective": objective,
        "combos_evaluated": len(results),
        "results": results[:top_n],
        "best": results[0] if results else None,
        "note": "样本内网格搜索存在过拟合风险，请结合 walk-forward 样本外结果一起看。",
    }


def walk_forward(
    bars: pl.DataFrame,
    strategy: str,
    grid: dict[str, list[Any]] | None = None,
    config: BacktestConfig | None = None,
    objective: str = "sharpe",
    train_bars: int = 500,
    test_bars: int = 120,
    max_combos: int = 120,
) -> dict[str, Any]:
    """Expanding-window walk-forward: choose params in-sample, score out-of-sample.

    For each fold the test-period signals are recomputed on the full prefix up to
    the test window, so indicator warm-up is realistic and no future bar leaks.
    """
    n = bars.height
    if n < train_bars + test_bars + 5:
        raise ValueError(f"历史样本不足：需要 ≥ {train_bars + test_bars + 5} 根，当前 {n} 根")
    cfg = config or BacktestConfig()
    folds: list[dict[str, Any]] = []
    oos_dates: list[str] = []
    oos_returns: list[float] = []
    start = train_bars
    while start + test_bars <= n:
        train = bars.slice(0, start)
        search = grid_search(train, strategy, grid, cfg, objective, top_n=1, max_combos=max_combos)
        best = search["best"]
        if best is None:
            break
        test = bars.slice(start, test_bars)
        # Recompute signals on the whole prefix, then take only the test window.
        prefix = bars.slice(0, start + test_bars)
        sig_all = signals_for(prefix, strategy, best["params"])
        sig_test = sig_all.slice(start, test_bars)
        result = run_backtest(test, sig_test, cfg, strategy=strategy, params=best["params"])
        eq = [p["equity"] for p in result.equity_curve]
        rets = [eq[0] / cfg.initial_capital - 1.0] + [eq[i] / eq[i - 1] - 1.0 for i in range(1, len(eq))]
        oos_returns.extend(rets)
        oos_dates.extend(p["date"] for p in result.equity_curve)
        folds.append({
            "fold": len(folds) + 1,
            "train": [str(bars["date"][0]), str(bars["date"][start - 1])],
            "test": [str(bars["date"][start]), str(bars["date"][start + test_bars - 1])],
            "best_params": best["params"],
            "in_sample_score": best["score"],
            "oos_metrics": result.metrics,
        })
        start += test_bars

    if not folds or not oos_returns:
        raise ValueError("walk-forward 未产生有效样本外折")
    growth = np.cumprod(1.0 + np.asarray(oos_returns, dtype=float))
    equity = (cfg.initial_capital * growth).tolist()
    curve = [
        {"date": d, "equity": round(e, 2), "benchmark": round(cfg.initial_capital * (1 + sum(oos_returns[: i + 1])), 2),
         "drawdown": 0.0, "position": 0.0}
        for i, (d, e) in enumerate(zip(oos_dates, equity))
    ]
    oos_metrics = performance_metrics(np.asarray(equity), None, positions=np.zeros(len(equity)))
    is_mean = float(np.mean([f["in_sample_score"] for f in folds]))
    oos_sharpe = oos_metrics["sharpe"]
    return {
        "strategy": strategy,
        "objective": objective,
        "train_bars": train_bars,
        "test_bars": test_bars,
        "folds": folds,
        "fold_count": len(folds),
        "oos_metrics": oos_metrics,
        "oos_equity_curve": curve,
        "in_sample_mean_score": round(is_mean, 4),
        "overfit_ratio": round(is_mean / oos_sharpe, 2) if oos_sharpe > 0 else None,
        "note": "overfit_ratio = 样本内目标均值 / 样本外夏普，数值越大越可能存在参数过拟合。",
    }


def compare_strategies(
    bars: pl.DataFrame, strategy_names: list[str] | None = None, config: BacktestConfig | None = None
) -> list[dict[str, Any]]:
    from chronostock.backtest.strategies import REGISTRY

    names = strategy_names or list(REGISTRY)
    out = []
    for name in names:
        try:
            result = run_strategy(bars, name, config=config)
            out.append({"strategy": name, "params": result.params, "metrics": result.metrics})
        except (ValueError, ZeroDivisionError) as exc:
            out.append({"strategy": name, "error": str(exc)})
    return out
