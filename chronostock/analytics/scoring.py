"""Deterministic multi-factor score for a point-in-time snapshot.

No model, no prompt, no randomness: six rule-based factors, each capped, summed
to 0-100. The same bars + the same config always give the same score.
"""

from __future__ import annotations

import math
from typing import Any

import polars as pl

from chronostock.analytics.indicators import INDICATOR_COLUMNS, last_values
from chronostock.analytics.regime import regime_stats, trend_state


def _clip(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _f(value: float | None, default: float = 0.0) -> float:
    if value is None or not math.isfinite(value):
        return default
    return value


def _level(name: str, value: float | None, close: float) -> dict[str, Any] | None:
    if value is None or not math.isfinite(value) or value <= 0:
        return None
    return {
        "name": name,
        "value": round(float(value), 2),
        "distance_pct": round((float(value) / close - 1.0) * 100.0, 2),
    }


def score_snapshot(ind: pl.DataFrame, source: str = "unknown") -> dict[str, Any]:
    """Score the LAST row of an indicator frame (the ``as_of`` snapshot)."""
    if ind.is_empty():
        raise ValueError("指标数据为空，无法评分")
    last = last_values(ind, ["close", *INDICATOR_COLUMNS])
    pct = regime_stats(
        ind,
        ["rsi14", "atr_pct", "vol_20", "pct_52w", "drawdown", "volume_ratio", "roc_20", "bb_pctb"],
    )
    close = _f(last.get("close"), 1.0)
    s20, s60 = _f(last.get("sma20"), close), _f(last.get("sma60"), close)
    rsi = _f(last.get("rsi14"), 50.0)
    roc20, roc60 = _f(last.get("roc_20")), _f(last.get("roc_60"))
    macd_hist = _f(last.get("macd_hist"))
    dd = _f(last.get("drawdown"))
    vol_ratio = _f(last.get("volume_ratio"), 1.0)
    pct52 = _f(last.get("pct_52w"), 0.5)

    factors: list[dict[str, Any]] = []

    trend = (
        (8.0 if close > s20 else 0.0)
        + (7.0 if close > s60 else 0.0)
        + (5.0 if s20 > s60 else 0.0)
        + (5.0 if macd_hist > 0 else 0.0)
    )
    factors.append({
        "key": "trend", "label": "趋势结构", "score": round(trend, 1), "max": 25,
        "detail": f"价格/MA20/MA60 排列 + MACD 柱 {'为正' if macd_hist > 0 else '为负'}",
    })

    momentum = _clip(0.5 + roc20 * 2.5) * 10.0 + _clip(0.5 + roc60 * 1.5) * 5.0
    if 45 <= rsi <= 70:
        momentum += 5.0
    elif 30 <= rsi < 45 or 70 < rsi <= 80:
        momentum += 2.5
    factors.append({
        "key": "momentum", "label": "动量强度", "score": round(momentum, 1), "max": 20,
        "detail": f"20 日 {roc20 * 100:.1f}% / 60 日 {roc60 * 100:.1f}% / RSI {rsi:.1f}",
    })

    position = 15.0 * _clip(pct52) - (3.0 if rsi > 78 else 0.0)
    position = max(0.0, position)
    factors.append({
        "key": "position", "label": "价格位置", "score": round(position, 1), "max": 15,
        "detail": f"52 周分位 {pct52 * 100:.1f}%" + ("，RSI 超买扣分" if rsi > 78 else ""),
    })

    vol_pct = (pct.get("vol_20") or {}).get("percentile")
    volatility = 15.0 * (1.0 - (vol_pct or 50.0) / 100.0)
    factors.append({
        "key": "volatility", "label": "波动环境", "score": round(volatility, 1), "max": 15,
        "detail": f"年化波动率历史分位 {vol_pct if vol_pct is not None else 'N/A'}%",
    })

    ratio = max(vol_ratio, 1e-6)
    volume = 10.0 * _clip(1.0 - abs(math.log(ratio / 1.3)) / math.log(3.0))
    factors.append({
        "key": "volume", "label": "量能配合", "score": round(volume, 1), "max": 10,
        "detail": f"量比 {vol_ratio:.2f}（1.3 附近视为温和放量）",
    })

    drawdown_score = 15.0 * (1.0 - _clip(abs(dd) / 0.4))
    factors.append({
        "key": "drawdown", "label": "回撤控制", "score": round(drawdown_score, 1), "max": 15,
        "detail": f"距历史高点 {dd * 100:.1f}%",
    })

    score = round(_clip(sum(f["score"] for f in factors) / 100.0) * 100.0, 1)
    if score >= 70:
        signal = "偏多"
    elif score >= 55:
        signal = "中性偏多"
    elif score >= 45:
        signal = "中性"
    elif score >= 30:
        signal = "中性偏空"
    else:
        signal = "偏空"

    candidates_support = [
        _level("MA60", s60, close),
        _level("布林下轨", last.get("bb_lower"), close),
        _level("唐奇安下轨", last.get("donchian_lower"), close),
        _level("52 周低点", last.get("low_52w"), close),
    ]
    candidates_resistance = [
        _level("布林上轨", last.get("bb_upper"), close),
        _level("唐奇安上轨", last.get("donchian_upper"), close),
        _level("52 周高点", last.get("high_52w"), close),
    ]
    supports = sorted([x for x in candidates_support if x and x["value"] < close],
                      key=lambda x: -x["value"])
    resistances = sorted([x for x in candidates_resistance if x and x["value"] > close],
                         key=lambda x: x["value"])

    caveats: list[str] = []
    if ind.height < 250:
        caveats.append(f"历史样本仅 {ind.height} 根 K 线，长周期指标（52 周分位、MA120）参考价值有限。")
    if source == "synthetic":
        caveats.append("当前使用合成演示数据，所有统计结论仅用于功能演示，不对应任何真实证券。")
    if vol_pct is not None and vol_pct > 90:
        caveats.append("波动率处于自身历史 90% 分位以上，风险敞口偏大。")
    if pct52 > 0.95 and rsi > 70:
        caveats.append("价格接近 52 周高点且 RSI 超买，追高的历史赔率通常下降。")

    return {
        "score": score,
        "signal": signal,
        "trend_state": trend_state(last),
        "factors": factors,
        "percentiles": pct,
        "indicators": last,
        "levels": {
            "nearest_support": supports[0] if supports else None,
            "nearest_resistance": resistances[0] if resistances else None,
            "support_candidates": [x for x in candidates_support if x],
            "resistance_candidates": [x for x in candidates_resistance if x],
        },
        "caveats": caveats,
        "bars_used": ind.height,
        "method": "六因子确定性打分（趋势25/动量20/位置15/波动15/量能10/回撤15）",
    }
