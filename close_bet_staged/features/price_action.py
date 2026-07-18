"""Leakage-safe Price Action features for setup types A, B, and C."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .common import safe_divide


def _confirmed_swing_series(
    lows: pd.Series,
    left_bars: int,
    confirmation_bars: int,
) -> tuple[pd.Series, pd.Series]:
    """Return the two latest swing lows known at each row, using no later signal data."""
    values = lows.to_numpy(dtype=float)
    recent = np.full(len(values), np.nan)
    previous = np.full(len(values), np.nan)
    confirmed: list[float] = []
    for signal_index in range(len(values)):
        pivot_index = signal_index - confirmation_bars
        if pivot_index >= left_bars:
            left = values[pivot_index - left_bars:pivot_index]
            right = values[pivot_index + 1:signal_index + 1]
            pivot = values[pivot_index]
            if len(right) == confirmation_bars and pivot < np.min(left) and pivot <= np.min(right):
                confirmed.append(pivot)
        if confirmed:
            recent[signal_index] = confirmed[-1]
        if len(confirmed) >= 2:
            previous[signal_index] = confirmed[-2]
    return pd.Series(recent, index=lows.index), pd.Series(previous, index=lows.index)


def build_price_action_features(
    daily: pd.DataFrame,
    breakout_windows: tuple[int, ...] = (5, 10, 20, 60),
    swing_left_bars: int = 2,
    swing_confirmation_bars: int = 2,
) -> pd.DataFrame:
    required = {"date", "ticker", "open", "high", "low", "close", "volume", "universe_type"}
    missing = required - set(daily.columns)
    if missing:
        raise ValueError(f"daily data missing columns: {sorted(missing)}")
    if not breakout_windows or any(window < 2 for window in breakout_windows):
        raise ValueError("breakout windows must be greater than one")
    frame = daily.copy().sort_values(["ticker", "date"]).reset_index(drop=True)
    candle_range = frame["high"] - frame["low"]
    frame["flat_candle"] = candle_range.eq(0)
    frame["clv"] = safe_divide(frame["close"] - frame["low"], candle_range)
    frame["upper_wick_ratio"] = safe_divide(
        frame["high"] - frame[["open", "close"]].max(axis=1), candle_range
    )
    frame["lower_wick_ratio"] = safe_divide(
        frame[["open", "close"]].min(axis=1) - frame["low"], candle_range
    )
    grouped = frame.groupby("ticker", sort=False, group_keys=False)
    for window in breakout_windows:
        prior_high = grouped["high"].transform(
            lambda values, w=window: values.shift(1).rolling(w, min_periods=w).max()
        )
        prior_low = grouped["low"].transform(
            lambda values, w=window: values.shift(1).rolling(w, min_periods=w).min()
        )
        frame[f"prior_high_{window}"] = prior_high
        frame[f"prior_low_{window}"] = prior_low
        frame[f"breakout_pct_{window}"] = safe_divide(frame["close"], prior_high) - 1.0
        frame[f"low_vs_prior_high_pct_{window}"] = safe_divide(frame["low"], prior_high) - 1.0
        frame[f"close_vs_prior_low_pct_{window}"] = safe_divide(frame["close"], prior_low) - 1.0
        frame[f"box_width_pct_{window}"] = safe_divide(prior_high - prior_low, prior_low)

    recent_parts = []
    previous_parts = []
    for _, indices in frame.groupby("ticker", sort=False).groups.items():
        lows = frame.loc[indices, "low"]
        recent, previous = _confirmed_swing_series(lows, swing_left_bars, swing_confirmation_bars)
        recent_parts.append(recent)
        previous_parts.append(previous)
    frame["recent_confirmed_swing_low"] = pd.concat(recent_parts).sort_index()
    frame["previous_confirmed_swing_low"] = pd.concat(previous_parts).sort_index()
    frame["higher_low_pct"] = (
        safe_divide(frame["recent_confirmed_swing_low"], frame["previous_confirmed_swing_low"]) - 1.0
    )
    frame["support_level"] = frame["recent_confirmed_swing_low"]
    frame["low_vs_support_pct"] = safe_divide(frame["low"], frame["support_level"]) - 1.0
    frame["close_vs_support_pct"] = safe_divide(frame["close"], frame["support_level"]) - 1.0
    base = [
        "date", "ticker", "universe_type", "flat_candle", "clv", "upper_wick_ratio",
        "lower_wick_ratio", "recent_confirmed_swing_low", "previous_confirmed_swing_low",
        "higher_low_pct", "support_level", "low_vs_support_pct", "close_vs_support_pct",
    ]
    for window in breakout_windows:
        base.extend([
            f"prior_high_{window}", f"prior_low_{window}", f"breakout_pct_{window}",
            f"low_vs_prior_high_pct_{window}", f"close_vs_prior_low_pct_{window}",
            f"box_width_pct_{window}",
        ])
    return frame[base]
