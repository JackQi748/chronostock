"""Historical market data: pluggable sources, DuckDB warehouse, incremental sync."""

from .sources import DataSourceError, FetchResult, fetch_bars, load_csv_bars, normalize_symbol
from .store import MarketStore, empty_bars

__all__ = [
    "DataSourceError",
    "FetchResult",
    "fetch_bars",
    "load_csv_bars",
    "normalize_symbol",
    "MarketStore",
    "empty_bars",
]
