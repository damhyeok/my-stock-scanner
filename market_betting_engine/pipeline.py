"""End-to-end feature and evidence construction from normalized observations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Sequence, Tuple

from .contracts import AxisSignal, Observation
from .features import (
    BarFeatureSnapshot,
    ClosingWindowFeatures,
    FeatureConfig,
    RelativeFeatureSnapshot,
    derive_bar_features,
    derive_closing_window_features,
    derive_relative_features,
    extract_bar_series,
)
from .signals import (
    SectorFeatureSummary,
    SignalThresholds,
    aggregate_sector_features,
    build_market_axis_signals,
    build_sector_axis_signals,
    build_stock_axis_signals,
)


@dataclass(frozen=True)
class InstrumentEvidence:
    features: BarFeatureSnapshot
    relative: Optional[RelativeFeatureSnapshot]
    signals: Tuple[AxisSignal, ...]
    closing: ClosingWindowFeatures


@dataclass(frozen=True)
class SectorEvidence:
    summary: SectorFeatureSummary
    signals: Tuple[AxisSignal, ...]
    requested_members: Tuple[str, ...]
    observed_members: Tuple[str, ...]
    missing_members: Tuple[str, ...]


@dataclass(frozen=True)
class DerivedEvidenceBundle:
    market_features: BarFeatureSnapshot
    futures_features: Optional[BarFeatureSnapshot]
    market_signals: Tuple[AxisSignal, ...]
    stocks: Mapping[str, InstrumentEvidence]
    sectors: Mapping[str, SectorEvidence]
    missing_instruments: Tuple[str, ...]
    placeholder_config: bool


def derive_evidence_bundle(
    observations: Sequence[Observation],
    *,
    stock_symbols: Sequence[str],
    sector_members: Mapping[str, Sequence[str]],
    index_prefix: str = "index.KOSPI",
    futures_prefix: str = "futures.ACTIVE",
    feature_config: FeatureConfig = FeatureConfig(),
    signal_thresholds: SignalThresholds = SignalThresholds(),
) -> DerivedEvidenceBundle:
    """Create features and evidence, but never silently fill a missing instrument."""

    index_bars = extract_bar_series(observations, index_prefix)
    market_features = derive_bar_features(index_bars, index_prefix, feature_config)
    if market_features is None:
        raise ValueError(f"required market series is unavailable: {index_prefix}")

    futures_bars = extract_bar_series(observations, futures_prefix)
    futures_features = derive_bar_features(futures_bars, futures_prefix, feature_config)
    market_signals = build_market_axis_signals(
        market_features, observations, futures_features, signal_thresholds
    )

    stocks: Dict[str, InstrumentEvidence] = {}
    missing = []
    for symbol in stock_symbols:
        prefix = f"stock.{symbol}"
        bars = extract_bar_series(observations, prefix)
        features = derive_bar_features(bars, prefix, feature_config)
        if features is None:
            missing.append(symbol)
            continue
        relative = derive_relative_features(features, market_features)
        stocks[symbol] = InstrumentEvidence(
            features=features,
            relative=relative,
            signals=build_stock_axis_signals(relative, signal_thresholds),
            closing=derive_closing_window_features(bars, prefix, feature_config),
        )

    sectors: Dict[str, SectorEvidence] = {}
    for sector_name, requested in sector_members.items():
        requested_tuple = tuple(requested)
        observed_tuple = tuple(symbol for symbol in requested_tuple if symbol in stocks)
        missing_tuple = tuple(symbol for symbol in requested_tuple if symbol not in stocks)
        relative_members = tuple(
            stocks[symbol].relative
            for symbol in observed_tuple
            if stocks[symbol].relative is not None
        )
        summary = aggregate_sector_features(relative_members)
        sectors[sector_name] = SectorEvidence(
            summary=summary,
            signals=build_sector_axis_signals(summary, signal_thresholds),
            requested_members=requested_tuple,
            observed_members=observed_tuple,
            missing_members=missing_tuple,
        )

    return DerivedEvidenceBundle(
        market_features=market_features,
        futures_features=futures_features,
        market_signals=market_signals,
        stocks=stocks,
        sectors=sectors,
        missing_instruments=tuple(missing),
        placeholder_config=feature_config.placeholder or signal_thresholds.placeholder,
    )
