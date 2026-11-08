"""Vectorised technical indicators (Polars expressions).

Every expression is backward-looking: rolling windows, Wilder EWM and forward
fill of state. Nothing reads row i+1, which is the precondition for the
point-in-time replay and the no-lookahead backtests.
"""

from __future__ import annotations

import math
from typing import Any

import polars as pl

SQRT252 = math.sqrt(252.0)


def sma(window: int, col: str = "close") -> pl.Expr:
    return pl.col(col).rolling_mean(window_size=window, min_samples=1)


def ema(span: int, col: str = "close") -> pl.Expr:
    return pl.col(col).ewm_mean(span=span, adjust=False, min_samples=1)


def rsi_expr(period: int = 14, col: str = "close") -> pl.Expr:
    """Wilder's RSI implemented with recursive EWM (alpha = 1/period)."""
    delta = pl.col(col).diff()
    gain = pl.when(delta > 0).then(delta).otherwise(0.0)
    loss = pl.when(delta < 0).then(-delta).otherwise(0.0)
    avg_gain = gain.ewm_mean(alpha=1.0 / period, adjust=False, min_samples=1)
    avg_loss = loss.ewm_mean(alpha=1.0 / period, adjust=False, min_samples=1)
    return pl.when(avg_loss == 0).then(
        pl.when(avg_gain == 0).then(50.0).otherwise(100.0)
    ).otherwise(100.0 - 100.0 / (1.0 + avg_gain / avg_loss))


def rolling_std(window: int, col: str = "close", ddof: int = 0) -> pl.Expr:
    return pl.col(col).rolling_std(window_size=window, min_samples=1, ddof=ddof)


def zscore_expr(window: int = 20, col: str = "close") -> pl.Expr:
    mean = sma(window, col)
    sd = rolling_std(window, col)
    return pl.when(sd > 0).then((pl.col(col) - mean) / sd).otherwise(0.0)


def roc_expr(window: int, col: str = "close") -> pl.Expr:
    return (pl.col(col) / pl.col(col).shift(window) - 1.0).fill_null(0.0)


def rsi_series(df: pl.DataFrame, period: int = 14) -> pl.Series:
    return df.select(rsi_expr(period).alias("rsi")).to_series()


INDICATOR_COLUMNS = [
    "sma5", "sma10", "sma20", "sma60", "sma120",
    "ema12", "ema26", "macd", "macd_signal", "macd_hist",
    "rsi14", "bb_mid", "bb_upper", "bb_lower", "bb_pctb", "bb_width",
    "true_range", "atr14", "atr_pct", "vol_20", "vol_60",
    "donchian_upper", "donchian_lower", "obv", "kdj_k", "kdj_d", "kdj_j", "cci14",
    "roc_20", "roc_60", "roc_120", "zscore_20", "drawdown",
    "high_52w", "low_52w", "pct_52w", "volume_ma20", "volume_ratio",
    "ma_bias_20", "sma20_slope", "ret_1d",
]


def compute_indicators(df: pl.DataFrame, periods: dict[str, int] | None = None) -> pl.DataFrame:
    """Append the full indicator panel to a canonical OHLCV frame."""
    p = {"rsi": 14, "atr": 14, "boll": 20, "donchian": 20, "kdj": 9, "cci": 14}
    p.update(periods or {})

    close, high, low, vol = pl.col("close"), pl.col("high"), pl.col("low"), pl.col("volume")
    prev_close = close.shift(1)
    ret1 = (close / prev_close - 1.0).fill_null(0.0)

    tr = pl.max_horizontal(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()]
    ).fill_null(high - low)
    atr = tr.ewm_mean(alpha=1.0 / p["atr"], adjust=False, min_samples=1)

    ema12, ema26 = ema(12), ema(26)
    macd = ema12 - ema26
    macd_signal = macd.ewm_mean(span=9, adjust=False, min_samples=1)

    bb_mid = sma(p["boll"])
    bb_sd = rolling_std(p["boll"])
    bb_upper, bb_lower = bb_mid + 2.0 * bb_sd, bb_mid - 2.0 * bb_sd

    lowest = low.rolling_min(window_size=p["kdj"], min_samples=1)
    highest = high.rolling_max(window_size=p["kdj"], min_samples=1)
    rsv = pl.when(highest > lowest).then((close - lowest) / (highest - lowest) * 100.0).otherwise(50.0)
    kdj_k = rsv.ewm_mean(alpha=1.0 / 3, adjust=False, min_samples=1)
    kdj_d = kdj_k.ewm_mean(alpha=1.0 / 3, adjust=False, min_samples=1)

    tp = (high + low + close) / 3.0
    tp_mid = tp.rolling_mean(window_size=p["cci"], min_samples=1)
    tp_sd = tp.rolling_std(window_size=p["cci"], min_samples=1, ddof=0)

    high_52w = close.rolling_max(window_size=252, min_samples=1)
    low_52w = close.rolling_min(window_size=252, min_samples=1)
    vol_ma20 = vol.rolling_mean(window_size=20, min_samples=1)
    sma20 = sma(20)

    obv = (
        pl.when(close > prev_close).then(vol)
        .when(close < prev_close).then(-vol)
        .otherwise(0.0)
        .cum_sum()
    )

    out = df.with_columns(
        sma(5).alias("sma5"),
        sma(10).alias("sma10"),
        sma20.alias("sma20"),
        sma(60).alias("sma60"),
        sma(120).alias("sma120"),
        ema12.alias("ema12"),
        ema26.alias("ema26"),
        macd.alias("macd"),
        macd_signal.alias("macd_signal"),
        (macd - macd_signal).alias("macd_hist"),
        rsi_expr(p["rsi"]).alias("rsi14"),
        bb_mid.alias("bb_mid"),
        bb_upper.alias("bb_upper"),
        bb_lower.alias("bb_lower"),
        pl.when(bb_upper > bb_lower)
        .then((close - bb_lower) / (bb_upper - bb_lower))
        .otherwise(0.5)
        .alias("bb_pctb"),
        pl.when(bb_mid > 0).then((bb_upper - bb_lower) / bb_mid).otherwise(0.0).alias("bb_width"),
        tr.alias("true_range"),
        atr.alias("atr14"),
        (atr / close).alias("atr_pct"),
        (ret1.rolling_std(window_size=20, min_samples=2, ddof=0) * SQRT252).fill_null(0.0).alias("vol_20"),
        (ret1.rolling_std(window_size=60, min_samples=2, ddof=0) * SQRT252).fill_null(0.0).alias("vol_60"),
        # shift(1) keeps the channel strictly prior-bar; early nulls are filled
        # with the same backward-looking rolling value (never with future rows).
        high.rolling_max(window_size=p["donchian"], min_samples=1).shift(1)
        .fill_null(high.rolling_max(window_size=p["donchian"], min_samples=1))
        .alias("donchian_upper"),
        low.rolling_min(window_size=p["donchian"], min_samples=1).shift(1)
        .fill_null(low.rolling_min(window_size=p["donchian"], min_samples=1))
        .alias("donchian_lower"),
        obv.alias("obv"),
        kdj_k.alias("kdj_k"),
        kdj_d.alias("kdj_d"),
        (3.0 * kdj_k - 2.0 * kdj_d).alias("kdj_j"),
        pl.when(tp_sd > 0).then((tp - tp_mid) / (0.015 * tp_sd)).otherwise(0.0).alias("cci14"),
        roc_expr(20).alias("roc_20"),
        roc_expr(60).alias("roc_60"),
        roc_expr(120).alias("roc_120"),
        zscore_expr(20).alias("zscore_20"),
        (close / close.cum_max() - 1.0).alias("drawdown"),
        high_52w.alias("high_52w"),
        low_52w.alias("low_52w"),
        pl.when(high_52w > low_52w)
        .then((close - low_52w) / (high_52w - low_52w))
        .otherwise(0.5)
        .alias("pct_52w"),
        vol_ma20.alias("volume_ma20"),
        pl.when(vol_ma20 > 0).then(vol / vol_ma20).otherwise(1.0).alias("volume_ratio"),
        (close / sma20 - 1.0).alias("ma_bias_20"),
        ((sma20 - sma20.shift(5)) / sma20.shift(5)).fill_null(0.0).alias("sma20_slope"),
        ret1.alias("ret_1d"),
    )
    return out


def last_values(ind: pl.DataFrame, columns: list[str] | None = None) -> dict[str, float | None]:
    """Last row as plain floats (JSON-safe, None instead of NaN)."""
    cols = columns or INDICATOR_COLUMNS
    if ind.is_empty():
        return {c: None for c in cols}
    row = ind.select([pl.col(c) for c in cols]).row(-1, named=True)
    clean: dict[str, float | None] = {}
    for key, value in row.items():
        if value is None:
            clean[key] = None
            continue
        f = float(value)
        clean[key] = f if math.isfinite(f) else None
    return clean


def indicator_frame_for_json(bars: pl.DataFrame, limit: int | None = None) -> list[dict[str, Any]]:
    """Serialize OHLCV + indicators, dropping internal columns."""
    ind = compute_indicators(bars)
    if limit:
        ind = ind.tail(limit)
    keep = ["date", "open", "high", "low", "close", "volume", *INDICATOR_COLUMNS]
    rows: list[dict[str, Any]] = []
    for row in ind.select(keep).iter_rows(named=True):
        item: dict[str, Any] = {}
        for key, value in row.items():
            if hasattr(value, "isoformat"):
                item[key] = value.isoformat()
            elif isinstance(value, float) and not math.isfinite(value):
                item[key] = None
            else:
                item[key] = value
        rows.append(item)
    return rows
