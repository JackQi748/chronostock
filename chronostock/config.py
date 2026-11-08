"""Runtime configuration and shared constants.

All paths are resolved lazily from environment variables so the same code can be
used from the CLI, the web service and the test-suite without side effects.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent
WEB_DIR = PACKAGE_DIR / "web"

TRADING_DAYS = 252
DEFAULT_START = "2015-01-01"
DEFAULT_CAPITAL = 100_000.0

DISCLAIMER = (
    "以上内容基于公开数据和量化分析，仅供参考，不构成投资建议。市场有风险，投资需谨慎。"
    "任何投资决策应结合个人风险承受能力、资金状况和投资目标独立判断，必要时咨询持牌专业机构。"
    "过往表现不预示未来收益。"
)


def home_dir() -> Path:
    return Path(os.environ.get("CHRONOSTOCK_HOME", str(Path.home() / ".chronostock"))).expanduser()


def warehouse_path() -> Path:
    """Durable Parquet snapshot (DuckDB is the in-process query engine)."""
    return Path(os.environ.get("CHRONOSTOCK_DB", str(home_dir() / "market.parquet"))).expanduser()


def db_path() -> Path:
    """Backwards-compatible alias for :func:`warehouse_path`."""
    return warehouse_path()


def reports_dir() -> Path:
    return Path(os.environ.get("CHRONOSTOCK_REPORTS", str(home_dir() / "reports"))).expanduser()


@dataclass(frozen=True)
class Settings:
    trading_days: int = TRADING_DAYS
    default_start: str = DEFAULT_START
    initial_capital: float = DEFAULT_CAPITAL
    commission_bps: float = 5.0
    slippage_bps: float = 2.0
    allow_short: bool = False
    request_timeout: int = 20
    demo_symbols: tuple[str, ...] = ("AAPL", "MSFT", "SPY", "600519.SH", "000001.SZ", "0700.HK")
    synthetic_warning: str = field(
        default="演示数据为确定性合成的模拟行情，仅用于功能演示与测试，不代表任何真实证券的历史价格。"
    )


SETTINGS = Settings()
