"""Typer CLI - the whole system is usable without a browser or an API key."""

from __future__ import annotations

import json
import os
import webbrowser
from pathlib import Path
from typing import Any

import typer
import uvicorn

from chronostock import __version__
from chronostock.analytics import compute_indicators, indicator_frame_for_json, score_snapshot, seasonality_report
from chronostock.backtest import BacktestConfig, grid_search, list_strategies, run_strategy, walk_forward
from chronostock.config import SETTINGS, reports_dir
from chronostock.data.sources import DataSourceError, load_csv_bars
from chronostock.data.store import MarketStore
from chronostock.data.sync import sync_many
from chronostock.report.builder import build_analysis
from chronostock.report.templates import render_report

app = typer.Typer(add_completion=False, help="ChronoStock · 可复现的历史股票分析与策略回测")


@app.callback()
def root(
    db: str | None = typer.Option(None, "--db", help="Parquet 仓库路径（也可用 CHRONOSTOCK_DB）"),
) -> None:
    if db:
        os.environ["CHRONOSTOCK_DB"] = db


def _store() -> MarketStore:
    return MarketStore()


def _emit(data: Any, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(data, ensure_ascii=False, default=str, indent=2))
    else:
        typer.echo(_pretty(data))


def _pretty(obj: Any, indent: int = 0, depth: int = 0) -> str:
    pad = " " * indent
    if isinstance(obj, dict):
        lines = []
        for key, value in obj.items():
            if isinstance(value, (dict, list)) and depth < 2:
                lines.append(f"{pad}{key}:")
                lines.append(_pretty(value, indent + 2, depth + 1))
            else:
                lines.append(f"{pad}{key}: {_short(value)}")
        return "\n".join(lines)
    if isinstance(obj, list):
        if depth >= 2:
            return f"{pad}[{len(obj)} 项]"
        return "\n".join(f"{pad}- {_pretty(item, 0, depth + 1).strip()}" for item in obj[:15])
    return f"{pad}{obj}"


def _short(value: Any) -> str:
    text = str(value)
    return text if len(text) <= 160 else text[:157] + "..."


def _fail(exc: Exception) -> None:
    typer.echo(f"错误：{exc}", err=True)
    raise typer.Exit(1)


@app.command()
def version() -> None:
    typer.echo(f"ChronoStock {__version__}")


@app.command(help="写入确定性合成行情（明确标注，仅用于离线演示与测试）")
def demo(
    start: str = typer.Option(SETTINGS.default_start, "--start"),
    end: str | None = typer.Option(None, "--end"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    store = _store()
    result = sync_many(store, list(SETTINGS.demo_symbols), start, end, "synthetic")
    result["warning"] = SETTINGS.synthetic_warning
    _emit(result, as_json)


@app.command(help="从在线数据源同步历史日线（或显式 --source synthetic）")
def sync(
    symbols: str = typer.Argument(..., help="逗号分隔，如 AAPL,MSFT,600519.SH"),
    start: str = typer.Option(SETTINGS.default_start, "--start"),
    end: str | None = typer.Option(None, "--end"),
    source: str = typer.Option("auto", "--source", help="auto|yahoo|stooq|akshare|synthetic"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    try:
        result = sync_many(_store(), [s.strip() for s in symbols.split(",") if s.strip()], start, end, source)
    except (ValueError, DataSourceError) as exc:
        _fail(exc)
        return
    _emit(result, as_json)


@app.command(help="列出本地仓库中的标的与覆盖区间")
def symbols(as_json: bool = typer.Option(False, "--json")) -> None:
    store = _store()
    _emit({"symbols": store.list_symbols(), "stats": store.stats(), "warehouse": str(store.path)}, as_json)


@app.command(help="任意历史时点的多因子评分 + 历史分位 + 事件研究")
def analyze(
    symbol: str = typer.Argument(...),
    as_of: str | None = typer.Option(None, "--as-of", help="历史时点 YYYY-MM-DD"),
    start: str | None = typer.Option(None, "--start"),
    horizon: int = typer.Option(20, "--horizon"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    try:
        bars = _store().load_bars(symbol, start=start, as_of=as_of)
        if bars.height < 60:
            raise LookupError(f"{symbol} 仅有 {bars.height} 根 K 线，至少需要 60 根")
        ind = compute_indicators(bars)
        data = {
            "symbol": symbol,
            "as_of": str(bars["date"][-1]),
            "source": str(bars["source"][-1]),
            "snapshot": score_snapshot(ind, str(bars["source"][-1])),
            "seasonality": seasonality_report(ind, horizon),
        }
    except LookupError as exc:
        _fail(exc)
        return
    _emit(data, as_json)


@app.command(help="输出指标面板（JSON 可保存为文件）")
def indicators(
    symbol: str = typer.Argument(...),
    as_of: str | None = typer.Option(None, "--as-of"),
    limit: int = typer.Option(120, "--limit"),
    out: str | None = typer.Option(None, "--out", help="输出 JSON 文件路径"),
) -> None:
    try:
        bars = _store().load_bars(symbol, as_of=as_of)
        if bars.is_empty():
            raise LookupError(f"仓库中没有 {symbol}")
        rows = indicator_frame_for_json(bars, limit=limit)
    except LookupError as exc:
        _fail(exc)
        return
    text = json.dumps(rows, ensure_ascii=False, default=str)
    if out:
        Path(out).write_text(text, encoding="utf-8")
        typer.echo(f"已写出 {len(rows)} 行 -> {out}")
    else:
        typer.echo(text)


@app.command(help="单策略回测，输出绩效指标与成交流水")
def backtest(
    symbol: str = typer.Argument(...),
    strategy: str = typer.Option("dual_momentum", "--strategy"),
    params: str = typer.Option("{}", "--params", help='JSON，如 \'{"fast":10,"slow":60}\''),
    start: str | None = typer.Option(None, "--start"),
    end: str | None = typer.Option(None, "--end"),
    as_of: str | None = typer.Option(None, "--as-of"),
    capital: float = typer.Option(100_000.0, "--capital"),
    commission_bps: float = typer.Option(5.0, "--commission-bps"),
    slippage_bps: float = typer.Option(2.0, "--slippage-bps"),
    allow_short: bool = typer.Option(False, "--allow-short"),
    stop_loss: float | None = typer.Option(None, "--stop-loss"),
    take_profit: float | None = typer.Option(None, "--take-profit"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    try:
        overrides = json.loads(params)
        bars = _store().load_bars(symbol, start=start, end=end, as_of=as_of)
        if bars.height < 10:
            raise LookupError(f"{symbol} 历史数据不足")
        cfg = BacktestConfig(
            initial_capital=capital, commission_bps=commission_bps, slippage_bps=slippage_bps,
            allow_short=allow_short, stop_loss=stop_loss, take_profit=take_profit,
        )
        result = run_strategy(bars, strategy, overrides, cfg)
        payload = result.to_dict(curve_limit=3000) if as_json else {
            "strategy": result.strategy, "params": result.params,
            "metrics": result.metrics, "trades_tail": result.trades[-10:],
        }
    except (ValueError, LookupError, json.JSONDecodeError) as exc:
        _fail(exc)
        return
    _emit(payload, as_json)


@app.command(help="参数网格搜索 / 滚动走步样本外验证")
def optimize(
    symbol: str = typer.Argument(...),
    strategy: str = typer.Option("ma_cross", "--strategy"),
    grid: str = typer.Option("{}", "--grid", help='JSON，如 \'{"fast":[5,10],"slow":[30,60]}\''),
    objective: str = typer.Option("sharpe", "--objective"),
    top: int = typer.Option(8, "--top"),
    as_of: str | None = typer.Option(None, "--as-of"),
    walk: bool = typer.Option(False, "--walk-forward/--no-walk-forward"),
    train_bars: int = typer.Option(500, "--train-bars"),
    test_bars: int = typer.Option(120, "--test-bars"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    try:
        param_grid = json.loads(grid)
        bars = _store().load_bars(symbol, as_of=as_of)
        if bars.height < 60:
            raise LookupError(f"{symbol} 历史数据不足")
        if walk:
            payload = walk_forward(bars, strategy, param_grid or None, BacktestConfig(),
                                   objective, train_bars, test_bars)
        else:
            payload = grid_search(bars, strategy, param_grid or None, BacktestConfig(), objective, top)
    except (ValueError, LookupError, json.JSONDecodeError) as exc:
        _fail(exc)
        return
    _emit(payload, as_json)


@app.command(help="月度季节性与条件事件研究")
def seasonality(
    symbol: str = typer.Argument(...),
    as_of: str | None = typer.Option(None, "--as-of"),
    horizon: int = typer.Option(20, "--horizon"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    try:
        bars = _store().load_bars(symbol, as_of=as_of)
        if bars.height < 60:
            raise LookupError(f"{symbol} 历史数据不足")
        _emit(seasonality_report(compute_indicators(bars), horizon), as_json)
    except LookupError as exc:
        _fail(exc)
        return


@app.command(help="生成自包含 HTML / Markdown 研究报告")
def report(
    symbol: str = typer.Argument(...),
    as_of: str | None = typer.Option(None, "--as-of"),
    format: str = typer.Option("html", "--format", help="html|md"),
    out: str | None = typer.Option(None, "--out"),
    open_browser: bool = typer.Option(False, "--open"),
) -> None:
    try:
        bundle = build_analysis(_store(), symbol, as_of=as_of)
        text = render_report(bundle, format)
    except (LookupError, ValueError) as exc:
        _fail(exc)
        return
    suffix = "html" if format == "html" else "md"
    path = Path(out) if out else reports_dir() / f"{symbol}_{bundle['as_of']}.{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    typer.echo(f"报告已生成：{path}（{len(text)} 字符，数据指纹 {bundle['data']['fingerprint']}）")
    if bundle["data"]["synthetic"]:
        typer.echo(f"⚠️ {SETTINGS.synthetic_warning}")
    if open_browser:
        webbrowser.open(path.resolve().as_uri())


@app.command(help="导出整个仓库为 Parquet 快照")
def export(path: str = typer.Argument(...)) -> None:
    out = _store().export_snapshot(path)
    typer.echo(f"快照已导出：{out}")


@app.command(name="import-csv", help="从本地 CSV 导入真实历史行情（支持中英文表头）")
def import_csv(
    path: str = typer.Argument(...),
    symbol: str | None = typer.Option(None, "--symbol", help="标的代码，默认取文件名"),
) -> None:
    try:
        bars = load_csv_bars(path, symbol)
        count = _store().upsert(bars)
    except (DataSourceError, FileNotFoundError, ValueError) as exc:
        _fail(exc)
        return
    typer.echo(f"已导入 {bars['symbol'][0]}：{count} 根 K 线，"
               f"{bars['date'].min()} ~ {bars['date'].max()}（来源标记为 csv）")


@app.command(help="导入 Parquet 快照（按 symbol+date 幂等覆盖）")
def import_snapshot(path: str = typer.Argument(...)) -> None:
    try:
        total = _store().import_snapshot(path)
    except FileNotFoundError as exc:
        _fail(exc)
        return
    typer.echo(f"导入完成，当前仓库共 {total} 根 K 线")


@app.command(help="列出所有可执行策略")
def strategies(as_json: bool = typer.Option(False, "--json")) -> None:
    _emit({"strategies": list_strategies()}, as_json)


@app.command(help="启动 Web 工作台与 REST API")
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
) -> None:
    typer.echo(f"ChronoStock 工作台：http://{host}:{port}  (API 文档 /docs)")
    uvicorn.run("chronostock.api.app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    app()
