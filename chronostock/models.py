"""Pydantic request/response contracts shared by the CLI, API and report layer."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field


class SyncRequest(BaseModel):
    symbols: list[str] = Field(default_factory=lambda: ["AAPL"])
    start: str = "2015-01-01"
    end: str | None = None
    source: Literal["auto", "yahoo", "stooq", "akshare", "synthetic"] = "auto"


class AnalyzeRequest(BaseModel):
    symbol: str
    as_of: str | None = None
    start: str | None = None


class BacktestRequest(BaseModel):
    symbol: str
    strategy: str = "dual_momentum"
    params: dict[str, Any] = Field(default_factory=dict)
    start: str | None = None
    end: str | None = None
    as_of: str | None = None
    initial_capital: float = 100_000.0
    commission_bps: float = 5.0
    slippage_bps: float = 2.0
    allow_short: bool = False
    stop_loss: float | None = None
    take_profit: float | None = None


class OptimizeRequest(BaseModel):
    symbol: str
    strategy: str = "ma_cross"
    grid: dict[str, list[Any]] = Field(default_factory=dict)
    objective: str = "sharpe"
    top_n: int = 8
    as_of: str | None = None
    walk_forward: bool = False
    train_bars: int = 500
    test_bars: int = 120


class BarPoint(BaseModel):
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    source: str


class HealthResponse(BaseModel):
    status: str
    version: str
    warehouse: str
    symbols: int
    bars: int
