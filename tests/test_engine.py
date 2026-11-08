"""Backtest engine: accounting, costs, execution timing, risk controls."""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from chronostock.backtest import BacktestConfig, run_backtest, run_strategy
from tests.conftest import constant_bars


def test_flat_price_zero_return_without_costs():
    bars = constant_bars()
    cfg = BacktestConfig(commission_bps=0, slippage_bps=0)
    signals = pl.Series("s", np.ones(bars.height))
    result = run_backtest(bars, signals, cfg)
    assert abs(result.metrics["total_return_pct"]) < 1e-6
    assert result.metrics["max_drawdown_pct"] == 0.0
    assert result.metrics["final_equity"] == pytest.approx(cfg.initial_capital, abs=0.01)


def test_execution_is_next_bar_open():
    bars = constant_bars()
    signals = pl.Series("s", np.ones(bars.height))
    result = run_backtest(bars, signals, BacktestConfig(commission_bps=0, slippage_bps=0))
    assert result.trades, "应至少有一笔成交"
    assert result.trades[0]["date"] == str(bars["date"][1]), "首笔成交必须发生在第二根 bar"


def test_costs_reduce_equity(demo_bars):
    cheap = run_strategy(demo_bars, "dual_momentum", config=BacktestConfig(commission_bps=0, slippage_bps=0))
    expensive = run_strategy(demo_bars, "dual_momentum", config=BacktestConfig(commission_bps=50, slippage_bps=20))
    assert expensive.metrics["final_equity"] < cheap.metrics["final_equity"]


def test_stop_loss_and_cooldown(demo_bars):
    cfg = BacktestConfig(stop_loss=0.005, take_profit=0.02, commission_bps=0, slippage_bps=0)
    result = run_strategy(demo_bars, "dual_momentum", config=cfg)
    assert any(t["action"] in ("stop_loss", "take_profit") for t in result.trades)
    assert all(-1.0 <= p["position"] <= 1.0 for p in result.equity_curve)


def test_short_disabled_by_default(demo_bars):
    signals = pl.Series("s", -np.ones(demo_bars.height))
    result = run_backtest(demo_bars, signals, BacktestConfig())
    assert result.metrics["exposure_pct"] == 0.0
    assert abs(result.metrics["total_return_pct"]) < 1e-6
    short = run_backtest(demo_bars, signals, BacktestConfig(allow_short=True))
    assert short.metrics["exposure_pct"] > 50.0


def test_metrics_keys_and_benchmark(demo_bars):
    result = run_strategy(demo_bars, "ma_cross")
    required = {"total_return_pct", "cagr_pct", "max_drawdown_pct", "sharpe", "sortino",
                "calmar", "trades", "benchmark_return_pct", "excess_return_pct", "bars"}
    assert required <= set(result.metrics)
    assert len(result.equity_curve) == demo_bars.height
    assert result.metrics["max_drawdown_pct"] <= 0.0


def test_invalid_inputs(demo_bars):
    with pytest.raises(ValueError):
        run_backtest(demo_bars.head(5), pl.Series("s", np.ones(5)))
    with pytest.raises(ValueError):
        run_backtest(demo_bars, pl.Series("s", np.ones(3)))
    with pytest.raises(ValueError):
        run_strategy(demo_bars, "not_a_strategy")
