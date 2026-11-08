"""Grid search and rolling walk-forward validation."""

from __future__ import annotations

import pytest

from chronostock.backtest import grid_search, walk_forward


def test_grid_search_sorted_and_limited(demo_bars):
    result = grid_search(demo_bars, "ma_cross", {"fast": [5, 10], "slow": [30, 60]}, top_n=3)
    assert result["combos_evaluated"] == 4
    assert len(result["results"]) == 3
    scores = [r["score"] for r in result["results"]]
    assert scores == sorted(scores, reverse=True)
    assert "params" in result["best"] and "metrics" in result["best"]
    assert "过拟合" in result["note"]


def test_grid_invalid_combos_are_skipped(demo_bars):
    result = grid_search(demo_bars, "ma_cross", {"fast": [60, 120], "slow": [30]})
    assert result["combos_evaluated"] == 0 or result["best"] is None


def test_walk_forward_out_of_sample(demo_bars):
    result = walk_forward(
        demo_bars, "ma_cross",
        {"fast": [5, 10], "slow": [30, 60]},
        train_bars=300, test_bars=80,
    )
    assert result["fold_count"] >= 3
    assert len(result["folds"]) == result["fold_count"]
    assert result["oos_metrics"]["bars"] > 0
    assert len(result["oos_equity_curve"]) == result["oos_metrics"]["bars"]
    assert "overfit_ratio" in result
    for fold in result["folds"]:
        assert fold["train"][0] <= fold["train"][1] < fold["test"][0] <= fold["test"][1]
        assert "best_params" in fold and "oos_metrics" in fold


def test_walk_forward_requires_enough_bars(demo_bars):
    with pytest.raises(ValueError):
        walk_forward(demo_bars.head(100), "ma_cross", train_bars=300, test_bars=80)


def test_invalid_objective(demo_bars):
    with pytest.raises(ValueError):
        grid_search(demo_bars, "ma_cross", objective="not_an_objective")
