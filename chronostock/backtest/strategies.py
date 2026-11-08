"""Executable, deterministic trading strategies (code rules, not prompts).

A strategy maps the OHLCV history to a *target portfolio weight* for bar i.
Every rule uses only information available at bar i's close; the engine executes
it at bar i+1's open. Stateful rules use an explicit Python state machine over
vectorised entry/exit masks, which keeps them free of hidden lookahead.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
import polars as pl

from chronostock.analytics.indicators import ema, rolling_std, rsi_expr, sma, zscore_expr

SignalFn = Callable[[pl.DataFrame, dict[str, Any]], pl.Series]


@dataclass(frozen=True)
class StrategySpec:
    name: str
    label: str
    description: str
    defaults: dict[str, Any]
    space: dict[str, list[Any]]
    fn: SignalFn


REGISTRY: dict[str, StrategySpec] = {}


def _register(
    name: str, label: str, description: str, defaults: dict[str, Any], space: dict[str, list[Any]]
) -> Callable[[SignalFn], SignalFn]:
    def deco(fn: SignalFn) -> SignalFn:
        REGISTRY[name] = StrategySpec(name, label, description, defaults, space, fn)
        return fn

    return deco


def _num(df: pl.DataFrame, expr: pl.Expr) -> np.ndarray:
    return df.select(expr.alias("v")).to_series().fill_null(0.0).to_numpy().astype(float)


def _bool(df: pl.DataFrame, expr: pl.Expr) -> np.ndarray:
    return df.select(expr.alias("v")).to_series().fill_null(False).to_numpy().astype(bool)


def _state(entry: np.ndarray, exit_: np.ndarray) -> pl.Series:
    """1 after an entry condition, 0 after an exit condition; no future reads."""
    state = 0.0
    out = np.empty(entry.size, dtype=float)
    for i, (e, x) in enumerate(zip(entry, exit_)):
        if e:
            state = 1.0
        elif x:
            state = 0.0
        out[i] = state
    return pl.Series("signal", out, dtype=pl.Float64)


def _series(values: np.ndarray) -> pl.Series:
    return pl.Series("signal", np.asarray(values, dtype=float), dtype=pl.Float64)


@_register(
    "ma_cross",
    "双均线交叉",
    "快线在慢线之上持有多头，反之平仓（可开空）",
    {"fast": 10, "slow": 60, "short": False},
    {"fast": [5, 10, 20], "slow": [30, 60, 120]},
)
def ma_cross(df: pl.DataFrame, params: dict[str, Any]) -> pl.Series:
    fast, slow = int(params["fast"]), int(params["slow"])
    if fast >= slow:
        raise ValueError("fast 必须小于 slow")
    flat = -1.0 if params.get("short") else 0.0
    cond = sma(fast) > sma(slow)
    values = np.where(_bool(df, cond), 1.0, flat)
    return _series(values)


@_register(
    "macd_trend",
    "MACD 趋势",
    "MACD 快线在信号线之上做多，否则空仓",
    {"fast": 12, "slow": 26, "signal": 9},
    {"fast": [8, 12, 16], "slow": [21, 26, 35], "signal": [6, 9, 12]},
)
def macd_trend(df: pl.DataFrame, params: dict[str, Any]) -> pl.Series:
    f, s, g = int(params["fast"]), int(params["slow"]), int(params["signal"])
    macd = ema(f) - ema(s)
    signal = macd.ewm_mean(span=g, adjust=False, min_samples=1)
    return _series(np.where(_bool(df, macd > signal), 1.0, 0.0))


@_register(
    "rsi_reversion",
    "RSI 超卖反转",
    "RSI 跌破下轨入场，升破上轨离场，中间区间保持状态",
    {"period": 14, "lower": 30, "upper": 55},
    {"period": [7, 14, 21], "lower": [20, 25, 30], "upper": [50, 55, 65]},
)
def rsi_reversion(df: pl.DataFrame, params: dict[str, Any]) -> pl.Series:
    r = rsi_expr(int(params["period"]))
    entry = _bool(df, r < float(params["lower"]))
    exit_ = _bool(df, r > float(params["upper"]))
    return _state(entry, exit_)


@_register(
    "bollinger_breakout",
    "布林带突破",
    "收盘突破上轨入场，跌回中轨离场",
    {"period": 20, "mult": 2.0},
    {"period": [10, 20, 30], "mult": [1.5, 2.0, 2.5]},
)
def bollinger_breakout(df: pl.DataFrame, params: dict[str, Any]) -> pl.Series:
    mid = sma(int(params["period"]))
    upper = mid + float(params["mult"]) * rolling_std(int(params["period"]))
    close = pl.col("close")
    entry = _bool(df, close > upper)
    exit_ = _bool(df, close < mid)
    return _state(entry, exit_)


@_register(
    "donchian_breakout",
    "唐奇安通道突破",
    "突破 N 日最高价（不含当日）入场，跌破 M 日最低价离场",
    {"entry": 20, "exit": 10},
    {"entry": [20, 40, 55], "exit": [5, 10, 20]},
)
def donchian_breakout(df: pl.DataFrame, params: dict[str, Any]) -> pl.Series:
    high, low, close = pl.col("high"), pl.col("low"), pl.col("close")
    up = high.rolling_max(window_size=int(params["entry"]), min_samples=1).shift(1)
    dn = low.rolling_min(window_size=int(params["exit"]), min_samples=1).shift(1)
    return _state(_bool(df, close > up), _bool(df, close < dn))


@_register(
    "dual_momentum",
    "双动量择时",
    "N 日动量为正且价格在长期均线上方才持仓，否则空仓",
    {"lookback": 120, "ma": 120},
    {"lookback": [60, 120, 250], "ma": [60, 120, 200]},
)
def dual_momentum(df: pl.DataFrame, params: dict[str, Any]) -> pl.Series:
    close = pl.col("close")
    mom = close / close.shift(int(params["lookback"])) - 1.0
    cond = (mom > 0) & (close > sma(int(params["ma"])))
    return _series(np.where(_bool(df, cond), 1.0, 0.0))


@_register(
    "zscore_reversion",
    "Z-Score 均值回归",
    "价格偏离均线超过阈值入场，回归到接近均值离场",
    {"period": 20, "entry": 2.0, "exit": 0.3},
    {"period": [10, 20, 40], "entry": [1.5, 2.0, 2.5], "exit": [0.2, 0.5]},
)
def zscore_reversion(df: pl.DataFrame, params: dict[str, Any]) -> pl.Series:
    z = zscore_expr(int(params["period"]))
    entry = _bool(df, z < -float(params["entry"]))
    exit_ = _bool(df, z > -float(params["exit"]))
    return _state(entry, exit_)


@_register(
    "vol_target",
    "波动率目标择时",
    "长期均线之上按目标波动率缩放仓位，之下空仓",
    {"vol_window": 20, "target_vol": 0.18, "ma": 200, "max_leverage": 1.0},
    {"vol_window": [20, 40], "target_vol": [0.12, 0.18, 0.25], "ma": [120, 200]},
)
def vol_target(df: pl.DataFrame, params: dict[str, Any]) -> pl.Series:
    close = pl.col("close")
    ret = close / close.shift(1) - 1.0
    vol = ret.rolling_std(window_size=int(params["vol_window"]), min_samples=2, ddof=0) * math.sqrt(252)
    target = pl.lit(float(params["target_vol"])) / pl.when(vol > 0).then(vol).otherwise(1.0)
    capped = pl.when(target > float(params["max_leverage"])).then(
        float(params["max_leverage"])
    ).otherwise(target)
    weight = pl.when(close > sma(int(params["ma"]))).then(capped).otherwise(0.0)
    return _series(np.clip(_num(df, weight), 0.0, float(params["max_leverage"])))


def get_strategy(name: str) -> StrategySpec:
    if name not in REGISTRY:
        raise ValueError(f"未知策略: {name}，可选: {', '.join(REGISTRY)}")
    return REGISTRY[name]


def resolve_params(name: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    spec = get_strategy(name)
    params = dict(spec.defaults)
    for key, value in (overrides or {}).items():
        if key in params:
            params[key] = value
    return params


def signals_for(bars: pl.DataFrame, name: str, params: dict[str, Any] | None = None) -> pl.Series:
    spec = get_strategy(name)
    p = resolve_params(name, params)
    out = spec.fn(bars, p)
    if len(out) != bars.height:
        raise RuntimeError(f"策略 {name} 输出长度与行情不一致")
    return out


def list_strategies() -> list[dict[str, Any]]:
    return [
        {
            "name": s.name,
            "label": s.label,
            "description": s.description,
            "defaults": s.defaults,
            "space": s.space,
        }
        for s in REGISTRY.values()
    ]
