"""Leakage-safe features extractable from the currently available daily and sampled bars."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .common import safe_divide


def _anchored_daily_vwap(group: pd.DataFrame, confirmation_bars: int = 2) -> pd.DataFrame:
    """Calculate point-in-time daily AVWAP proxies from confirmed lows and volume concentration."""
    group = group.sort_values("date").copy()
    size = len(group)
    low = group["low"].to_numpy(dtype=float)
    volume = group["volume"].to_numpy(dtype=float)
    tpv = (
        (group["high"].to_numpy(dtype=float)
         + group["low"].to_numpy(dtype=float)
         + group["close"].to_numpy(dtype=float))
        / 3.0
        * volume
    )
    cumulative_tpv = np.cumsum(tpv)
    cumulative_volume = np.cumsum(volume)
    volume_ratio = group["volume_ratio_20d_prior"].to_numpy(dtype=float)
    swing_value = np.full(size, np.nan)
    volume_value = np.full(size, np.nan)
    volume_anchor_ratio = np.full(size, np.nan)
    latest_swing_anchor: int | None = None

    def anchored_value(anchor: int, end: int) -> float:
        before_tpv = cumulative_tpv[anchor - 1] if anchor else 0.0
        before_volume = cumulative_volume[anchor - 1] if anchor else 0.0
        denominator = cumulative_volume[end] - before_volume
        return (cumulative_tpv[end] - before_tpv) / denominator if denominator > 0 else np.nan

    for end in range(size):
        pivot = end - confirmation_bars
        if pivot >= 2:
            left = low[pivot - 2:pivot]
            right = low[pivot + 1:end + 1]
            if len(right) == confirmation_bars and low[pivot] < left.min() and low[pivot] <= right.min():
                latest_swing_anchor = pivot
        if latest_swing_anchor is not None:
            swing_value[end] = anchored_value(latest_swing_anchor, end)

        start = max(0, end - 19)
        candidates = np.arange(start, end + 1)
        valid = candidates[np.isfinite(volume_ratio[candidates])]
        if len(valid):
            anchor = int(valid[np.argmax(volume_ratio[valid])])
            volume_value[end] = anchored_value(anchor, end)
            volume_anchor_ratio[end] = volume_ratio[anchor]

    group["swing_avwap_daily_approx"] = swing_value
    group["volume_anchor_avwap_daily_approx"] = volume_value
    group["volume_anchor_ratio"] = volume_anchor_ratio
    group["close_vs_swing_avwap_pct"] = safe_divide(
        group["close"], group["swing_avwap_daily_approx"]
    ) - 1.0
    group["close_vs_volume_anchor_avwap_pct"] = safe_divide(
        group["close"], group["volume_anchor_avwap_daily_approx"]
    ) - 1.0
    return group


def build_daily_extended_features(daily: pd.DataFrame) -> pd.DataFrame:
    required = {
        "date", "ticker", "open", "high", "low", "close", "volume", "universe_type"
    }
    missing = required - set(daily.columns)
    if missing:
        raise ValueError(f"daily data missing columns: {sorted(missing)}")
    frame = daily.copy().sort_values(["ticker", "date"]).reset_index(drop=True)
    grouped = frame.groupby("ticker", sort=False, group_keys=False)
    previous_close = grouped["close"].shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    frame["true_range"] = true_range
    frame["stock_return_5d"] = safe_divide(frame["close"], grouped["close"].shift(5)) - 1.0
    frame["stock_return_20d"] = safe_divide(frame["close"], grouped["close"].shift(20)) - 1.0
    frame["atr14"] = grouped["true_range"].transform(
        lambda values: values.rolling(14, min_periods=14).mean()
    )
    frame["atr14_pct"] = safe_divide(frame["atr14"], frame["close"])
    range_pct = safe_divide(frame["high"] - frame["low"], previous_close)
    frame["range_pct"] = range_pct
    frame["avg_range_pct_5d"] = range_pct.groupby(frame["ticker"], sort=False).transform(
        lambda values: values.rolling(5, min_periods=5).mean()
    )
    frame["avg_range_pct_20d"] = range_pct.groupby(frame["ticker"], sort=False).transform(
        lambda values: values.rolling(20, min_periods=20).mean()
    )
    prior_atr = frame.groupby("ticker", sort=False)["atr14_pct"].shift(5)
    frame["atr_expansion_5d"] = safe_divide(frame["atr14_pct"], prior_atr) - 1.0
    frame["stop_to_atr_ratio"] = 0.02 / frame["atr14_pct"].replace(0, np.nan)

    prior_avg_volume = grouped["volume"].transform(
        lambda values: values.shift(1).rolling(20, min_periods=20).mean()
    )
    frame["volume_ratio_20d_prior"] = safe_divide(frame["volume"], prior_avg_volume)
    frame = pd.concat(
        [_anchored_daily_vwap(group) for _, group in frame.groupby("ticker", sort=False)],
        ignore_index=True,
    )
    keep = [
        "date", "ticker", "universe_type", "stock_return_5d", "stock_return_20d",
        "atr14_pct", "avg_range_pct_5d", "avg_range_pct_20d", "atr_expansion_5d",
        "stop_to_atr_ratio", "volume_anchor_ratio", "close_vs_swing_avwap_pct",
        "close_vs_volume_anchor_avwap_pct",
    ]
    return frame[keep]


def build_sampled_afternoon_features(minute: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    required = {"timestamp", "ticker", "open", "high", "low", "close", "volume"}
    missing = required - set(minute.columns)
    if missing:
        raise ValueError(f"minute data missing columns: {sorted(missing)}")
    bars = minute.copy()
    bars["timestamp"] = pd.to_datetime(bars["timestamp"])
    bars["date"] = bars["timestamp"].dt.normalize()
    bars["hhmm"] = bars["timestamp"].dt.strftime("%H:%M")
    bars = bars[bars["hhmm"].between("14:30", "15:30")].copy()
    totals = daily[["date", "ticker", "volume", "close"]].copy().rename(
        columns={"volume": "daily_volume", "close": "daily_close"}
    )
    totals["date"] = pd.to_datetime(totals["date"]).dt.normalize()
    keys = ["date", "ticker"]
    bars = bars.sort_values(keys + ["timestamp"])
    grouped = bars.groupby(keys, sort=False)
    bars["running_low"] = grouped["low"].cummin()
    bars["new_sampled_low"] = grouped["running_low"].diff().lt(0).astype(int)
    bars["up_volume"] = bars["volume"].where(bars["close"].ge(bars["open"]), 0)
    bars["down_volume"] = bars["volume"].where(bars["close"].lt(bars["open"]), 0)
    aggregate = grouped.agg(
        sampled_low=("low", "min"),
        sampled_high=("high", "max"),
        sampled_new_low_count=("new_sampled_low", "sum"),
        sampled_afternoon_volume=("volume", "sum"),
        up_volume=("up_volume", "sum"),
        down_volume=("down_volume", "sum"),
        sampled_bar_count=("timestamp", "size"),
    ).reset_index()
    start = (
        bars[bars["hhmm"].eq("14:30")]
        .drop_duplicates(keys, keep="last")[keys + ["open"]]
        .rename(columns={"open": "afternoon_reference"})
    )
    end = bars.drop_duplicates(keys, keep="last")[keys + ["hhmm"]].rename(
        columns={"hhmm": "afternoon_last_observation"}
    )
    end = end[end["afternoon_last_observation"].ge("15:10")]
    end["afternoon_end_quality"] = np.where(
        end["afternoon_last_observation"].eq("15:30"), "exact_1530", "preclose_proxy"
    )
    poc_indices = grouped["volume"].idxmax()
    poc = bars.loc[poc_indices, keys + ["close"]].rename(columns={"close": "sampled_poc"})
    result = aggregate.merge(start, on=keys, how="inner", validate="one_to_one")
    result = result.merge(end, on=keys, how="inner", validate="one_to_one")
    result = result.merge(poc, on=keys, how="left", validate="one_to_one")
    result = result.merge(totals, on=["date", "ticker"], how="left", validate="one_to_one")
    result["return_after_1430"] = safe_divide(
        result["daily_close"], result["afternoon_reference"]
    ) - 1.0
    result["sampled_up_down_volume_ratio"] = safe_divide(
        result["up_volume"], result["down_volume"]
    )
    result.loc[
        result["down_volume"].eq(0) & result["up_volume"].gt(0),
        "sampled_up_down_volume_ratio",
    ] = np.inf
    effective_high = pd.concat([result["sampled_high"], result["daily_close"]], axis=1).max(axis=1)
    effective_low = pd.concat([result["sampled_low"], result["daily_close"]], axis=1).min(axis=1)
    sampled_range = effective_high - effective_low
    result["sampled_afternoon_clv"] = safe_divide(
        result["daily_close"] - effective_low, sampled_range
    )
    result["close_vs_sampled_high_pct"] = safe_divide(
        result["daily_close"], effective_high
    ) - 1.0
    result["close_vs_sampled_poc_pct"] = safe_divide(
        result["daily_close"], result["sampled_poc"]
    ) - 1.0
    result["sampled_afternoon_volume_ratio"] = safe_divide(
        result["sampled_afternoon_volume"], result["daily_volume"]
    )
    return result.drop(columns=[
        "sampled_afternoon_volume", "daily_volume", "up_volume", "down_volume",
        "afternoon_reference", "daily_close", "sampled_low", "sampled_high", "sampled_poc",
    ])
