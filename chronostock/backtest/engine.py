"""Deterministic target-weight backtest engine.

Key no-lookahead properties:
* signals are shifted by one bar and executed at the NEXT bar's open;
* position sizing uses the previous bar's closing equity only;
* stop-loss is checked before take-profit, and a forced exit blocks re-entry
  for two bars (conservative treatment of same-bar ambiguity);
* costs are charged on traded notional on every rebalance.

This is deliberately a transparent, auditable engine rather than a black box.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import polars as pl

from chronostock.backtest.metrics import performance_metrics


@dataclass
class BacktestConfig:
    initial_capital: float = 100_000.0
    commission_bps: float = 5.0
    slippage_bps: float = 2.0
    allow_short: bool = False
    stop_loss: float | None = None
    take_profit: float | None = None
    max_leverage: float = 1.0
    rebalance_threshold: float = 0.02

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "BacktestConfig":
        data = {k: v for k, v in (data or {}).items() if v is not None}
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def as_dict(self) -> dict[str, Any]:
        return {
            "initial_capital": self.initial_capital,
            "commission_bps": self.commission_bps,
            "slippage_bps": self.slippage_bps,
            "allow_short": self.allow_short,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "max_leverage": self.max_leverage,
            "rebalance_threshold": self.rebalance_threshold,
        }


@dataclass
class BacktestResult:
    strategy: str
    params: dict[str, Any]
    config: dict[str, Any]
    metrics: dict[str, Any]
    equity_curve: list[dict[str, Any]] = field(default_factory=list)
    trades: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self, curve_limit: int | None = None) -> dict[str, Any]:
        curve = self.equity_curve
        if curve_limit and len(curve) > curve_limit:
            step = max(1, len(curve) // curve_limit)
            curve = curve[::step] + [curve[-1]]
        return {
            "strategy": self.strategy,
            "params": self.params,
            "config": self.config,
            "metrics": self.metrics,
            "equity_curve": curve,
            "trades": self.trades,
        }


REQUIRED_COLUMNS = {"date", "open", "high", "low", "close"}


def run_backtest(
    bars: pl.DataFrame,
    signals: pl.Series | np.ndarray | list[float],
    config: BacktestConfig | None = None,
    strategy: str = "custom",
    params: dict[str, Any] | None = None,
) -> BacktestResult:
    """Run a target-weight backtest over an OHLCV frame."""
    cfg = config or BacktestConfig()
    missing = REQUIRED_COLUMNS - set(bars.columns)
    if missing:
        raise ValueError(f"行情缺少列: {sorted(missing)}")
    if bars.height < 10:
        raise ValueError("回测至少需要 10 根 K 线")

    if isinstance(signals, pl.Series):
        sig = signals.to_numpy().astype(float)
    else:
        sig = np.asarray(signals, dtype=float)
    sig = np.nan_to_num(sig, nan=0.0, posinf=0.0, neginf=0.0)
    if sig.size != bars.height:
        raise ValueError("信号长度与行情长度不一致")
    sig = np.clip(sig, 0.0 if not cfg.allow_short else -cfg.max_leverage, cfg.max_leverage)
    target = np.roll(sig, 1)  # bar i close signal -> bar i+1 open execution
    target[0] = 0.0

    opens = bars["open"].to_numpy().astype(float)
    highs = bars["high"].to_numpy().astype(float)
    lows = bars["low"].to_numpy().astype(float)
    closes = bars["close"].to_numpy().astype(float)
    dates = bars["date"].to_list()
    n = bars.height
    cost_rate = (cfg.commission_bps + cfg.slippage_bps) / 10_000.0

    cash = float(cfg.initial_capital)
    units = 0.0
    avg_price = 0.0
    cooldown = 0
    turnover = 0.0
    equity = np.empty(n, dtype=float)
    positions = np.empty(n, dtype=float)
    benchmark = np.empty(n, dtype=float)
    curve: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    peak = float(cfg.initial_capital)

    for i in range(n):
        op, hi, lo, cl = opens[i], highs[i], lows[i], closes[i]
        w = target[i]
        if cooldown > 0:
            w = 0.0
            cooldown -= 1
        base = equity[i - 1] if i > 0 else float(cfg.initial_capital)

        current_weight = (units * op / base) if base > 0 else 0.0
        # No-trade band: equity drift alone must not create a trade every bar.
        if op > 0 and abs(w - current_weight) > cfg.rebalance_threshold:
            desired_units = (w * base) / op
            delta = desired_units - units
        else:
            delta = 0.0
        if abs(delta) * op > 1e-8:
            fee = abs(delta * op) * cost_rate
            pnl: float | None = None
            if units != 0.0 and np.sign(delta) != np.sign(units):
                closed = min(abs(delta), abs(units))
                direction = 1.0 if units > 0 else -1.0
                pnl = (op - avg_price) * closed * direction - fee
            cash -= delta * op + fee
            turnover += abs(delta * op)
            new_units = units + delta
            if new_units != 0.0 and (units == 0.0 or np.sign(delta) == np.sign(units)):
                avg_price = (avg_price * abs(units) + op * abs(delta)) / (abs(units) + abs(delta))
            if abs(new_units) < 1e-9:
                avg_price = 0.0
            trades.append({
                "date": str(dates[i]),
                "action": "BUY" if delta > 0 else "SELL",
                "units": round(float(abs(delta)), 4),
                "price": round(float(op), 4),
                "fee": round(float(fee), 2),
                "pnl": round(float(pnl), 2) if pnl is not None else None,
                "reason": "rebalance",
            })
            units = new_units

        if units != 0.0 and avg_price > 0 and (cfg.stop_loss or cfg.take_profit):
            direction = 1.0 if units > 0 else -1.0
            adverse = lo if direction > 0 else hi
            favorable = hi if direction > 0 else lo
            forced: tuple[str, float] | None = None
            if cfg.stop_loss and direction * (adverse - avg_price) / avg_price <= -cfg.stop_loss:
                forced = ("stop_loss", avg_price * (1.0 - direction * cfg.stop_loss))
            elif cfg.take_profit and direction * (favorable - avg_price) / avg_price >= cfg.take_profit:
                forced = ("take_profit", avg_price * (1.0 + direction * cfg.take_profit))
            if forced:
                reason, px = forced
                fee = abs(units * px) * cost_rate
                pnl = (px - avg_price) * units - fee
                cash += units * px - fee
                turnover += abs(units * px)
                trades.append({
                    "date": str(dates[i]),
                    "action": reason,
                    "units": round(float(abs(units)), 4),
                    "price": round(float(px), 4),
                    "fee": round(float(fee), 2),
                    "pnl": round(float(pnl), 2),
                    "reason": reason,
                })
                units = 0.0
                avg_price = 0.0
                cooldown = 2

        eq = cash + units * cl
        equity[i] = eq
        positions[i] = (units * cl / eq) if eq > 0 else 0.0
        benchmark[i] = cfg.initial_capital * cl / closes[0]
        peak = max(peak, eq)
        curve.append({
            "date": str(dates[i]),
            "equity": round(float(eq), 2),
            "benchmark": round(float(benchmark[i]), 2),
            "drawdown": round(float(eq / peak - 1.0) * 100, 3),
            "position": round(float(positions[i]), 3),
            "close": round(float(cl), 4),
        })

    metrics = performance_metrics(
        equity,
        benchmark,
        trades,
        positions,
        turnover / cfg.initial_capital,
        cfg.initial_capital,
    )
    return BacktestResult(
        strategy=strategy,
        params=params or {},
        config=cfg.as_dict(),
        metrics=metrics,
        equity_curve=curve,
        trades=trades,
    )


def run_strategy(
    bars: pl.DataFrame,
    strategy_name: str,
    params: dict[str, Any] | None = None,
    config: BacktestConfig | None = None,
) -> BacktestResult:
    from chronostock.backtest.strategies import resolve_params, signals_for

    resolved = resolve_params(strategy_name, params)
    signals = signals_for(bars, strategy_name, resolved)
    return run_backtest(bars, signals, config, strategy=strategy_name, params=resolved)
