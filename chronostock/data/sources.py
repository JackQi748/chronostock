"""Pluggable historical OHLCV sources.

Design goals (deliberately different from the reference project):

* one canonical symbol format, one canonical bar schema;
* every row carries its provenance (``source`` column), so a report can always
  state where its numbers came from;
* online sources are optional imports - the system is fully usable offline with
  a local snapshot or with explicitly labelled synthetic demo data;
* failures degrade transparently instead of silently switching price scales.

No source is allowed to invent data. ``SyntheticSource`` is the only generator
and is always tagged ``synthetic`` and surfaced with a warning.
"""

from __future__ import annotations

import csv
import io
import re
import urllib.request
import zlib
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

import numpy as np
import polars as pl

from chronostock.config import SETTINGS

BAR_COLUMNS = ["symbol", "date", "open", "high", "low", "close", "volume", "source"]


class DataSourceError(RuntimeError):
    """Raised when no configured source can serve the requested history."""


@dataclass(frozen=True)
class FetchResult:
    bars: pl.DataFrame
    source: str
    notes: list[str] = field(default_factory=list)
    coverage: tuple[date, date] | None = None

    @property
    def is_synthetic(self) -> bool:
        return self.source == "synthetic"


def normalize_symbol(raw: str) -> str:
    """Normalise user input into ``AAPL`` / ``600519.SH`` / ``00700.HK``."""
    s = str(raw).strip().upper().replace(" ", "")
    if not s:
        raise ValueError("股票代码不能为空")
    m = re.fullmatch(r"(SH|SZ)(\d{6})", s)
    if m:
        return f"{m.group(2)}.{m.group(1)}"
    m = re.fullmatch(r"HK(\d{1,5})", s)
    if m:
        return f"{m.group(1).zfill(5)}.HK"
    if s.endswith(".SS"):
        return s[:-3] + ".SH"
    if s.endswith(".US"):
        return s[:-3]
    if s.endswith(".HK"):
        code = s[:-3]
        return f"{code.zfill(5)}.HK"
    return s


def market_of(symbol: str) -> str:
    if symbol.endswith(".HK"):
        return "HK"
    if symbol.endswith((".SH", ".SZ")):
        return "CN"
    return "US"


def _to_yahoo(symbol: str) -> str:
    if symbol.endswith(".SH"):
        return symbol[:-3] + ".SS"
    return symbol


def _to_stooq(symbol: str) -> str:
    code, _, suffix = symbol.partition(".")
    suffix = suffix.lower() if suffix else "us"
    return f"{code.lower()}.{suffix}"


def _to_akshare(symbol: str) -> str:
    return symbol.split(".")[0]


def _as_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _frame(rows: list[dict], symbol: str, source: str) -> pl.DataFrame:
    """Build a canonical, sorted, validated OHLCV frame."""
    if not rows:
        raise DataSourceError("数据源返回空结果")
    keys = ["date", "open", "high", "low", "close", "volume"]
    data = [tuple(row[k] for k in keys) for row in rows]
    df = pl.DataFrame(
        data,
        schema={
            "date": pl.Date,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Float64,
        },
        orient="row",
    )
    df = df.filter(
        (pl.col("close") > 0)
        & (pl.col("high") >= pl.col("low"))
        & (pl.col("open") > 0)
        & (pl.col("high") >= pl.col("close"))
        & (pl.col("low") <= pl.col("close"))
    )
    df = df.unique(subset=["date"], keep="last").sort("date")
    if df.is_empty():
        raise DataSourceError("数据源返回的行情未通过完整性校验")
    return df.with_columns(
        pl.lit(symbol).alias("symbol"),
        pl.lit(source).alias("source"),
    ).select(BAR_COLUMNS)


class StooqSource:
    """Free daily CSV endpoint, no API key required (best effort for US/HK)."""

    name = "stooq"
    BASE = "https://stooq.com/q/d/l/"

    def fetch(self, symbol: str, start: date, end: date, timeout: int) -> pl.DataFrame:
        url = (
            f"{self.BASE}?s={_to_stooq(symbol)}"
            f"&d1={start:%Y%m%d}&d2={end:%Y%m%d}&i=d"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "ChronoStock/0.1"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8-sig", errors="replace")
        if "No data" in text or "Date" not in text[:200]:
            raise DataSourceError("Stooq 无该标的或无该区间数据")
        rows: list[dict] = []
        for row in csv.DictReader(io.StringIO(text)):
            try:
                rows.append(
                    {
                        "date": date.fromisoformat(row["Date"]),
                        "open": float(row["Open"]),
                        "high": float(row["High"]),
                        "low": float(row["Low"]),
                        "close": float(row["Close"]),
                        "volume": float(row.get("Volume") or 0),
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
        return _frame(rows, symbol, self.name)


class YahooSource:
    """yfinance adapter; optional dependency, auto-adjusted (splits/dividends)."""

    name = "yahoo"

    def fetch(self, symbol: str, start: date, end: date, timeout: int) -> pl.DataFrame:
        try:
            import yfinance as yf
        except ImportError as exc:  # pragma: no cover - depends on optional dep
            raise DataSourceError("未安装 yfinance，请执行 pip install 'chronostock[market]'") from exc
        raw = yf.Ticker(_to_yahoo(symbol)).history(
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            interval="1d",
            auto_adjust=True,
            actions=False,
            timeout=timeout,
        )
        if raw is None or raw.empty:
            raise DataSourceError("Yahoo 无该标的或无该区间数据")
        rows = []
        for idx, row in raw.iterrows():
            rows.append(
                {
                    "date": idx.date() if hasattr(idx, "date") else _as_date(str(idx)[:10]),
                    "open": float(row["Open"]),
                    "high": float(row["High"]),
                    "low": float(row["Low"]),
                    "close": float(row["Close"]),
                    "volume": float(row.get("Volume") or 0),
                }
            )
        return _frame(rows, symbol, self.name)


class AkshareSource:
    """A-share adapter with forward-adjusted (qfq) prices."""

    name = "akshare"

    def fetch(self, symbol: str, start: date, end: date, timeout: int) -> pl.DataFrame:
        try:
            import akshare as ak
        except ImportError as exc:  # pragma: no cover - depends on optional dep
            raise DataSourceError("未安装 akshare，请执行 pip install 'chronostock[market]'") from exc
        raw = ak.stock_zh_a_hist(
            symbol=_to_akshare(symbol),
            period="daily",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            adjust="qfq",
        )
        if raw is None or raw.empty:
            raise DataSourceError("AkShare 无该标的或无该区间数据")
        rows = []
        for row in raw.to_dict("records"):
            d = row["日期"]
            rows.append(
                {
                    "date": d.date() if hasattr(d, "date") else _as_date(str(d)[:10]),
                    "open": float(row["开盘"]),
                    "high": float(row["最高"]),
                    "low": float(row["最低"]),
                    "close": float(row["收盘"]),
                    "volume": float(row["成交量"] or 0),
                }
            )
        return _frame(rows, symbol, self.name)


class SyntheticSource:
    """Deterministic simulated OHLCV for demos/tests only.

    The output is *always* tagged ``synthetic``; reports and the UI show a
    prominent warning. It is never used as a silent fallback for a failed
    online fetch.
    """

    name = "synthetic"

    def __init__(self, seed: int = 20261109, start_price: float = 100.0):
        self.seed = seed
        self.start_price = start_price

    def fetch(self, symbol: str, start: date, end: date, timeout: int = 0) -> pl.DataFrame:
        dates = (
            pl.DataFrame({"date": pl.date_range(start, end, "1d", eager=True)})
            .filter(pl.col("date").dt.weekday() <= 5)
            .to_series()
            .to_list()
        )
        n = len(dates)
        if n < 30:
            raise DataSourceError("合成数据的区间至少需要 30 个自然日")
        rng = np.random.default_rng(self.seed + zlib.crc32(symbol.encode()))
        i = np.arange(n)
        # A slow regime cycle makes trends and drawdowns appear without any real data.
        drift = 0.00028 + 0.00075 * np.sin(i / 95.0) + 0.00035 * np.sin(i / 31.0)
        shock = rng.normal(0.0, 0.011, n)
        log_ret = drift + shock
        close = self.start_price * np.exp(np.cumsum(log_ret))
        prev = np.concatenate([[self.start_price], close[:-1]])
        open_ = prev * (1 + rng.normal(0.0, 0.0025, n))
        high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0.0, 0.0045, n)))
        low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0.0, 0.0045, n)))
        volume = np.clip(rng.lognormal(13.2, 0.45, n) * (1 + 2.0 * np.abs(log_ret) / 0.011), 1e3, None)
        rows = [
            {
                "date": dates[j],
                "open": float(open_[j]),
                "high": float(high[j]),
                "low": float(low[j]),
                "close": float(close[j]),
                "volume": float(volume[j]),
            }
            for j in range(n)
        ]
        return _frame(rows, symbol, self.name)


def load_csv_bars(path: str, symbol: str | None = None) -> pl.DataFrame:
    """Import real OHLCV history from a local CSV (offline, no network needed).

    Accepts English or Chinese headers (date/open/high/low/close/volume or
    日期/开盘/最高/最低/收盘/成交量). Volume is optional.
    """
    frame = pl.read_csv(path)
    aliases = {
        "date": "date", "日期": "date", "trade_date": "date", "time": "date",
        "open": "open", "开盘": "open", "开盘价": "open",
        "high": "high", "最高": "high", "最高价": "high",
        "low": "low", "最低": "low", "最低价": "low",
        "close": "close", "收盘": "close", "收盘价": "close",
        "volume": "volume", "成交量": "volume", "vol": "volume",
    }
    rename = {c: aliases[c.strip().lower()] for c in frame.columns if c.strip().lower() in aliases}
    frame = frame.rename(rename)
    required = {"date", "open", "high", "low", "close"}
    if not required <= set(frame.columns):
        raise DataSourceError(f"CSV 缺少必要列: {sorted(required - set(frame.columns))}")
    if frame["date"].dtype == pl.Utf8:
        frame = frame.with_columns(pl.col("date").str.strptime(pl.Date, "%Y-%m-%d", strict=False))
    elif frame["date"].dtype in (pl.Datetime, pl.Date):
        frame = frame.with_columns(pl.col("date").cast(pl.Date))
    else:
        raise DataSourceError("CSV 的 date 列无法解析为日期")
    if "volume" not in frame.columns:
        frame = frame.with_columns(pl.lit(0.0).alias("volume"))
    rows = [
        {
            "date": r["date"],
            "open": float(r["open"]), "high": float(r["high"]),
            "low": float(r["low"]), "close": float(r["close"]),
            "volume": float(r["volume"] or 0),
        }
        for r in frame.iter_rows(named=True)
        if r["date"] is not None
    ]
    sym = normalize_symbol(symbol or Path(path).stem)
    return _frame(rows, sym, "csv")


_SOURCES: dict[str, Callable[[], object]] = {
    "stooq": StooqSource,
    "yahoo": YahooSource,
    "akshare": AkshareSource,
    "synthetic": SyntheticSource,
}


def _candidates(symbol: str, source: str) -> list[str]:
    if source != "auto":
        return [source]
    if market_of(symbol) == "CN":
        return ["akshare", "yahoo", "stooq"]
    return ["yahoo", "stooq"]


def fetch_bars(
    symbol: str,
    start: str | date = SETTINGS.default_start,
    end: str | date | None = None,
    source: str = "auto",
    timeout: int = SETTINGS.request_timeout,
) -> FetchResult:
    """Fetch one symbol's daily history, trying sources in market-aware order."""
    sym = normalize_symbol(symbol)
    d0 = _as_date(start)
    d1 = _as_date(end) if end else date.today()
    if d1 <= d0:
        raise ValueError("结束日期必须晚于开始日期")
    if source not in (*_SOURCES, "auto"):
        raise ValueError(f"未知数据源: {source}")

    errors: list[str] = []
    for name in _candidates(sym, source):
        try:
            engine = _SOURCES[name]()
            bars = engine.fetch(sym, d0, d1, timeout)  # type: ignore[attr-defined]
            if bars.is_empty():
                raise DataSourceError("空结果")
            notes = [f"source={name}", f"requested={d0.isoformat()}..{d1.isoformat()}"]
            if name == "synthetic":
                notes.append(SETTINGS.synthetic_warning)
            return FetchResult(
                bars=bars,
                source=name,
                notes=notes,
                coverage=(bars["date"].min(), bars["date"].max()),
            )
        except Exception as exc:  # noqa: BLE001 - source isolation is intentional
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
    raise DataSourceError(
        "所有数据源均失败 -> " + " | ".join(errors) + "。可改用本地快照，或显式指定 source=synthetic 做离线演示。"
    )
