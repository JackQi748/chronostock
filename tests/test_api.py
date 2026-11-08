"""REST API contract tests."""

from __future__ import annotations


def test_health_and_symbols(api_client):
    r = api_client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["bars"] > 0
    r = api_client.get("/api/symbols")
    assert r.status_code == 200
    assert r.json()["symbols"][0]["symbol"] == "AAPL"


def test_bars_and_indicators(api_client):
    r = api_client.get("/api/bars", params={"symbol": "AAPL", "as_of": "2021-12-31"})
    assert r.status_code == 200 and len(r.json()) > 100
    assert r.json()[-1]["date"] <= "2021-12-31"
    r = api_client.get("/api/indicators", params={"symbol": "AAPL", "limit": 30})
    assert r.status_code == 200 and len(r.json()) == 30
    assert "macd_hist" in r.json()[0] and "donchian_upper" in r.json()[0]


def test_analyze_and_seasonality(api_client):
    r = api_client.get("/api/analyze", params={"symbol": "AAPL", "as_of": "2022-06-30"})
    assert r.status_code == 200
    body = r.json()
    assert body["as_of"] <= "2022-06-30"
    assert 0 <= body["snapshot"]["score"] <= 100
    assert body["synthetic"] is True
    r = api_client.get("/api/seasonality", params={"symbol": "AAPL"})
    assert r.status_code == 200 and len(r.json()["monthly"]) == 12


def test_backtest_endpoint(api_client):
    payload = {"symbol": "AAPL", "strategy": "dual_momentum", "as_of": "2023-06-30"}
    r = api_client.post("/api/backtest", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["metrics"]["bars"] > 0
    assert body["equity_curve"] and body["config"]["commission_bps"] == 5.0
    bad = api_client.post("/api/backtest", json={"symbol": "AAPL", "strategy": "nope"})
    assert bad.status_code == 400


def test_optimize_endpoint(api_client):
    r = api_client.post("/api/optimize", json={
        "symbol": "AAPL", "strategy": "ma_cross",
        "grid": {"fast": [5, 10], "slow": [30, 60]}, "top_n": 3,
    })
    assert r.status_code == 200 and r.json()["combos_evaluated"] == 4
    wf = api_client.post("/api/optimize", json={
        "symbol": "AAPL", "strategy": "ma_cross",
        "grid": {"fast": [5, 10], "slow": [30, 60]},
        "walk_forward": True, "train_bars": 300, "test_bars": 80,
    })
    assert wf.status_code == 200 and wf.json()["fold_count"] >= 3


def test_report_endpoints(api_client):
    html = api_client.get("/api/report", params={"symbol": "AAPL", "format": "html"})
    assert html.status_code == 200 and "<svg" in html.text and "免责声明" in html.text
    md = api_client.get("/api/report", params={"symbol": "AAPL", "format": "md"})
    assert md.status_code == 200 and "# ChronoStock" in md.text
    data = api_client.post("/api/report-data", json={"symbol": "AAPL", "as_of": "2022-12-30"})
    assert data.status_code == 200 and data.json()["data"]["fingerprint"]


def test_missing_symbol_returns_404(api_client):
    assert api_client.get("/api/analyze", params={"symbol": "NOPE"}).status_code == 404
    assert api_client.get("/api/report", params={"symbol": "NOPE"}).status_code == 404


def test_workbench_is_served(api_client):
    r = api_client.get("/")
    assert r.status_code == 200 and "ChronoStock" in r.text
    assert api_client.get("/app.js").status_code == 200
