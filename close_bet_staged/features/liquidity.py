"""Continuous liquidity and tradability features."""

from __future__ import annotations

import pandas as pd

from .common import safe_divide


def build_liquidity_features(daily: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    required = {
        "date", "ticker", "open", "high", "low", "close", "volume",
        "trading_value", "market_cap", "universe_type",
    }
    missing = required - set(daily.columns)
    if missing:
        raise ValueError(f"daily data missing columns: {sorted(missing)}")
    frame = daily.copy().sort_values(["ticker", "date"]).reset_index(drop=True)
    grouped = frame.groupby("ticker", sort=False, group_keys=False)
    prior_avg_value = grouped["trading_value"].transform(
        lambda values: values.shift(1).rolling(window, min_periods=window).mean()
    )
    prior_avg_volume = grouped["volume"].transform(
        lambda values: values.shift(1).rolling(window, min_periods=window).mean()
    )
    previous_close = grouped["close"].shift(1)
    frame["daily_history_count"] = grouped.cumcount()
    frame["avg_trading_value_20d_prior"] = prior_avg_value
    frame["trading_value_ratio_20d_prior"] = safe_divide(frame["trading_value"], prior_avg_value)
    frame["volume_ratio_20d_prior"] = safe_divide(frame["volume"], prior_avg_volume)
    frame["daily_return"] = safe_divide(frame["close"], previous_close) - 1.0
    frame["daily_range_pct"] = safe_divide(frame["high"] - frame["low"], previous_close)
    frame["close_price"] = frame["close"]
    keep = [
        "date", "ticker", "universe_type", "daily_history_count", "trading_value",
        "avg_trading_value_20d_prior", "trading_value_ratio_20d_prior",
        "volume_ratio_20d_prior", "close_price", "daily_return", "daily_range_pct", "market_cap",
    ]
    return frame[keep]
