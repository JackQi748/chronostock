"""Scoring, seasonality, event study and report assembly."""

from __future__ import annotations

from chronostock.analytics import compute_indicators, score_snapshot, seasonality_report
from chronostock.report.builder import build_analysis
from chronostock.report.templates import render_html, render_markdown, render_report


def test_score_is_bounded_and_deterministic(demo_bars):
    ind = compute_indicators(demo_bars)
    a = score_snapshot(ind, "synthetic")
    b = score_snapshot(ind, "synthetic")
    assert 0 <= a["score"] <= 100
    assert a == b, "评分必须完全可复现"
    assert a["signal"] in ("偏多", "中性偏多", "中性", "中性偏空", "偏空")
    assert sum(f["max"] for f in a["factors"]) == 100
    assert a["bars_used"] == demo_bars.height
    assert "nearest_support" in a["levels"]


def test_score_flags_synthetic_data(demo_bars):
    snap = score_snapshot(compute_indicators(demo_bars), "synthetic")
    assert any("合成" in c for c in snap["caveats"])


def test_seasonality_and_events(demo_bars):
    report = seasonality_report(compute_indicators(demo_bars), horizon=20)
    assert len(report["monthly"]) == 12
    ev = report["event_study"]
    assert ev["horizon_bars"] == 20
    assert ev["baseline"]["samples"] > 0
    assert len(ev["events"]) >= 6
    for e in ev["events"]:
        assert "label" in e and "win_rate_pct" in e and "edge_vs_baseline_pct" in e


def test_report_bundle_is_self_contained(store):
    bundle = build_analysis(store, "AAPL", as_of="2022-12-30")
    assert bundle["as_of"] == "2022-12-30"
    assert bundle["data"]["fingerprint"]
    assert bundle["data"]["synthetic"] is True
    assert bundle["data"]["first_date"] < bundle["data"]["last_date"]
    assert len(bundle["strategy_comparison"]) >= 4
    assert bundle["equity_curve"]
    html = render_html(bundle)
    assert html.startswith("<!doctype html>")
    assert "<svg" in html and "免责声明" in html and "演示数据提示" in html
    assert "<script" not in html, "HTML 报告必须自包含且无外部脚本"
    md = render_markdown(bundle)
    assert "# ChronoStock" in md and "免责声明" in md
    assert render_report(bundle, "md") == md
    assert render_report(bundle, "html") == html
