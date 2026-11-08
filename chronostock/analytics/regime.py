"""Historical percentile / regime diagnostics."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import polars as pl


def percentile_rank(values: pl.Series, current: float | None) -> float | None:
    """Where does ``current`` sit inside its own full-history distribution?"""
    if current is None or not math.isfinite(current):
        return None
    clean = values.drop_nulls()
    try:
        arr = clean.drop_nans().to_numpy()
    except (AttributeError, TypeError):
        arr = clean.to_numpy()
    if arr.size == 0:
        return None
    return round(float((arr <= current).mean() * 100.0), 1)


DEFAULT_PERCENTILE_FIELDS = [
    "rsi14",
    "atr_pct",
    "vol_20",
    "pct_52w",
    "drawdown",
    "volume_ratio",
    "roc_20",
    "bb_pctb",
]


def regime_stats(
    ind: pl.DataFrame, fields: list[str] | None = None, window: int | None = None
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    frame = ind.tail(window) if window else ind
    last = frame.row(-1, named=True) if frame.height else {}
    for field in fields or DEFAULT_PERCENTILE_FIELDS:
        if field not in frame.columns:
            continue
        value = last.get(field)
        try:
            value_f = float(value) if value is not None else None
        except (TypeError, ValueError):
            value_f = None
        out[field] = {
            "value": value_f if value_f is not None and math.isfinite(value_f) else None,
            "percentile": percentile_rank(frame[field], value_f),
            "samples": int(frame[field].drop_nulls().len()),
        }
    return out


def trend_state(last: dict[str, float | None]) -> str:
    """Readable, rule-based trend classification - no LLM, fully reproducible."""
    close = last.get("close")
    s5, s20, s60 = last.get("sma5"), last.get("sma20"), last.get("sma60")
    hist = last.get("macd_hist")
    if any(v is None for v in (close, s5, s20, s60)):
        return "数据不足"
    assert close is not None and s5 is not None and s20 is not None and s60 is not None
    if close > s5 > s20 > s60 and (hist or 0) > 0:
        return "多头排列（趋势向上）"
    if close < s5 < s20 < s60 and (hist or 0) < 0:
        return "空头排列（趋势向下）"
    if close > s60 and (s20 or 0) > (s60 or 0):
        return "偏多震荡"
    if close < s60 and (s20 or 0) < (s60 or 0):
        return "偏空震荡"
    return "均线纠缠（方向不明）"


def drawdown_series(equity: list[float]) -> list[float]:
    arr = np.asarray(equity, dtype=float)
    if arr.size == 0:
        return []
    peak = np.maximum.accumulate(arr)
    return (arr / peak - 1.0).tolist()
