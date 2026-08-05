"""Convert deterministic features into explicit, configurable axis evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence, Tuple

from .contracts import AxisSignal, AxisStatus, Observation
from .features import BarFeatureSnapshot, RelativeFeatureSnapshot
from .metrics import ordinary_least_squares_slope


@dataclass(frozen=True)
class SignalThresholds:
    positive_vwap_distance: float = 0.0
    negative_vwap_distance: float = 0.0
    positive_clv_proxy_ratio: float = 0.10
    negative_clv_proxy_ratio: float = -0.10
    positive_relative_return: float = 0.003
    negative_relative_return: float = -0.003
    positive_activity_acceleration: float = 0.20
    sector_participation_pass: float = 0.50
    sector_participation_fail: float = 0.40
    sector_minimum_supporting_members: int = 3
    sector_minimum_observed_members: int = 4
    placeholder: bool = True


@dataclass(frozen=True)
class SectorFeatureSummary:
    member_count: int
    above_vwap_ratio: Optional[float]
    outperforming_ratio: Optional[float]
    equal_weight_relative_return: Optional[float]
    activity_confirming_ratio: Optional[float]


def _signal(axis: str, status: AxisStatus, code: str, message: str) -> AxisSignal:
    return AxisSignal(axis, status, code, message)


def build_price_action_signals(
    features: BarFeatureSnapshot,
    thresholds: SignalThresholds = SignalThresholds(),
) -> Tuple[AxisSignal, ...]:
    """VWAP and CLV proxy share one axis because both derive from price bars."""

    if not features.vwap_distance_ratio.available or not features.short_return.available:
        vwap_signal = _signal(
            "price_action", AxisStatus.UNAVAILABLE, "PRICE_STRUCTURE_UNAVAILABLE",
            "VWAP distance or short return is unavailable",
        )
    else:
        distance = float(features.vwap_distance_ratio.value)
        short_return = float(features.short_return.value)
        if distance > thresholds.positive_vwap_distance and short_return > 0:
            status = AxisStatus.PASS
        elif distance < thresholds.negative_vwap_distance and short_return < 0:
            status = AxisStatus.FAIL
        else:
            status = AxisStatus.WARNING
        vwap_signal = _signal(
            "price_action", status, "PRICE_VWAP_STRUCTURE",
            f"VWAP distance={distance:.4f}, short return={short_return:.4f}",
        )

    proxy_ratio = features.clv_turnover_proxy.components.get("proxy_ratio")
    if not features.clv_turnover_proxy.available or proxy_ratio is None:
        proxy_signal = _signal(
            "price_action", AxisStatus.UNAVAILABLE, "CLV_FLOW_PROXY_UNAVAILABLE",
            "CLV-weighted turnover proxy is unavailable",
        )
    elif proxy_ratio >= thresholds.positive_clv_proxy_ratio:
        proxy_signal = _signal(
            "price_action", AxisStatus.PASS, "CLV_FLOW_PROXY_POSITIVE",
            f"price-derived proxy ratio={proxy_ratio:.4f}; this is not actual net flow",
        )
    elif proxy_ratio <= thresholds.negative_clv_proxy_ratio:
        proxy_signal = _signal(
            "price_action", AxisStatus.FAIL, "CLV_FLOW_PROXY_NEGATIVE",
            f"price-derived proxy ratio={proxy_ratio:.4f}; this is not actual net flow",
        )
    else:
        proxy_signal = _signal(
            "price_action", AxisStatus.WARNING, "CLV_FLOW_PROXY_NEUTRAL",
            f"price-derived proxy ratio={proxy_ratio:.4f}; this is not actual net flow",
        )
    return vwap_signal, proxy_signal


def build_activity_signal(
    features: BarFeatureSnapshot,
    thresholds: SignalThresholds = SignalThresholds(),
) -> AxisSignal:
    if not features.activity_acceleration.available or not features.short_return.available:
        return _signal(
            "activity", AxisStatus.UNAVAILABLE, "ACTIVITY_CONFIRMATION_UNAVAILABLE",
            "activity acceleration or price direction is unavailable",
        )
    acceleration = float(features.activity_acceleration.value)
    direction = float(features.short_return.value)
    if acceleration >= thresholds.positive_activity_acceleration and direction > 0:
        status, code = AxisStatus.PASS, "RISING_ACTIVITY_CONFIRMS_ADVANCE"
    elif acceleration >= thresholds.positive_activity_acceleration and direction < 0:
        status, code = AxisStatus.FAIL, "RISING_ACTIVITY_CONFIRMS_DECLINE"
    else:
        status, code = AxisStatus.WARNING, "ACTIVITY_NOT_EXPANDING"
    return _signal(
        "activity", status, code,
        f"activity rate change={acceleration:.4f}, short return={direction:.4f}",
    )


def build_relative_strength_signal(
    relative: RelativeFeatureSnapshot,
    thresholds: SignalThresholds = SignalThresholds(),
) -> AxisSignal:
    metric = relative.relative_short_return
    if not metric.available:
        return _signal(
            "relative_strength", AxisStatus.UNAVAILABLE, "RELATIVE_RETURN_UNAVAILABLE",
            "asset or benchmark return is unavailable",
        )
    value = float(metric.value)
    if value >= thresholds.positive_relative_return:
        status = AxisStatus.PASS
    elif value <= thresholds.negative_relative_return:
        status = AxisStatus.FAIL
    else:
        status = AxisStatus.WARNING
    return _signal(
        "relative_strength", status, "RELATIVE_SHORT_RETURN",
        f"asset minus benchmark short return={value:.4f}",
    )


def build_stock_axis_signals(
    relative: RelativeFeatureSnapshot,
    thresholds: SignalThresholds = SignalThresholds(),
) -> Tuple[AxisSignal, ...]:
    return (
        *build_price_action_signals(relative.asset, thresholds),
        build_relative_strength_signal(relative, thresholds),
        build_activity_signal(relative.asset, thresholds),
    )


def build_program_actual_flow_signal(observations: Iterable[Observation]) -> AxisSignal:
    """Use provider-reported non-arbitrage net amount, never a price proxy."""

    points = []
    for observation in observations:
        if observation.metric.startswith("program.KOSPI.") and observation.metric.endswith(".non_arbitrage_net_amount"):
            parts = observation.metric.split(".")
            if len(parts) >= 5 and parts[-3].isdigit() and parts[-2].isdigit():
                points.append((parts[-3] + parts[-2], float(observation.value)))
    points.sort(key=lambda point: point[0])
    if len(points) < 2:
        return _signal(
            "actual_flow", AxisStatus.UNAVAILABLE, "PROGRAM_FLOW_SLOPE_UNAVAILABLE",
            "at least two provider-reported program observations are required",
        )
    slope = ordinary_least_squares_slope(
        [(float(index), value) for index, (_, value) in enumerate(points)],
        "provider_amount_unit_per_observation",
    )
    latest = points[-1][1]
    if float(slope.value) > 0 and latest >= 0:
        status = AxisStatus.PASS
    elif float(slope.value) < 0 and latest <= 0:
        status = AxisStatus.FAIL
    else:
        status = AxisStatus.WARNING
    return _signal(
        "actual_flow", status, "PROGRAM_NON_ARBITRAGE_ACTUAL_FLOW",
        f"provider net amount latest={latest:.4f}, slope={float(slope.value):.4f}",
    )


def build_futures_confirmation_signal(features: Optional[BarFeatureSnapshot]) -> AxisSignal:
    if features is None or not features.vwap_distance_ratio.available or not features.short_return.available:
        return _signal(
            "futures", AxisStatus.UNAVAILABLE, "FUTURES_PRICE_CONFIRMATION_UNAVAILABLE",
            "futures price structure is unavailable; basis is not inferred",
        )
    distance = float(features.vwap_distance_ratio.value)
    short_return = float(features.short_return.value)
    if distance > 0 and short_return > 0:
        status = AxisStatus.PASS
    elif distance < 0 and short_return < 0:
        status = AxisStatus.FAIL
    else:
        status = AxisStatus.WARNING
    return _signal(
        "futures", status, "FUTURES_PRICE_CONFIRMATION",
        f"futures VWAP distance={distance:.4f}, short return={short_return:.4f}; not a basis signal",
    )


def build_market_axis_signals(
    index_features: BarFeatureSnapshot,
    observations: Iterable[Observation],
    futures_features: Optional[BarFeatureSnapshot],
    thresholds: SignalThresholds = SignalThresholds(),
) -> Tuple[AxisSignal, ...]:
    return (
        *build_price_action_signals(index_features, thresholds),
        build_program_actual_flow_signal(observations),
        build_futures_confirmation_signal(futures_features),
    )


def aggregate_sector_features(members: Sequence[RelativeFeatureSnapshot]) -> SectorFeatureSummary:
    if not members:
        return SectorFeatureSummary(0, None, None, None, None)
    above_vwap = [
        item.asset.vwap_distance_ratio.value > 0
        for item in members if item.asset.vwap_distance_ratio.available
    ]
    relative_values = [
        float(item.relative_short_return.value)
        for item in members if item.relative_short_return.available
    ]
    activity_confirming = [
        float(item.asset.activity_acceleration.value) > 0 and float(item.asset.short_return.value) > 0
        for item in members
        if item.asset.activity_acceleration.available and item.asset.short_return.available
    ]
    return SectorFeatureSummary(
        member_count=len(members),
        above_vwap_ratio=sum(above_vwap) / len(above_vwap) if above_vwap else None,
        outperforming_ratio=sum(value > 0 for value in relative_values) / len(relative_values) if relative_values else None,
        equal_weight_relative_return=sum(relative_values) / len(relative_values) if relative_values else None,
        activity_confirming_ratio=sum(activity_confirming) / len(activity_confirming) if activity_confirming else None,
    )


def build_sector_axis_signals(
    summary: SectorFeatureSummary,
    thresholds: SignalThresholds = SignalThresholds(),
) -> Tuple[AxisSignal, ...]:
    def ratio_signal(value: Optional[float], axis: str, code: str) -> AxisSignal:
        if value is None or summary.member_count < thresholds.sector_minimum_observed_members:
            return _signal(axis, AxisStatus.UNAVAILABLE, f"{code}_UNAVAILABLE", f"{axis} is unavailable")
        supporting_members = round(value * summary.member_count)
        enough_members = supporting_members >= thresholds.sector_minimum_supporting_members
        if value >= thresholds.sector_participation_pass and enough_members:
            status = AxisStatus.PASS
        elif value < thresholds.sector_participation_fail or not enough_members:
            status = AxisStatus.FAIL
        else:
            status = AxisStatus.WARNING
        return _signal(
            axis,
            status,
            code,
            f"equal-weight member ratio={value:.4f}, supporting members={supporting_members}/{summary.member_count}",
        )

    participation = ratio_signal(summary.above_vwap_ratio, "sector_participation", "SECTOR_ABOVE_VWAP_RATIO")
    breadth = ratio_signal(summary.outperforming_ratio, "sector_relative_strength", "SECTOR_OUTPERFORMING_RATIO")
    activity = ratio_signal(summary.activity_confirming_ratio, "sector_activity", "SECTOR_ACTIVITY_CONFIRMING_RATIO")
    return participation, breadth, activity
