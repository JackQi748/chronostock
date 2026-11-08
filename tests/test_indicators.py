"""Indicator correctness and the strict no-lookahead invariant."""

from __future__ import annotations

from chronostock.analytics import compute_indicators, indicator_frame_for_json, last_values
from chronostock.analytics.indicators import INDICATOR_COLUMNS
from chronostock.analytics.regime import percentile_rank
from chronostock.backtest import list_strategies, signals_for
from tests.conftest import constant_bars


def test_indicator_columns_and_shape(demo_bars):
    ind = compute_indicators(demo_bars)
    assert ind.height == demo_bars.height
    for col in INDICATOR_COLUMNS:
        assert col in ind.columns, col
    assert ind["close"].tail(1)[0] == demo_bars["close"].tail(1)[0]


def test_indicators_on_constant_series():
    bars = constant_bars()
    ind = compute_indicators(bars)
    assert abs(ind["sma20"][-1] - 100.0) < 1e-9
    assert abs(ind["macd"][-1]) < 1e-9
    assert abs(ind["bb_upper"][-1] - 100.0) < 1e-9
    assert abs(ind["drawdown"][-1]) < 1e-9
    assert ind["rsi14"][-1] == 50.0


def test_no_lookahead_indicators(demo_bars):
    """Truncating future bars must not change any earlier indicator value."""
    full = compute_indicators(demo_bars)
    truncated = compute_indicators(demo_bars.head(300))
    for col in INDICATOR_COLUMNS:
        a, b = full[col].head(300).to_list(), truncated[col].to_list()
        assert len(a) == len(b) == 300
        for x, y in zip(a, b):
            if x is None or y is None:
                assert x is None and y is None, col
            else:
                assert abs(float(x) - float(y)) < 1e-9, f"{col} 存在未来函数"


def test_all_strategies_no_lookahead(demo_bars):
    head = demo_bars.head(320)
    for spec in list_strategies():
        full = signals_for(demo_bars, spec["name"]).head(320).to_list()
        trunc = signals_for(head, spec["name"]).to_list()
        assert full == trunc, f"{spec['name']} 信号依赖了未来数据"


def test_json_serialisation_and_percentile(demo_bars):
    rows = indicator_frame_for_json(demo_bars, limit=20)
    assert len(rows) == 20
    assert isinstance(rows[0]["date"], str)
    assert "rsi14" in rows[0]
    values = last_values(compute_indicators(demo_bars))
    assert 0 <= values["rsi14"] <= 100
    assert percentile_rank(demo_bars["close"], float(demo_bars["close"][-1])) is not None
