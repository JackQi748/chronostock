"""Backtesting: engine, executable strategies, metrics and parameter research."""

from .engine import BacktestConfig, BacktestResult, run_backtest, run_strategy
from .metrics import performance_metrics
from .optimize import compare_strategies, grid_search, walk_forward
from .strategies import REGISTRY, list_strategies, resolve_params, signals_for

__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "run_backtest",
    "run_strategy",
    "performance_metrics",
    "grid_search",
    "walk_forward",
    "compare_strategies",
    "REGISTRY",
    "list_strategies",
    "resolve_params",
    "signals_for",
]
