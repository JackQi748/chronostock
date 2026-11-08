"""Self-contained HTML / Markdown report renderers.

The HTML report embeds its own SVG charts - no CDN, no JavaScript, no network.
It can be archived, diffed and opened ten years from now.
"""

from __future__ import annotations

import html
import math
from typing import Any

CSS = """
:root { --ink:#16202c; --muted:#64748b; --line:#e2e8f0; --bg:#f5f7fa;
        --card:#ffffff; --accent:#1d4ed8; --up:#dc2626; --down:#16a34a; }
* { box-sizing:border-box; }
body { margin:0; padding:32px 18px 60px; background:var(--bg); color:var(--ink);
       font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",Segoe UI,sans-serif;
       line-height:1.65; }
.wrap { max-width:980px; margin:0 auto; }
header { border-bottom:3px solid var(--accent); padding-bottom:18px; margin-bottom:24px; }
h1 { font-size:26px; margin:0 0 6px; }
h2 { font-size:19px; margin:0 0 14px; padding-left:10px; border-left:4px solid var(--accent); }
h3 { font-size:15px; margin:18px 0 8px; }
.sub { color:var(--muted); font-size:13px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px;
        padding:20px 22px; margin-bottom:18px; box-shadow:0 1px 3px rgba(15,23,42,.04); }
.warn { background:#fff7ed; border:1px solid #fdba74; color:#9a3412; padding:12px 16px;
        border-radius:8px; margin-bottom:18px; font-size:13px; }
.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:12px; }
.kpi { background:#f8fafc; border:1px solid var(--line); border-radius:8px; padding:12px 14px; }
.kpi .label { font-size:12px; color:var(--muted); }
.kpi .value { font-size:21px; font-weight:650; margin-top:2px; }
table { width:100%; border-collapse:collapse; font-size:13px; }
th,td { padding:8px 10px; border-bottom:1px solid var(--line); text-align:right; }
th:first-child,td:first-child { text-align:left; }
th { background:#f8fafc; color:var(--muted); font-weight:600; }
tr:hover td { background:#fafbff; }
.bar-row { display:flex; align-items:center; gap:10px; margin:7px 0; font-size:13px; }
.bar-label { width:90px; color:var(--muted); }
.bar-track { flex:1; height:9px; background:#eef2f7; border-radius:6px; overflow:hidden; }
.bar-fill { height:100%; background:var(--accent); border-radius:6px; }
.bar-val { width:70px; text-align:right; font-variant-numeric:tabular-nums; }
.pos { color:var(--up); } .neg { color:var(--down); }
.note { font-size:12.5px; color:var(--muted); }
footer { margin-top:26px; padding:16px; background:#fff7ed; border:1px solid #fed7aa;
         border-radius:8px; font-size:13px; color:#7c2d12; }
svg { display:block; width:100%; height:auto; }
.legend { font-size:12px; color:var(--muted); margin-bottom:6px; }
code { background:#f1f5f9; padding:1px 5px; border-radius:4px; font-size:12px; }
"""


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _num(value: Any, digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "—"
    try:
        f = float(value)
        if not math.isfinite(f):
            return "—"
        return f"{f:.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return "—"


def _sign(value: Any, digits: int = 2, suffix: str = "%") -> str:
    if value is None:
        return "—"
    f = float(value)
    cls = "pos" if f > 0 else ("neg" if f < 0 else "")
    return f'<span class="{cls}">{f:+.{digits}f}{suffix}</span>'


def _svg_lines(series: list[tuple[str, list[float], str]], width: int = 900, height: int = 240) -> str:
    usable = [v for _, arr, _ in series for v in arr if v is not None and math.isfinite(v)]
    if not usable:
        return '<p class="note">数据不足，无法绘制曲线。</p>'
    lo, hi = min(usable), max(usable)
    if hi - lo < 1e-9:
        hi = lo + 1.0
    pad = (hi - lo) * 0.08
    lo, hi = lo - pad, hi + pad
    left, right, top, bottom = 58, 14, 14, 28
    plot_w, plot_h = width - left - right, height - top - bottom

    def y_of(v: float) -> float:
        return top + (hi - v) / (hi - lo) * plot_h

    def x_of(i: int, n: int) -> float:
        return left + (i / max(1, n - 1)) * plot_w

    chunks = [f'<svg viewBox="0 0 {width} {height}" role="img">']
    for frac in (0, 0.25, 0.5, 0.75, 1.0):
        y = top + frac * plot_h
        val = hi - frac * (hi - lo)
        chunks.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}" stroke="#e8edf3"/>')
        chunks.append(f'<text x="{left - 6}" y="{y + 4:.1f}" text-anchor="end" font-size="10" fill="#94a3b8">{val:.2f}</text>')
    for label, arr, color in series:
        n = len(arr)
        pts = " ".join(
            f"{x_of(i, n):.1f},{y_of(float(v)):.1f}"
            for i, v in enumerate(arr)
            if v is not None and math.isfinite(float(v))
        )
        chunks.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.8" stroke-linejoin="round"/>')
    chunks.append("</svg>")
    legend = " · ".join(
        f'<span style="color:{color}">■</span> {_esc(label)}' for label, _, color in series
    )
    return f'<div class="legend">{legend}</div>' + "".join(chunks)


def _svg_bars(labels: list[str], values: list[float], width: int = 900, height: int = 190) -> str:
    if not values:
        return ""
    limit = max(abs(min(values)), abs(max(values)), 1e-6) * 1.2
    left, right, top, bottom = 40, 14, 12, 26
    plot_h = height - top - bottom
    zero = top + plot_h / 2
    slot = (width - left - right) / len(values)
    bw = slot * 0.58
    chunks = [f'<svg viewBox="0 0 {width} {height}" role="img">']
    chunks.append(f'<line x1="{left}" y1="{zero}" x2="{width - right}" y2="{zero}" stroke="#cbd5e1"/>')
    for i, (label, v) in enumerate(zip(labels, values)):
        h = abs(v) / limit * (plot_h / 2)
        y = zero - h if v >= 0 else zero
        color = "#dc2626" if v >= 0 else "#16a34a"
        x = left + i * slot + (slot - bw) / 2
        chunks.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{max(h, 1):.1f}" fill="{color}" rx="2"/>')
        chunks.append(f'<text x="{x + bw / 2:.1f}" y="{height - 8}" text-anchor="middle" font-size="10" fill="#64748b">{_esc(label)}</text>')
    chunks.append(f'<text x="4" y="{zero + 4}" font-size="10" fill="#94a3b8">0</text>')
    chunks.append("</svg>")
    return "".join(chunks)


def _gauge(score: float) -> str:
    r, c = 52, 2 * math.pi * 52
    filled = c * max(0.0, min(100.0, score)) / 100.0
    color = "#dc2626" if score >= 70 else ("#f59e0b" if score >= 45 else "#16a34a")
    return (
        f'<svg viewBox="0 0 140 140" style="width:150px;height:150px">'
        f'<circle cx="70" cy="70" r="{r}" fill="none" stroke="#e8edf3" stroke-width="12"/>'
        f'<circle cx="70" cy="70" r="{r}" fill="none" stroke="{color}" stroke-width="12" '
        f'stroke-linecap="round" stroke-dasharray="{filled:.1f} {c:.1f}" transform="rotate(-90 70 70)"/>'
        f'<text x="70" y="76" text-anchor="middle" font-size="30" font-weight="700" fill="#16202c">{score:.1f}</text>'
        f'<text x="70" y="96" text-anchor="middle" font-size="11" fill="#64748b">/ 100</text></svg>'
    )


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def render_html(data: dict[str, Any]) -> str:
    snap = data["snapshot"]
    d = data["data"]
    parts: list[str] = [
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">",
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>ChronoStock 历史分析 · {_esc(data['symbol'])} · {_esc(data['as_of'])}</title>",
        f"<style>{CSS}</style></head><body><div class=\"wrap\">",
        "<header>",
        f"<h1>ChronoStock 历史股票分析报告 · {_esc(data['symbol'])}</h1>",
        f"<div class=\"sub\">历史时点 as_of = <b>{_esc(data['as_of'])}</b> · "
        f"数据区间 {_esc(d['first_date'])} ~ {_esc(d['last_date'])}（{d['bars']} 根日线） · "
        f"数据源 {_esc(', '.join(d['sources']))} · 生成于 {_esc(data['generated_at'])}</div>",
        "</header>",
    ]
    if data.get("warning"):
        parts.append(f'<div class="warn"><b>演示数据提示：</b>{_esc(data["warning"])}</div>')

    # score
    factors_html = "".join(
        f'<div class="bar-row"><div class="bar-label">{_esc(f["label"])}</div>'
        f'<div class="bar-track"><div class="bar-fill" style="width:{min(100, f["score"] / f["max"] * 100):.0f}%"></div></div>'
        f'<div class="bar-val">{_num(f["score"], 1)} / {f["max"]}</div></div>'
        f'<div class="note" style="margin:-4px 0 6px 100px">{_esc(f["detail"])}</div>'
        for f in snap["factors"]
    )
    parts.append(
        '<section class="card"><h2>综合评分与结论</h2><div style="display:flex;gap:26px;align-items:center;flex-wrap:wrap">'
        f'{_gauge(float(snap["score"]))}'
        f'<div><div style="font-size:22px;font-weight:650">信号：{_esc(snap["signal"])}</div>'
        f'<div class="sub">趋势状态：{_esc(snap["trend_state"])} · 样本 {snap["bars_used"]} 根</div>'
        f'<div class="note">{_esc(snap["method"])}</div></div></div>'
        f'<h3>六因子拆解</h3>{factors_html}'
    )
    if snap["caveats"]:
        parts.append("<h3>风险提示</h3><ul>" + "".join(f"<li>{_esc(c)}</li>" for c in snap["caveats"]) + "</ul>")
    parts.append("</section>")

    # price chart
    pc = data["price_chart"]
    parts.append(
        '<section class="card"><h2>历史价格与均线（最近 250 根）</h2>'
        + _svg_lines([("收盘价", pc["close"], "#1d4ed8"), ("MA20", pc["sma20"], "#f59e0b"), ("MA60", pc["sma60"], "#0f766e")])
        + '<div class="note">图示为 as_of 之前的实际窗口，未包含任何未来数据。</div></section>'
    )

    # key levels + percentiles
    lv = snap["levels"]
    rows = []
    for item in lv["support_candidates"] + lv["resistance_candidates"]:
        rows.append([_esc(item["name"]), _num(item["value"]), _sign(item["distance_pct"])])
    pct_rows = []
    labels = {"rsi14": "RSI14", "atr_pct": "ATR / 价格", "vol_20": "年化波动率", "pct_52w": "52 周价格分位",
              "drawdown": "距历史高点", "volume_ratio": "量比", "roc_20": "20 日动量", "bb_pctb": "布林 %B"}
    for key, item in snap["percentiles"].items():
        pct_rows.append([_esc(labels.get(key, key)), _num(item["value"], 4), _num(item["percentile"], 1, "%"), item["samples"]])
    parts.append(
        '<section class="card"><h2>关键价位与历史分位</h2><div class="grid">'
        f'<div><h3>关键价位</h3>{_table(["位置", "价格", "距现价"], rows)}</div>'
        f'<div><h3>当前值在全历史中的分位</h3>{_table(["指标", "当前值", "历史分位", "样本数"], pct_rows)}</div>'
        "</div></section>"
    )

    # seasonality
    monthly = data["seasonality"]["monthly"]
    parts.append(
        '<section class="card"><h2>月度季节性（全历史日均收益 %）</h2>'
        + _svg_bars([f"{m['month']}月" for m in monthly], [m["mean_daily_pct"] for m in monthly])
        + _table(
            ["月份", "日均收益", "上涨日占比", "样本数", "最好单日", "最差单日"],
            [[f"{m['month']}月", _sign(m["mean_daily_pct"], 3), _num(m["win_rate_pct"], 1, "%"),
              m["samples"], _sign(m["best_day_pct"]), _sign(m["worst_day_pct"])] for m in monthly],
        )
        + "</section>"
    )

    # event study
    ev = data["seasonality"]["event_study"]
    base = ev["baseline"]
    parts.append(
        f'<section class="card"><h2>条件事件研究 · 未来 {ev["horizon_bars"]} 根 K 线收益</h2>'
        f'<div class="note">全样本基准：{base["samples"]} 次 · 胜率 {_num(base["win_rate_pct"], 1, "%")} · '
        f'平均 {_num(base["mean_pct"], 2, "%")} · 中位数 {_num(base["median_pct"], 2, "%")}</div>'
        + _table(
            ["条件", "样本数", "胜率", "平均收益", "中位数", "相对基准", "最好/最差"],
            [[_esc(e["label"]), e["samples"], _num(e["win_rate_pct"], 1, "%"), _sign(e["mean_pct"]),
              _sign(e["median_pct"]), _sign(e["edge_vs_baseline_pct"]),
              f'{_num(e["best_pct"], 1, "%")} / {_num(e["worst_pct"], 1, "%")}'] for e in ev["events"]],
        )
        + f'<div class="note" style="margin-top:10px">{_esc(ev["note"])}</div></section>'
    )

    # strategies
    comp_rows = []
    for item in data["strategy_comparison"]:
        m = item.get("metrics")
        if not m:
            comp_rows.append([_esc(item["strategy"]), "—", "—", "—", "—", _esc(item.get("error", ""))])
            continue
        comp_rows.append([
            _esc(item["strategy"]), _sign(m["total_return_pct"]), _num(m["sharpe"], 2),
            _sign(m["max_drawdown_pct"]), m["trades"], _num(m["win_rate_pct"], 1, "%"),
        ])
    parts.append(
        '<section class="card"><h2>策略横向回测（默认参数，同一区间）</h2>'
        + _table(["策略", "总收益", "夏普", "最大回撤", "交易次数", "胜率"], comp_rows)
    )
    best = data["best_strategy"]
    if best:
        m = best["metrics"]
        kpis = [
            ("总收益", _sign(m["total_return_pct"])), ("年化 CAGR", _sign(m["cagr_pct"])),
            ("最大回撤", _sign(m["max_drawdown_pct"])), ("夏普", _num(m["sharpe"], 2)),
            ("索提诺", _num(m["sortino"], 2)), ("卡玛", _num(m["calmar"], 2)),
            ("基准收益", _sign(m["benchmark_return_pct"])), ("超额收益", _sign(m["excess_return_pct"])),
        ]
        parts.append(
            f'<h3>样本内最优（按夏普）：{_esc(best["strategy"])} · 参数 <code>{_esc(best["params"])}</code></h3>'
            + '<div class="grid">'
            + "".join(f'<div class="kpi"><div class="label">{_esc(k)}</div><div class="value">{v}</div></div>' for k, v in kpis)
            + "</div>"
            + '<h3>策略净值与买入持有基准</h3>'
            + _svg_lines([("策略净值", data["equity_curve"], "#1d4ed8"), ("买入持有", data["benchmark_curve"], "#94a3b8")])
        )
    parts.append("</section>")

    # provenance
    parts.append(
        '<section class="card"><h2>可复现性记录</h2>'
        + _table(
            ["项目", "值"],
            [
                ["标的代码", _esc(data["symbol"])],
                ["历史时点", _esc(data["as_of"])],
                ["数据区间", f"{_esc(d['first_date'])} ~ {_esc(d['last_date'])}"],
                ["数据源", _esc(", ".join(d["sources"]))],
                ["输入数据指纹", f"<code>{_esc(d['fingerprint'])}</code>"],
                ["回测配置", f"<code>{_esc(data['backtest_config'])}</code>"],
            ],
        )
        + "<h3>方法论</h3><ul>" + "".join(f"<li>{_esc(x)}</li>" for x in data["methodology"]) + "</ul></section>"
    )
    parts.append(f"<footer><b>免责声明</b>：{_esc(data['disclaimer'])}</footer>")
    parts.append("</div></body></html>")
    return "".join(parts)


def render_markdown(data: dict[str, Any]) -> str:
    snap, d = data["snapshot"], data["data"]
    lines = [
        f"# ChronoStock 历史股票分析 · {data['symbol']}",
        "",
        f"- 历史时点（as_of）：{data['as_of']}",
        f"- 数据区间：{d['first_date']} ~ {d['last_date']}（{d['bars']} 根日线）",
        f"- 数据源：{', '.join(d['sources'])}（来源时点：{d['last_date']}）",
        f"- 输入数据指纹：`{d['fingerprint']}`",
        f"- 生成时间：{data['generated_at']}",
        "",
    ]
    if data.get("warning"):
        lines += [f"> ⚠️ {data['warning']}", ""]
    lines += [
        f"## 结论：{snap['signal']}（评分 {snap['score']}/100）",
        "",
        f"趋势状态：{snap['trend_state']}；评分方法：{snap['method']}。",
        "",
        "| 因子 | 得分 | 上限 | 说明 |",
        "|---|---:|---:|---|",
    ]
    lines += [f"| {f['label']} | {f['score']} | {f['max']} | {f['detail']} |" for f in snap["factors"]]
    lines += ["", "### 风险提示", ""] + [f"- {c}" for c in snap["caveats"]]

    lines += ["", "## 当前值与历史分位（数据源：本地 DuckDB 仓库，区间同上）", "",
              "| 指标 | 当前值 | 历史分位 | 样本数 |", "|---|---:|---:|---:|"]
    for key, item in snap["percentiles"].items():
        lines.append(f"| {key} | {_num(item['value'], 4)} | {_num(item['percentile'], 1, '%')} | {item['samples']} |")

    ev = data["seasonality"]["event_study"]
    lines += ["", f"## 条件事件研究（未来 {ev['horizon_bars']} 根 K 线）", "",
              f"全样本基准：{ev['baseline']['samples']} 次，胜率 {_num(ev['baseline']['win_rate_pct'], 1, '%')}，"
              f"平均 {_num(ev['baseline']['mean_pct'], 2, '%')}。", "",
              "| 条件 | 样本 | 胜率 | 平均收益 | 相对基准 |", "|---|---:|---:|---:|---:|"]
    for e in ev["events"]:
        lines.append(f"| {e['label']} | {e['samples']} | {_num(e['win_rate_pct'], 1, '%')} | "
                     f"{_num(e['mean_pct'], 2, '%')} | {_num(e['edge_vs_baseline_pct'], 2, '%')} |")

    lines += ["", "## 策略横向回测（默认参数，同一区间，含手续费与滑点）", "",
              "| 策略 | 总收益 | 夏普 | 最大回撤 | 交易次数 |", "|---|---:|---:|---:|---:|"]
    for item in data["strategy_comparison"]:
        m = item.get("metrics")
        if m:
            lines.append(f"| {item['strategy']} | {_num(m['total_return_pct'], 2, '%')} | {_num(m['sharpe'], 2)} | "
                         f"{_num(m['max_drawdown_pct'], 2, '%')} | {m['trades']} |")
    if data["best_strategy"]:
        b = data["best_strategy"]
        lines += ["", f"样本内最优（按夏普）：`{b['strategy']}`，参数 `{b['params']}`。",
                  "", f"- 回测配置：`{data['backtest_config']}`",
                  f"- 绩效：`{b['metrics']}`"]

    lines += ["", "## 方法论", ""] + [f"- {x}" for x in data["methodology"]]
    lines += ["", "---", "", f"**免责声明**：{data['disclaimer']}", ""]
    return "\n".join(lines)


def render_report(data: dict[str, Any], fmt: str = "html") -> str:
    if fmt == "md" or fmt == "markdown":
        return render_markdown(data)
    if fmt == "html":
        return render_html(data)
    raise ValueError(f"不支持的报告格式: {fmt}")
