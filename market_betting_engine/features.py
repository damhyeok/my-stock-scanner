"""Build deterministic bar-series features from normalized observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple

from .contracts import CalculationMode, MetricResult, Observation
from .metrics import (
    PriceBar,
    activity_rate_change,
    close_location_value,
    clv_weighted_turnover_proxy,
    relative_return,
    volume_weighted_average_price,
)
from .session import KST


@dataclass(frozen=True)
class NormalizedBar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    cumulative_turnover: Optional[float] = None


@dataclass(frozen=True)
class FeatureConfig:
    short_return_bars: int = 5
    activity_window_bars: int = 5
    vwap_price_mode: str = "typical_price"
    placeholder: bool = True


@dataclass(frozen=True)
class BarFeatureSnapshot:
    instrument_prefix: str
    as_of: datetime
    bar_count: int
    last_close: float
    session_vwap: MetricResult
    vwap_distance_ratio: MetricResult
    latest_clv: MetricResult
    clv_turnover_proxy: MetricResult
    short_return: MetricResult
    activity_acceleration: MetricResult
    total_turnover_proxy: float
    flags: Tuple[str, ...] = ()


@dataclass(frozen=True)
class RelativeFeatureSnapshot:
    asset: BarFeatureSnapshot
    benchmark: BarFeatureSnapshot
    relative_short_return: MetricResult


@dataclass(frozen=True)
class ClosingWindowFeatures:
    pre_close_flow: Optional[BarFeatureSnapshot]
    close_continuity: Optional[BarFeatureSnapshot]
    closing_auction: Optional[BarFeatureSnapshot]


def extract_bar_series(
    observations: Iterable[Observation],
    instrument_prefix: str,
) -> Tuple[NormalizedBar, ...]:
    """Reconstruct bars from ``prefix.YYYYMMDD.HHMMSS.field`` metrics."""

    grouped: Dict[tuple[str, str], Dict[str, float]] = {}
    for observation in observations:
        prefix = f"{instrument_prefix}."
        if not observation.metric.startswith(prefix):
            continue
        tail = observation.metric[len(prefix):].split(".")
        if len(tail) != 3:
            continue
        date_key, time_key, field = tail
        if len(date_key) != 8 or not date_key.isdigit() or len(time_key) != 6 or not time_key.isdigit():
            continue
        if field not in {"open", "high", "low", "close", "volume", "cumulative_turnover"}:
            continue
        grouped.setdefault((date_key, time_key), {})[field] = float(observation.value)

    bars = []
    for (date_key, time_key), fields in grouped.items():
        if not {"open", "high", "low", "close", "volume"}.issubset(fields):
            continue
        try:
            timestamp = datetime.strptime(date_key + time_key, "%Y%m%d%H%M%S").replace(tzinfo=KST)
        except ValueError:
            continue
        if fields["high"] < fields["low"] or not fields["low"] <= fields["close"] <= fields["high"]:
            continue
        bars.append(
            NormalizedBar(
                timestamp=timestamp,
                open=fields["open"],
                high=fields["high"],
                low=fields["low"],
                close=fields["close"],
                volume=fields["volume"],
                cumulative_turnover=fields.get("cumulative_turnover"),
            )
        )
    return tuple(sorted(bars, key=lambda bar: bar.timestamp))


def derive_bar_features(
    bars: Sequence[NormalizedBar],
    instrument_prefix: str,
    config: FeatureConfig = FeatureConfig(),
) -> Optional[BarFeatureSnapshot]:
    if not bars:
        return None
    ordered = tuple(sorted(bars, key=lambda bar: bar.timestamp))
    latest_date = ordered[-1].timestamp.date()
    session = tuple(bar for bar in ordered if bar.timestamp.date() == latest_date)
    if not session:
        return None

    if config.vwap_price_mode == "close":
        prices = [bar.close for bar in session]
    elif config.vwap_price_mode == "typical_price":
        prices = [(bar.high + bar.low + bar.close) / 3 for bar in session]
    else:
        raise ValueError(f"unsupported vwap_price_mode: {config.vwap_price_mode}")
    volumes = [bar.volume for bar in session]
    vwap = volume_weighted_average_price(prices, volumes)
    if vwap.available and vwap.value:
        vwap_distance = MetricResult(
            "vwap_distance_ratio",
            session[-1].close / float(vwap.value) - 1,
            "ratio",
            CalculationMode.DERIVED,
            True,
        )
    else:
        vwap_distance = MetricResult(
            "vwap_distance_ratio", None, "ratio", CalculationMode.DERIVED, False, ("VWAP_UNAVAILABLE",)
        )
    latest_clv = close_location_value(session[-1].high, session[-1].low, session[-1].close)
    flow_proxy = clv_weighted_turnover_proxy(
        PriceBar(bar.high, bar.low, bar.close, bar.volume) for bar in session
    )

    return_count = min(max(config.short_return_bars, 1), len(session) - 1)
    if return_count > 0 and session[-return_count - 1].close != 0:
        short_return = MetricResult(
            "short_return",
            session[-1].close / session[-return_count - 1].close - 1,
            "return",
            CalculationMode.DERIVED,
            True,
            components={"bars": float(return_count)},
        )
    else:
        short_return = MetricResult(
            "short_return", None, "return", CalculationMode.DERIVED, False, ("INSUFFICIENT_BARS",)
        )

    window = max(config.activity_window_bars, 1)
    if len(session) >= window * 2:
        prior = session[-window * 2:-window]
        current = session[-window:]
        prior_activity = sum(bar.close * bar.volume for bar in prior)
        current_activity = sum(bar.close * bar.volume for bar in current)
        activity = activity_rate_change(current_activity, window * 60, prior_activity, window * 60)
    else:
        activity = MetricResult(
            "activity_rate_change", None, "ratio", CalculationMode.ACTIVITY, False, ("INSUFFICIENT_BARS",)
        )

    flags = ("PLACEHOLDER_CONFIG",) if config.placeholder else ()
    return BarFeatureSnapshot(
        instrument_prefix=instrument_prefix,
        as_of=session[-1].timestamp,
        bar_count=len(session),
        last_close=session[-1].close,
        session_vwap=vwap,
        vwap_distance_ratio=vwap_distance,
        latest_clv=latest_clv,
        clv_turnover_proxy=flow_proxy,
        short_return=short_return,
        activity_acceleration=activity,
        total_turnover_proxy=sum(bar.close * bar.volume for bar in session),
        flags=flags,
    )


def derive_relative_features(
    asset: BarFeatureSnapshot,
    benchmark: BarFeatureSnapshot,
) -> RelativeFeatureSnapshot:
    if asset.short_return.available and benchmark.short_return.available:
        relative = relative_return(float(asset.short_return.value), float(benchmark.short_return.value))
    else:
        relative = MetricResult(
            "relative_return", None, "return", CalculationMode.DERIVED, False, ("RETURN_UNAVAILABLE",)
        )
    return RelativeFeatureSnapshot(asset, benchmark, relative)


def _window(
    bars: Sequence[NormalizedBar],
    start: time,
    end: time,
    *,
    include_end: bool = False,
) -> Tuple[NormalizedBar, ...]:
    selected = []
    for bar in bars:
        current = bar.timestamp.astimezone(KST).time().replace(tzinfo=None)
        if current >= start and (current <= end if include_end else current < end):
            selected.append(bar)
    return tuple(selected)


def derive_closing_window_features(
    bars: Sequence[NormalizedBar],
    instrument_prefix: str,
    config: FeatureConfig = FeatureConfig(),
) -> ClosingWindowFeatures:
    """Keep the auction impact separate from pre-close continuous trading."""

    def derive(selected: Sequence[NormalizedBar]) -> Optional[BarFeatureSnapshot]:
        return derive_bar_features(selected, instrument_prefix, config) if selected else None

    return ClosingWindowFeatures(
        pre_close_flow=derive(_window(bars, time(14, 30), time(15, 0))),
        close_continuity=derive(_window(bars, time(15, 0), time(15, 20))),
        closing_auction=derive(_window(bars, time(15, 20), time(15, 30), include_end=True)),
    )
