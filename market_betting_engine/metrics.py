"""Dependency-free price/activity calculations used by both decision engines."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence, Tuple

from .contracts import CalculationMode, MetricResult


@dataclass(frozen=True)
class PriceBar:
    high: float
    low: float
    close: float
    volume: float


def close_location_value(high: float, low: float, close: float) -> MetricResult:
    """Return CLV in [-1, 1]; a flat bar is unavailable, never neutral zero."""

    if high < low or close < low or close > high:
        return MetricResult("clv", None, "ratio", CalculationMode.DERIVED, False, ("INVALID_OHLC",))
    width = high - low
    if width == 0:
        return MetricResult("clv", None, "ratio", CalculationMode.DERIVED, False, ("FLAT_BAR",))
    value = ((close - low) - (high - close)) / width
    return MetricResult("clv", value, "ratio", CalculationMode.DERIVED, True)


def volume_weighted_average_price(
    prices: Sequence[float], volumes: Sequence[float]
) -> MetricResult:
    if len(prices) != len(volumes) or not prices:
        return MetricResult("vwap", None, "price", CalculationMode.DERIVED, False, ("INVALID_INPUT",))
    if any(volume < 0 for volume in volumes):
        return MetricResult("vwap", None, "price", CalculationMode.DERIVED, False, ("NEGATIVE_VOLUME",))
    total_volume = sum(volumes)
    if total_volume == 0:
        return MetricResult("vwap", None, "price", CalculationMode.DERIVED, False, ("ZERO_TOTAL_VOLUME",))
    value = sum(price * volume for price, volume in zip(prices, volumes)) / total_volume
    return MetricResult(
        "vwap",
        value,
        "price",
        CalculationMode.DERIVED,
        True,
        components={"total_volume": total_volume},
    )


def clv_weighted_turnover_proxy(bars: Iterable[PriceBar]) -> MetricResult:
    """Estimate directional turnover; this is explicitly not actual net flow."""

    proxy_amount = 0.0
    gross_turnover = 0.0
    usable_turnover = 0.0
    flat_bar_count = 0
    count = 0
    for bar in bars:
        count += 1
        if bar.volume < 0 or bar.high < bar.low or not (bar.low <= bar.close <= bar.high):
            return MetricResult(
                "clv_weighted_turnover_proxy",
                None,
                "currency_proxy",
                CalculationMode.PROXY,
                False,
                ("INVALID_BAR",),
            )
        turnover = bar.close * bar.volume
        gross_turnover += turnover
        clv = close_location_value(bar.high, bar.low, bar.close)
        if not clv.available:
            flat_bar_count += 1
            continue
        proxy_amount += turnover * float(clv.value)
        usable_turnover += turnover

    if count == 0 or usable_turnover == 0:
        return MetricResult(
            "clv_weighted_turnover_proxy",
            None,
            "currency_proxy",
            CalculationMode.PROXY,
            False,
            ("NO_USABLE_TURNOVER",),
            {"gross_turnover": gross_turnover, "flat_bar_count": float(flat_bar_count)},
        )
    return MetricResult(
        "clv_weighted_turnover_proxy",
        proxy_amount,
        "currency_proxy",
        CalculationMode.PROXY,
        True,
        ("FLAT_BARS_EXCLUDED",) if flat_bar_count else (),
        {
            "gross_turnover": gross_turnover,
            "usable_turnover": usable_turnover,
            "proxy_ratio": proxy_amount / usable_turnover,
            "flat_bar_count": float(flat_bar_count),
        },
    )


def relative_return(asset_return: float, benchmark_return: float) -> MetricResult:
    return MetricResult(
        "relative_return",
        asset_return - benchmark_return,
        "return",
        CalculationMode.DERIVED,
        True,
        components={"asset_return": asset_return, "benchmark_return": benchmark_return},
    )


def ordinary_least_squares_slope(points: Sequence[Tuple[float, float]], unit: str) -> MetricResult:
    """Return y-per-x OLS slope, retaining the caller's unit definition."""

    if len(points) < 2:
        return MetricResult("slope", None, unit, CalculationMode.DERIVED, False, ("INSUFFICIENT_POINTS",))
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    x_mean = sum(xs) / len(xs)
    y_mean = sum(ys) / len(ys)
    denominator = sum((x - x_mean) ** 2 for x in xs)
    if denominator == 0:
        return MetricResult("slope", None, unit, CalculationMode.DERIVED, False, ("ZERO_X_VARIANCE",))
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in points)
    return MetricResult("slope", numerator / denominator, unit, CalculationMode.DERIVED, True)


def activity_rate_change(
    current_value: float,
    current_seconds: float,
    prior_value: float,
    prior_seconds: float,
) -> MetricResult:
    """Compare activity rates between unequal windows without calling it flow."""

    if min(current_value, prior_value, current_seconds, prior_seconds) < 0 or current_seconds == 0 or prior_seconds == 0:
        return MetricResult("activity_rate_change", None, "ratio", CalculationMode.ACTIVITY, False, ("INVALID_INPUT",))
    prior_rate = prior_value / prior_seconds
    current_rate = current_value / current_seconds
    if prior_rate == 0:
        return MetricResult("activity_rate_change", None, "ratio", CalculationMode.ACTIVITY, False, ("ZERO_PRIOR_RATE",))
    return MetricResult(
        "activity_rate_change",
        current_rate / prior_rate - 1,
        "ratio",
        CalculationMode.ACTIVITY,
        True,
        components={"current_rate": current_rate, "prior_rate": prior_rate},
    )
