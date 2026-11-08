# ChronoStock · 可复现的历史股票分析与策略回测系统

> 一句话：**不猜明天，先 replay 昨天。** ChronoStock 把「历史行情存档 → 任意历史时点的当时视角分析 → 确定性策略回测 → 参数扫描/走步验证 → 可归档报告」做成一条可复现、可离线运行的流水线。

与 [`ZhuLinsen/daily_stock_analysis`](https://github.com/ZhuLinsen/daily_stock_analysis) 功能定位相近（多市场历史行情、技术指标、分析报告、回测、Web 工作台、自动化），但实现路线完全不同：那个项目是「LLM 每日生成决策仪表盘并推送」，本项目是「本地历史数据仓库 + 纯代码规则策略 + 可复现的时间点回放」。**没有复制其任何代码、提示词或模板。**

## 与参考项目的差异

| 维度 | 参考项目 | ChronoStock |
|---|---|---|
| 核心产出 | LLM 生成的每日决策仪表盘 | 确定性、可复算的历史分析报告 + 资金曲线 |
| 可复现性 | 受模型/提示词/新闻检索影响，同输入不同输出 | 输入数据指纹 + 参数快照，同输入必然同输出 |
| 历史回放 | 以「今天」为视角 | `as_of` 任意历史日期，只用当天及之前数据（point-in-time） |
| 回测 | 校验文本建议方向的胜率 | 真实目标仓位回测：手续费/滑点/止损止盈/基准/回撤/夏普 |
| 策略 | 15 份自然语言 prompt | 8 个可执行代码策略 + 参数网格 + walk-forward 样本外验证 |
| 数据层 | SQLite + 内存 TTL 缓存，强依赖在线 API | DuckDB 嵌入式 SQL 引擎 + Parquet 列存持久化，可完全离线 |
| 分析深度 | 评分 + 买卖点位 | 历史分位、事件研究（条件胜率）、季节性、多因子归因 |
| 技术栈 | FastAPI + SQLAlchemy + pandas + LiteLLM + React/Vite | FastAPI + DuckDB + Polars + 零构建原生前端（ECharts） |
| 部署侧重 | GitHub Actions 定时推送到 6 种 IM | 本地 CLI / Web 工作台 / Docker / CI 生成归档报告 |

## 功能清单

- **数据仓库**：多源历史日线（Yahoo / Stooq / AkShare，均为可选依赖），统一代码与 OHLCV schema，每行记录来源与入库时间；增量同步带重叠窗口以对齐复权；Parquet 快照导入导出。
- **Point-in-time 分析**：`as_of=2020-03-23` 生成的分析只使用当天及之前的 K 线，杜绝未来函数；可选 `vintage=True` 进一步只保留当时已入库的数据版本。
- **指标引擎**：SMA/EMA(5~120)、MACD、RSI(Wilder)、布林带与 %B、ATR/真实波幅、KDJ、CCI、OBV、Donchian、历史波动率、Z-Score、ROC、52 周分位、回撤等 30+ 列，全部 Polars 表达式向量化。
- **策略回测**：均线交叉、MACD 趋势、RSI 反转、布林突破、唐奇安突破、双动量、Z-Score 均值回归、波动率目标择时；支持只做多/多空、手续费、滑点、止损止盈、冷却期、基准对比。
- **绩效度量**：总收益、CAGR、最大回撤、夏普、索提诺、卡玛、胜率、盈亏比、换手、暴露度、逐日净值/回撤/仓位曲线与成交流水。
- **参数研究**：网格搜索 + 目标函数排序 + 滚动 walk-forward 样本外验证 + 过拟合比（IS/OOS 夏普）。
- **历史统计**：月度季节性、条件事件研究（超卖/金叉/新高/放量/深回撤后 N 日收益分布）、当前指标的历史分位。
- **多因子评分**：趋势/动量/位置/波动/量能/回撤六维确定性打分（0-100）与信号分档、关键支撑阻力位。
- **报告**：自包含 HTML（内嵌 SVG 图表，无外部依赖）与 Markdown，含数据来源、时点、数据指纹、参数、免责声明。
- **接口**：Typer CLI、REST API、单页 Web 工作台（K 线/指标/净值/热力图/历史回放）、Docker、GitHub Actions CI。

## 快速开始

```bash
git clone https://github.com/JackQi748/chronostock.git && cd chronostock
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"          # 可选行情源: pip install -e ".[market,dev]"

# 1. 零配置离线演示（合成数据，明确标注，仅用于功能演示）
chronostock demo

# 2. 查看仓库
chronostock symbols

# 3. 任意历史时点分析
chronostock analyze AAPL --as-of 2023-06-30

# 4. 策略回测 / 参数优化 / 走步验证
chronostock backtest AAPL --strategy dual_momentum --json
chronostock optimize AAPL --strategy ma_cross --walk-forward

# 5. 生成自包含 HTML 报告并打开
chronostock report AAPL --as-of 2023-06-30 --open

# 6. 启动 Web 工作台  http://127.0.0.1:8000
chronostock serve
```

同步真实行情（需网络；A 股建议装 AkShare，美股/港股建议装 yfinance）：

```bash
chronostock sync AAPL,MSFT,SPY --start 2015-01-01
chronostock sync 600519.SH,000001.SZ --source akshare
chronostock sync 00700.HK --source yahoo
chronostock export ./snapshot.parquet && chronostock import ./snapshot.parquet

# 完全离线也能用真实数据：导入自己的 CSV（支持中英文表头）
chronostock import-csv ./my_history.csv --symbol 600519.SH
```

> 数据源可用性说明：AkShare（A 股前复权）无需密钥、当前可直接取到真实行情；Yahoo / yfinance 与 Stooq 在部分网络环境会被反爬或本地缓存权限拦截，此时会自动降级并明确报错，**不会**悄悄替换成模拟数据。离线场景请用 `import-csv` 或 Parquet 快照。

数据默认存放在 `~/.chronostock/market.parquet`（同步日志在同目录 `market.sync.parquet`），可用 `CHRONOSTOCK_DB` / `CHRONOSTOCK_HOME` 覆盖。DuckDB 作为进程内 SQL 引擎，每次写入会把内存表刷回 Parquet；因此同一时间请只运行一个 CLI 命令或一个 Web 进程，跨机器迁移直接用 `export` / `import`。

## 主要命令

| 命令 | 作用 |
|---|---|
| `demo` | 写入确定性合成行情（演示/测试用） |
| `sync` / `symbols` / `export` / `import` / `import-csv` | 数据同步、快照与本地 CSV 导入 |
| `analyze` | 某个历史时点的多因子评分 + 历史分位 + 事件研究 |
| `indicators` | 输出指标表（CSV/JSON） |
| `backtest` | 单策略回测，输出绩效与资金曲线 |
| `optimize` | 参数网格 / walk-forward |
| `seasonality` | 月度季节性与条件事件研究 |
| `report` | 生成 HTML / Markdown 研报 |
| `serve` | 启动 Web 工作台与 REST API |

REST API：`/api/health`、`/api/symbols`、`/api/sync`、`/api/bars`、`/api/indicators`、`/api/analyze`、`/api/backtest`、`/api/optimize`、`/api/seasonality`、`/api/report`，交互式文档在 `/docs`。

## 方法论与防未来函数

1. **信号只向后看**：所有指标用 rolling / ewm（backward-looking）表达式，突破通道使用 `shift(1)`，状态延续用 `fill_null(strategy="forward")`，任何一列都不读取未来 bar。
2. **次日开盘成交**：bar `i` 收盘产生的信号在 bar `i+1` 开盘执行（`np.roll(signal, 1)`），避免收盘价成交的同 bar 前视偏差。
3. **成本显式**：手续费与滑点按成交名义金额计，默认各 5bps / 2bps；止损优先于止盈、触发后 2 根 bar 冷却，避免同 bar 双重触发的乐观假设。
4. **基准对齐**：买入持有基准与策略净值使用同一段行情、同一初始资金。
5. **样本外检验**：walk-forward 只在训练窗选参，测试窗用全历史前缀重算信号后截取，参数不泄漏到样本外评分。
6. **数据诚实**：在线源失败不会悄悄改用假数据；只有显式 `source=synthetic` 或 `demo` 才会产生模拟行情，且报告/页面/CLI 均显著标注。真实源使用复权价格（Yahoo `auto_adjust`、AkShare `qfq`）。

## 项目结构

```
chronostock/
├── chronostock/
│   ├── data/         # 数据源适配、DuckDB 查询引擎 + Parquet 仓库、增量同步
│   ├── analytics/    # 指标、评分、历史分位、季节性/事件研究
│   ├── backtest/     # 引擎、策略库、绩效、网格与走步验证
│   ├── report/       # HTML/Markdown 报告构建
│   ├── api/ + web/   # REST API 与零构建单页工作台
│   └── cli.py
├── tests/            # 指标、防未来函数、仓库、引擎、优化、API、CLI
└── .github/workflows/ci.yml
```

运行测试：`pytest`。代码风格：`ruff check .`。

## 免责声明

> **免责声明**：以上内容基于公开数据和量化分析，仅供参考，不构成投资建议。市场有风险，投资需谨慎。任何投资决策应结合个人风险承受能力、资金状况和投资目标独立判断，必要时咨询持牌专业机构。过往表现不预示未来收益。

本项目输出的是基于历史规则的统计结果，**不是**买卖建议；历史回测表现不预示未来收益。数据源可能因上游接口变更、限流或复权口径调整而失效，请以交易所与公司公告等一手来源为准。

## 致谢与许可

功能对标参考了开源项目 `ZhuLinsen/daily_stock_analysis` 的产品形态；本仓库为独立实现，未复用其代码。行情数据版权归各数据提供方所有。

MIT License.
