/* ChronoStock workbench - zero-build vanilla JS + ECharts (optional CDN). */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const state = { symbols: [], specs: [], loaded: new Set(), active: 'overview', strategySpecs: {} };
  const charts = {};

  const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const palette = () => ({
    ink: cssVar('--ink'), muted: cssVar('--muted'), line: cssVar('--line'),
    up: cssVar('--up'), down: cssVar('--down'), accent: cssVar('--accent'),
    panel2: cssVar('--panel2'),
  });

  async function api(path, opts) {
    const res = await fetch(path, Object.assign({ headers: { 'Content-Type': 'application/json' } }, opts || {}));
    if (!res.ok) {
      let msg = res.status + ' ' + res.statusText;
      try { const j = await res.json(); if (j.detail) msg = j.detail; } catch (_) { /* ignore */ }
      throw new Error(msg);
    }
    const ct = res.headers.get('content-type') || '';
    return ct.indexOf('json') >= 0 ? res.json() : res.text();
  }

  function q() {
    const sym = encodeURIComponent($('symbolSelect').value || '');
    const asOf = encodeURIComponent($('asOf').value || '');
    return 'symbol=' + sym + '&as_of=' + asOf;
  }

  function fmt(v, d, suffix) {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return '—';
    return Number(v).toFixed(d === undefined ? 2 : d) + (suffix || '');
  }
  function pct(v, d) {
    if (v === null || v === undefined) return '—';
    const n = Number(v);
    const cls = n > 0 ? 'class="pos"' : (n < 0 ? 'class="neg"' : '');
    return '<span ' + cls + '>' + (n > 0 ? '+' : '') + n.toFixed(d === undefined ? 2 : d) + '%</span>';
  }
  function banner(text, show) {
    const el = $('banner');
    el.textContent = text;
    el.classList.toggle('hidden', !show);
  }
  function table(headers, rows) {
    if (!rows.length) return '<p class="muted small">暂无数据</p>';
    const head = '<thead><tr>' + headers.map((h) => '<th>' + h + '</th>').join('') + '</tr></thead>';
    const body = '<tbody>' + rows.map((r) => '<tr>' + r.map((c) => '<td>' + c + '</td>').join('') + '</tr>').join('') + '</tbody>';
    return '<table>' + head + body + '</table>';
  }

  function chart(id, option) {
    const el = $(id);
    if (!el) return;
    if (window.__echartsFailed || !window.echarts) {
      el.innerHTML = '<div class="chart-fallback">图表库未加载（离线环境）；数据仍可通过 REST API 获取</div>';
      return;
    }
    if (charts[id]) { charts[id].dispose(); }
    const c = window.echarts.init(el);
    c.setOption(option);
    charts[id] = c;
  }

  function baseOpt(extra) {
    const p = palette();
    const dark = document.documentElement.dataset.theme === 'dark';
    const base = {
      backgroundColor: 'transparent',
      textStyle: { color: p.ink, fontFamily: 'sans-serif' },
      tooltip: {
        trigger: 'axis',
        backgroundColor: dark ? '#1b2536' : '#ffffff',
        borderColor: p.line,
        textStyle: { color: p.ink, fontSize: 12 },
      },
      grid: { left: 62, right: 22, top: 26, bottom: 34 },
      xAxis: {
        type: 'category', boundaryGap: true,
        axisLine: { lineStyle: { color: p.line } },
        axisLabel: { color: p.muted, fontSize: 10 },
        splitLine: { show: false },
      },
      yAxis: {
        type: 'value', scale: true,
        axisLine: { show: false }, axisLabel: { color: p.muted, fontSize: 10 },
        splitLine: { lineStyle: { color: p.line, type: 'dashed' } },
      },
    };
    return Object.assign(base, extra || {});
  }

  function gauge(score) {
    const p = palette();
    const r = 52, c = 2 * Math.PI * r;
    const fill = c * Math.max(0, Math.min(100, Number(score))) / 100;
    const color = score >= 70 ? p.up : (score >= 45 ? '#f59e0b' : p.down);
    $('scoreGauge').innerHTML =
      '<svg viewBox="0 0 140 140" width="132" height="132">' +
      '<circle cx="70" cy="70" r="' + r + '" fill="none" stroke="' + p.line + '" stroke-width="12"/>' +
      '<circle cx="70" cy="70" r="' + r + '" fill="none" stroke="' + color + '" stroke-width="12" stroke-linecap="round" ' +
      'stroke-dasharray="' + fill.toFixed(1) + ' ' + c.toFixed(1) + '" transform="rotate(-90 70 70)"/>' +
      '<text x="70" y="76" text-anchor="middle" font-size="28" font-weight="700" fill="' + p.ink + '">' + Number(score).toFixed(1) + '</text>' +
      '<text x="70" y="96" text-anchor="middle" font-size="11" fill="' + p.muted + '">/ 100</text></svg>';
  }

  /* ------------------------------------------------------------ overview */
  async function loadOverview() {
    const a = await api('/api/analyze?' + q() + '&horizon=20');
    const s = a.snapshot;
    gauge(s.score);
    $('scoreSignal').textContent = s.signal;
    $('scoreTrend').textContent = s.trend_state;
    $('scoreMeta').textContent = a.symbol + ' · as_of ' + a.as_of + ' · ' + s.bars_used + ' 根 K 线 · ' + a.source;
    $('factorBars').innerHTML = s.factors.map((f) =>
      '<div class="factor"><div class="name">' + f.label + '</div>' +
      '<div class="track"><div class="fill" style="width:' + Math.min(100, f.score / f.max * 100).toFixed(0) + '%"></div></div>' +
      '<div class="val">' + fmt(f.score, 1) + '/' + f.max + '</div></div>' +
      '<div class="muted small" style="margin:-6px 0 8px 88px">' + f.detail + '</div>'
    ).join('');
    $('caveats').innerHTML = s.caveats.length ? s.caveats.map((c) => '• ' + c).join('<br>') : '';

    const kpis = [
      ['收盘价', fmt(s.indicators.close)], ['RSI14', fmt(s.indicators.rsi14, 1)],
      ['20 日动量', pct(s.indicators.roc_20 * 100)], ['60 日动量', pct(s.indicators.roc_60 * 100)],
      ['年化波动率', pct(s.indicators.vol_20 * 100, 1)], ['距历史高点', pct(s.indicators.drawdown * 100)],
      ['52 周分位', pct(s.indicators.pct_52w * 100, 1)], ['量比', fmt(s.indicators.volume_ratio)],
    ];
    $('kpiGrid').innerHTML = kpis.map((k) => '<div class="kpi"><div class="k">' + k[0] + '</div><div class="v">' + k[1] + '</div></div>').join('');

    const levels = s.levels.support_candidates.concat(s.levels.resistance_candidates);
    $('levelsTable').innerHTML = table(['位置', '价格', '距现价'], levels.map((l) => [l.name, fmt(l.value), pct(l.distance_pct)]));
    const labelMap = { rsi14: 'RSI14', atr_pct: 'ATR/价格', vol_20: '年化波动率', pct_52w: '52周分位',
      drawdown: '距高点', volume_ratio: '量比', roc_20: '20日动量', bb_pctb: '布林%B' };
    $('pctTable').innerHTML = table(['指标', '当前值', '历史分位', '样本'], Object.keys(s.percentiles).map((k) => {
      const v = s.percentiles[k];
      return [labelMap[k] || k, fmt(v.value, 4), fmt(v.percentile, 1, '%'), v.samples];
    }));
    banner(a.synthetic ? '⚠️ 演示模式：当前为确定性合成行情，不对应任何真实证券，结论仅供功能演示。' : '', a.synthetic);
  }

  /* -------------------------------------------------------------- replay */
  async function loadReplay() {
    const rows = await api('/api/indicators?' + q() + '&limit=250');
    if (!rows.length) { $('replayStats').textContent = '无数据'; return; }
    const p = palette();
    const dates = rows.map((r) => r.date);
    const candles = rows.map((r) => [r.open, r.close, r.low, r.high]);
    const volumes = rows.map((r) => ({ value: r.volume, itemStyle: { color: r.close >= r.open ? p.up : p.down } }));
    $('replayStats').textContent = rows.length + ' 根 · ' + rows[0].date + ' ~ ' + rows[rows.length - 1].date +
      ' · 数据截断于所选 as_of，不含未来数据';
    chart('chartPrice', baseOpt({
      legend: { data: ['K线', 'MA20', 'MA60', '布林上轨', '布林下轨'], textStyle: { color: p.muted }, top: 0 },
      xAxis: { type: 'category', data: dates, axisLine: { lineStyle: { color: p.line } }, axisLabel: { color: p.muted, fontSize: 10 } },
      yAxis: { type: 'value', scale: true, axisLabel: { color: p.muted, fontSize: 10 }, splitLine: { lineStyle: { color: p.line, type: 'dashed' } } },
      dataZoom: [{ type: 'inside', start: 40, end: 100 }, { type: 'slider', height: 16, bottom: 4, borderColor: p.line }],
      series: [
        { name: 'K线', type: 'candlestick', data: candles,
          itemStyle: { color: p.up, color0: p.down, borderColor: p.up, borderColor0: p.down } },
        { name: 'MA20', type: 'line', data: rows.map((r) => r.sma20), smooth: true, showSymbol: false, lineStyle: { width: 1.4, color: '#f59e0b' } },
        { name: 'MA60', type: 'line', data: rows.map((r) => r.sma60), smooth: true, showSymbol: false, lineStyle: { width: 1.4, color: '#0ea5e9' } },
        { name: '布林上轨', type: 'line', data: rows.map((r) => r.bb_upper), showSymbol: false, lineStyle: { width: 1, type: 'dashed', color: p.muted } },
        { name: '布林下轨', type: 'line', data: rows.map((r) => r.bb_lower), showSymbol: false, lineStyle: { width: 1, type: 'dashed', color: p.muted } },
      ],
    }));
    chart('chartVolume', baseOpt({
      grid: { left: 62, right: 22, top: 12, bottom: 26 },
      xAxis: { type: 'category', data: dates, axisLine: { lineStyle: { color: p.line } }, axisLabel: { color: p.muted, fontSize: 10 } },
      tooltip: Object.assign(baseOpt().tooltip, { trigger: 'axis' }),
      series: [{ name: '成交量', type: 'bar', data: volumes }],
    }));
  }

  /* ------------------------------------------------------------ backtest */
  async function runBacktest() {
    let params = {};
    try { params = JSON.parse($('btParams').value || '{}'); } catch (e) { alert('参数 JSON 解析失败: ' + e.message); return; }
    $('btRun').disabled = true;
    $('btRun').textContent = '运行中…';
    try {
      const r = await api('/api/backtest', { method: 'POST', body: JSON.stringify({
        symbol: $('symbolSelect').value, strategy: $('btStrategy').value, params: params,
        as_of: $('asOf').value || null, allow_short: $('btShort').checked,
      }) });
      const m = r.metrics, p = palette();
      const items = [
        ['总收益', pct(m.total_return_pct)], ['年化 CAGR', pct(m.cagr_pct)],
        ['最大回撤', pct(m.max_drawdown_pct)], ['夏普', fmt(m.sharpe, 2)],
        ['索提诺', fmt(m.sortino, 2)], ['卡玛', fmt(m.calmar, 2)],
        ['胜率', fmt(m.win_rate_pct, 1, '%')], ['交易次数', m.trades],
        ['基准收益', pct(m.benchmark_return_pct)], ['超额收益', pct(m.excess_return_pct)],
        ['换手倍数', fmt(m.turnover_x, 2)], ['仓位暴露', fmt(m.exposure_pct, 1, '%')],
      ];
      $('btMetrics').innerHTML = items.map((k) => '<div class="kpi"><div class="k">' + k[0] + '</div><div class="v">' + k[1] + '</div></div>').join('');
      const dates = r.equity_curve.map((x) => x.date);
      chart('chartEquity', baseOpt({
        legend: { data: ['策略净值', '买入持有'], textStyle: { color: p.muted }, top: 0 },
        xAxis: { type: 'category', data: dates, axisLine: { lineStyle: { color: p.line } }, axisLabel: { color: p.muted, fontSize: 10 } },
        series: [
          { name: '策略净值', type: 'line', data: r.equity_curve.map((x) => x.equity), showSymbol: false, smooth: true,
            lineStyle: { width: 2, color: p.accent }, areaStyle: { color: 'rgba(110,168,254,.12)' } },
          { name: '买入持有', type: 'line', data: r.equity_curve.map((x) => x.benchmark), showSymbol: false,
            lineStyle: { width: 1.4, color: p.muted, type: 'dashed' } },
        ],
      }));
      chart('chartDrawdown', baseOpt({
        grid: { left: 62, right: 22, top: 12, bottom: 26 },
        xAxis: { type: 'category', data: dates, axisLine: { lineStyle: { color: p.line } }, axisLabel: { color: p.muted, fontSize: 10 } },
        yAxis: { type: 'value', max: 0, axisLabel: { color: p.muted, fontSize: 10, formatter: '{value}%' }, splitLine: { lineStyle: { color: p.line, type: 'dashed' } } },
        series: [{ name: '回撤', type: 'line', data: r.equity_curve.map((x) => x.drawdown), showSymbol: false,
          lineStyle: { width: 1, color: p.down }, areaStyle: { color: 'rgba(34,197,94,.18)' } }],
      }));
      $('tradesTable').innerHTML = table(['日期', '动作', '数量', '价格', '费用', '已实现盈亏'],
        r.trades.slice(-20).reverse().map((t) => [t.date, t.action, fmt(t.units, 2), fmt(t.price), fmt(t.fee),
          t.pnl === null ? '—' : pct((t.pnl / 1000), 2)]));
    } catch (e) {
      banner('回测失败：' + e.message, true);
    } finally {
      $('btRun').disabled = false;
      $('btRun').textContent = '运行回测';
    }
  }

  /* ------------------------------------------------------------ optimize */
  async function runOptimize() {
    let grid = {};
    try { grid = JSON.parse($('optGrid').value || '{}'); } catch (e) { alert('网格 JSON 解析失败: ' + e.message); return; }
    $('optRun').disabled = true;
    $('optRun').textContent = '搜索中…';
    try {
      const r = await api('/api/optimize', { method: 'POST', body: JSON.stringify({
        symbol: $('symbolSelect').value, strategy: $('optStrategy').value, grid: grid,
        objective: $('optObjective').value, walk_forward: $('optWalk').checked, top_n: 12,
      }) });
      const p = palette();
      if (r.folds) {
        $('optSummary').textContent = 'fold 数 ' + r.fold_count + ' · 样本外夏普 ' + fmt(r.oos_metrics.sharpe, 2) +
          ' · 样本外总收益 ' + fmt(r.oos_metrics.total_return_pct, 2) + '% · 过拟合比 ' + fmt(r.overfit_ratio, 2);
        $('optTable').innerHTML = table(['折', '训练区间', '测试区间', '最优参数', '样本内得分', '样本外收益'],
          r.folds.map((f) => [f.fold, f.train.join('~'), f.test.join('~'), JSON.stringify(f.best_params),
            fmt(f.in_sample_score, 3), pct(f.oos_metrics.total_return_pct)]));
        const dates = r.oos_equity_curve.map((x) => x.date);
        chart('chartWF', baseOpt({
          xAxis: { type: 'category', data: dates, axisLine: { lineStyle: { color: p.line } }, axisLabel: { color: p.muted, fontSize: 10 } },
          series: [{ name: '样本外净值', type: 'line', data: r.oos_equity_curve.map((x) => x.equity), showSymbol: false,
            lineStyle: { width: 2, color: p.accent }, areaStyle: { color: 'rgba(110,168,254,.12)' } }],
        }));
        $('chartHeatmap').innerHTML = '';
      } else {
        $('optSummary').textContent = '已评估 ' + r.combos_evaluated + ' 组参数 · 目标 ' + r.objective +
          (r.best ? ' · 最优 ' + JSON.stringify(r.best.params) + ' 得分 ' + fmt(r.best.score, 3) : '');
        $('optTable').innerHTML = table(['#', '参数', '得分', '总收益', '夏普', '最大回撤', '交易数'],
          r.results.map((x, i) => [i + 1, JSON.stringify(x.params), fmt(x.score, 3),
            pct(x.metrics.total_return_pct), fmt(x.metrics.sharpe, 2), pct(x.metrics.max_drawdown_pct), x.metrics.trades]));
        renderHeatmap(r.results, r.objective);
        $('chartWF').innerHTML = '';
      }
    } catch (e) {
      banner('参数搜索失败：' + e.message, true);
    } finally {
      $('optRun').disabled = false;
      $('optRun').textContent = '开始搜索';
    }
  }

  function renderHeatmap(results, objective) {
    const el = $('chartHeatmap');
    if (!results.length || !window.echarts || window.__echartsFailed) { el.innerHTML = ''; return; }
    const keys = Object.keys(results[0].params || {}).filter((k) =>
      new Set(results.map((r) => String(r.params[k]))).size > 1);
    if (keys.length < 2) { el.innerHTML = '<p class="muted small">单参数搜索无需热力图。</p>'; return; }
    const [kx, ky] = keys.slice(0, 2);
    const xs = Array.from(new Set(results.map((r) => String(r.params[kx]))));
    const ys = Array.from(new Set(results.map((r) => String(r.params[ky]))));
    const data = results.map((r) => [xs.indexOf(String(r.params[kx])), ys.indexOf(String(r.params[ky])), r.score]);
    const p = palette();
    chart('chartHeatmap', {
      backgroundColor: 'transparent',
      textStyle: { color: p.ink },
      tooltip: { position: 'top', formatter: (o) => kx + '=' + xs[o.data[0]] + ', ' + ky + '=' + ys[o.data[1]] + '<br>' + objective + ': ' + o.data[2] },
      grid: { left: 80, right: 80, top: 20, bottom: 46 },
      xAxis: { type: 'category', data: xs, name: kx, axisLabel: { color: p.muted }, axisLine: { lineStyle: { color: p.line } } },
      yAxis: { type: 'category', data: ys, name: ky, axisLabel: { color: p.muted }, axisLine: { lineStyle: { color: p.line } } },
      visualMap: { min: Math.min.apply(null, data.map((d) => d[2])), max: Math.max.apply(null, data.map((d) => d[2])),
        calculable: true, orient: 'vertical', right: 4, top: 'middle', textStyle: { color: p.muted },
        inRange: { color: ['#1d4ed8', '#60a5fa', '#f59e0b', '#ef4444'] } },
      series: [{ type: 'heatmap', data: data, label: { show: true, color: '#fff', fontSize: 10 },
        itemStyle: { borderColor: p.line, borderWidth: 1 } }],
    });
  }

  /* --------------------------------------------------------------- stats */
  async function loadStats() {
    const r = await api('/api/seasonality?' + q() + '&horizon=20');
    const p = palette();
    const m = r.monthly || [];
    $('seasonalityTable').innerHTML = table(['月份', '日均收益', '上涨日占比', '样本', '最好单日', '最差单日'],
      m.map((x) => [x.month + '月', pct(x.mean_daily_pct, 3), fmt(x.win_rate_pct, 1, '%'), x.samples,
        pct(x.best_day_pct), pct(x.worst_day_pct)]));
    chart('chartMonthly', baseOpt({
      grid: { left: 62, right: 22, top: 16, bottom: 30 },
      xAxis: { type: 'category', data: m.map((x) => x.month + '月'), axisLine: { lineStyle: { color: p.line } }, axisLabel: { color: p.muted, fontSize: 10 } },
      series: [{ type: 'bar', data: m.map((x) => ({ value: x.mean_daily_pct,
        itemStyle: { color: x.mean_daily_pct >= 0 ? p.up : p.down } })) }],
    }));
    const ev = r.event_study, b = ev.baseline;
    $('eventBaseline').textContent = '未来 ' + ev.horizon_bars + ' 根 K 线 · 全样本 ' + b.samples + ' 次 · 胜率 ' +
      fmt(b.win_rate_pct, 1, '%') + ' · 平均收益 ' + fmt(b.mean_pct, 2, '%') + ' · ' + ev.note;
    $('eventTable').innerHTML = table(['条件', '样本', '胜率', '平均收益', '中位数', '相对基准'],
      ev.events.map((e) => [e.label, e.samples, fmt(e.win_rate_pct, 1, '%'), pct(e.mean_pct),
        pct(e.median_pct), pct(e.edge_vs_baseline_pct)]));
  }

  /* --------------------------------------------------------------- report */
  async function syncData() {
    const symbols = $('syncSymbols').value.split(',').map((s) => s.trim()).filter(Boolean);
    $('syncResult').textContent = '同步中…';
    try {
      const r = await api('/api/sync', { method: 'POST', body: JSON.stringify({
        symbols: symbols.length ? symbols : [$('symbolSelect').value],
        source: $('syncSource').value, start: '2015-01-01',
      }) });
      $('syncResult').textContent = JSON.stringify(r, null, 2);
      const s = await api('/api/symbols');
      state.symbols = s.symbols;
      fillSymbols();
    } catch (e) { $('syncResult').textContent = '失败：' + e.message; }
  }

  /* ------------------------------------------------------------ navigation */
  const loaders = { overview: loadOverview, replay: loadReplay, stats: loadStats };
  function activate(tab, force) {
    state.active = tab;
    document.querySelectorAll('.tab').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
    document.querySelectorAll('.panel').forEach((p) => p.classList.toggle('active', p.id === 'tab-' + tab));
    if (loaders[tab] && (force || !state.loaded.has(tab))) {
      loaders[tab]().then(() => state.loaded.add(tab)).catch((e) => banner(tab + ' 加载失败：' + e.message, true));
    }
    setTimeout(() => Object.values(charts).forEach((c) => c && c.resize()), 60);
  }

  function fillSymbols() {
    const sel = $('symbolSelect');
    const current = sel.value;
    sel.innerHTML = state.symbols.map((s) => '<option value="' + s.symbol + '">' + s.symbol +
      ' (' + s.first_date + '~' + s.last_date + ', ' + s.source + ')</option>').join('');
    if (state.symbols.some((s) => s.symbol === current)) sel.value = current;
    if (state.symbols.length && !$('asOf').value) $('asOf').value = state.symbols[0].last_date;
    if (state.symbols.length) {
      const s = state.symbols.find((x) => x.symbol === sel.value) || state.symbols[0];
      banner(s.source === 'synthetic' ? '⚠️ 演示模式：当前为确定性合成行情，不对应任何真实证券，结论仅供功能演示。' : '', s.source === 'synthetic');
    }
  }

  function fillStrategies() {
    const opts = state.specs.map((s) => '<option value="' + s.name + '">' + s.label + ' · ' + s.name + '</option>').join('');
    $('btStrategy').innerHTML = opts;
    $('optStrategy').innerHTML = opts;
    const apply = () => {
      const spec = state.specs.find((s) => s.name === $('btStrategy').value);
      if (spec) { $('btParams').value = JSON.stringify(spec.defaults); state.strategySpecs[spec.name] = spec; }
    };
    $('btStrategy').addEventListener('change', apply);
    apply();
  }

  function bind() {
    document.querySelectorAll('.tab').forEach((b) => b.addEventListener('click', () => activate(b.dataset.tab)));
    $('refreshBtn').addEventListener('click', () => { state.loaded.clear(); activate(state.active, true); });
    $('symbolSelect').addEventListener('change', () => { state.loaded.clear(); fillSymbols(); activate(state.active, true); });
    $('asOf').addEventListener('change', () => { state.loaded.clear(); activate(state.active, true); });
    $('themeBtn').addEventListener('click', () => {
      const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
      document.documentElement.dataset.theme = next;
      state.loaded.clear();
      activate(state.active, true);
    });
    $('btRun').addEventListener('click', runBacktest);
    $('optRun').addEventListener('click', runOptimize);
    $('btnSync').addEventListener('click', syncData);
    $('btnHtml').addEventListener('click', () => window.open('/api/report?' + q() + '&format=html', '_blank'));
    $('btnMd').addEventListener('click', () => window.open('/api/report?' + q() + '&format=md', '_blank'));
    $('reportInfo').textContent = '报告页会在新标签打开；也可使用 CLI：chronostock report ' +
      ($('symbolSelect').value || 'SYMBOL') + ' --as-of ' + ($('asOf').value || 'YYYY-MM-DD');
  }

  async function init() {
    bind();
    try {
      const [h, s, st] = await Promise.all([api('/api/health'), api('/api/symbols'), api('/api/strategies')]);
      state.symbols = s.symbols;
      state.specs = st.strategies;
      fillStrategies();
      fillSymbols();
      $('reportInfo').textContent = '仓库：' + h.warehouse + ' · ' + h.bars + ' 根 K 线 / ' + h.symbols + ' 个标的';
      if (!state.symbols.length) {
        banner('仓库为空。请先运行 `chronostock demo` 写入演示数据，或在本页「报告与数据」标签同步在线行情。', true);
        return;
      }
      activate('overview', true);
    } catch (e) {
      banner('无法连接 API：' + e.message + '。请确认服务已启动（chronostock serve）。', true);
    }
  }

  init();
})();
