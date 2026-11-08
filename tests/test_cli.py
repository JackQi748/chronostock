"""CLI end-to-end: demo sync -> analysis -> backtest -> report."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from chronostock import __version__
from chronostock.cli import app

runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_full_cli_flow(tmp_path, monkeypatch):
    db = tmp_path / "cli.parquet"
    monkeypatch.setenv("CHRONOSTOCK_DB", str(db))

    demo = runner.invoke(app, ["demo", "--start", "2021-01-01", "--end", "2022-12-31"])
    assert demo.exit_code == 0, demo.output
    assert "AAPL" in demo.output

    syms = runner.invoke(app, ["symbols", "--json"])
    assert syms.exit_code == 0
    assert len(json.loads(syms.output)["symbols"]) >= 1

    ana = runner.invoke(app, ["analyze", "AAPL", "--as-of", "2022-06-30", "--json"])
    assert ana.exit_code == 0, ana.output
    body = json.loads(ana.output)
    assert body["as_of"] <= "2022-06-30"
    assert 0 <= body["snapshot"]["score"] <= 100

    bt = runner.invoke(app, ["backtest", "AAPL", "--strategy", "ma_cross", "--as-of", "2022-12-30"])
    assert bt.exit_code == 0, bt.output
    assert "total_return_pct" in bt.output

    out = tmp_path / "report.html"
    rep = runner.invoke(app, ["report", "AAPL", "--as-of", "2022-12-30", "--out", str(out)])
    assert rep.exit_code == 0, rep.output
    assert out.exists() and "免责声明" in out.read_text(encoding="utf-8")

    opt = runner.invoke(app, ["optimize", "AAPL", "--strategy", "ma_cross",
                              "--grid", '{"fast":[5,10],"slow":[30,60]}', "--json"])
    assert opt.exit_code == 0, opt.output
    assert json.loads(opt.output)["combos_evaluated"] == 4


def test_cli_error_handling(tmp_path, monkeypatch):
    monkeypatch.setenv("CHRONOSTOCK_DB", str(tmp_path / "empty.parquet"))
    result = runner.invoke(app, ["analyze", "NOPE"])
    assert result.exit_code == 1
    assert "错误" in result.output
