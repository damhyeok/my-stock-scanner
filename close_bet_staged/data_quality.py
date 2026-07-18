"""Fail-fast validation for raw daily bars."""

from __future__ import annotations

import pandas as pd

from .schema import DAILY_COLUMNS, DAILY_KEY


def validate_daily_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Return a sorted copy after enforcing raw-data invariants."""
    missing = set(DAILY_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"daily data missing columns: {sorted(missing)}")
    result = frame.copy()
    result["date"] = pd.to_datetime(result["date"]).dt.normalize()
    result["ticker"] = result["ticker"].astype("string").str.zfill(6)
    if result.duplicated(list(DAILY_KEY)).any():
        raise ValueError("daily data contains duplicate (date, ticker, universe_type) rows")
    numeric = ["open", "high", "low", "close", "volume", "trading_value", "market_cap"]
    if result[numeric].isna().any().any():
        raise ValueError("daily data contains missing required numeric values")
    if (result[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("daily OHLC prices must be positive")
    if (result[["volume", "trading_value", "market_cap"]] < 0).any().any():
        raise ValueError("volume, trading_value, and market_cap must be non-negative")
    invalid_ohlc = (
        (result["low"] > result["high"])
        | (result["open"] < result["low"])
        | (result["open"] > result["high"])
        | (result["close"] < result["low"])
        | (result["close"] > result["high"])
    )
    if invalid_ohlc.any():
        raise ValueError(f"daily data contains {int(invalid_ohlc.sum())} invalid OHLC rows")
    return result.sort_values(["ticker", "date"]).reset_index(drop=True)
