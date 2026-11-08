"""Seasonality and conditional event studies on a single symbol's history.

An "event" is a deterministic condition observable at bar i; the outcome is the
forward return over the next ``horizon`` bars. The baseline (all bars) is always
reported alongside so a conditional win rate is never presented without context.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl


def _stats(values: np.ndarray) -> dict[str, Any]:
    vals = values[np.isfinite(values)]
    if vals.size == 0:
        return {"samples": 0, "win_rate_pct": None, "mean_pct": None, "median_pct": None,
                "p25_pct": None, "p75_pct": None, "best_pct": None, "worst_pct": None}
    return {
        "samples": int(vals.size),
        "win_rate_pct": round(float((vals > 0).mean() * 100), 1),
        "mean_pct": round(float(vals.mean() * 100), 2),
        "median_pct": round(float(np.median(vals) * 100), 2),
        "p25_pct": round(float(np.percentile(vals, 25) * 100), 2),
        "p75_pct": round(float(np.percentile(vals, 75) * 100), 2),
        "best_pct": round(float(vals.max() * 100), 2),
        "worst_pct": round(float(vals.min() * 100), 2),
    }


def forward_returns(ind: pl.DataFrame, horizon: int = 20) -> np.ndarray:
    close = ind["close"].to_numpy().astype(float)
    n = close.size
    out = np.full(n, np.nan)
    if horizon > 0 and n > horizon:
        out[: n - horizon] = close[horizon:] / close[: n - horizon] - 1.0
    return out


def monthly_stats(ind: pl.DataFrame) -> list[dict[str, Any]]:
    """Average daily return / win rate per calendar month over the full history."""
    if "ret_1d" not in ind.columns:
        return []
    grouped = (
        ind.with_columns(pl.col("date").dt.month().alias("month"))
        .group_by("month")
        .agg(
            pl.col("ret_1d").mean().alias("mean"),
            (pl.col("ret_1d") > 0).cast(pl.Int8).mean().alias("win"),
            pl.len().alias("samples"),
            pl.col("ret_1d").min().alias("worst"),
            pl.col("ret_1d").max().alias("best"),
        )
        .sort("month")
    )
    return [
        {
            "month": int(r["month"]),
            "mean_daily_pct": round(float(r["mean"] or 0) * 100, 3),
            "win_rate_pct": round(float(r["win"] or 0) * 100, 1),
            "samples": int(r["samples"]),
            "best_day_pct": round(float(r["best"] or 0) * 100, 2),
            "worst_day_pct": round(float(r["worst"] or 0) * 100, 2),
        }
        for r in grouped.iter_rows(named=True)
    ]


def _mask(ind: pl.DataFrame, expr: pl.Expr) -> np.ndarray:
    return (
        ind.select(expr.fill_null(False).alias("m"))
        .to_series()
        .to_numpy()
        .astype(bool)
    )


def event_study(ind: pl.DataFrame, horizon: int = 20) -> dict[str, Any]:
    """Condition -> forward-return distribution, with an unconditional baseline."""
    fwd = forward_returns(ind, horizon)
    valid = np.isfinite(fwd)
    close = pl.col("close")
    conditions: dict[str, tuple[str, pl.Expr]] = {
        "rsi_oversold": ("RSI 超卖 (<30)", pl.col("rsi14") < 30),
        "rsi_overbought": ("RSI 超买 (>70)", pl.col("rsi14") > 70),
        "ma_golden_cross": ("MA5 上穿 MA20", (pl.col("sma5") > pl.col("sma20"))
                            & (pl.col("sma5").shift(1) <= pl.col("sma20").shift(1))),
        "macd_bull_cross": ("MACD 金叉", (pl.col("macd") > pl.col("macd_signal"))
                            & (pl.col("macd").shift(1) <= pl.col("macd_signal").shift(1))),
        "near_52w_high": ("接近 52 周高点 (分位>0.95)", pl.col("pct_52w") > 0.95),
        "deep_drawdown": ("深度回撤 (<-15%)", pl.col("drawdown") < -0.15),
        "volume_spike": ("放量 (量比>2)", pl.col("volume_ratio") > 2.0),
        "below_sma60": ("跌破 MA60", (close < pl.col("sma60"))
                        & (close.shift(1) >= pl.col("sma60").shift(1))),
    }
    baseline = _stats(fwd[valid])
    events: list[dict[str, Any]] = []
    for key, (label, expr) in conditions.items():
        mask = _mask(ind, expr) & valid
        item = {"key": key, "label": label, **_stats(fwd[mask])}
        if item["samples"] and baseline["mean_pct"] is not None and item["mean_pct"] is not None:
            item["edge_vs_baseline_pct"] = round(item["mean_pct"] - baseline["mean_pct"], 2)
        else:
            item["edge_vs_baseline_pct"] = None
        events.append(item)
    return {
        "horizon_bars": horizon,
        "baseline": baseline,
        "events": events,
        "note": "条件在 bar i 收盘可观测，收益为 bar i 到 i+horizon 的收盘价变动；仅供统计研究，不含交易成本。",
    }


def seasonality_report(ind: pl.DataFrame, horizon: int = 20) -> dict[str, Any]:
    return {
        "monthly": monthly_stats(ind),
        "event_study": event_study(ind, horizon),
    }
