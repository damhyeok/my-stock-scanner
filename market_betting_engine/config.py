"""Versioned JSON configuration loader for expert placeholder thresholds."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .features import FeatureConfig
from .signals import SignalThresholds
from .universe import AdaptiveUniverseConfig


@dataclass(frozen=True)
class AnalysisConfig:
    config_version: str
    placeholder: bool
    feature: FeatureConfig
    signals: SignalThresholds
    universe: AdaptiveUniverseConfig


def _object(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object")
    return value


def load_analysis_config(path: str | Path) -> AnalysisConfig:
    payload = _object(json.loads(Path(path).read_text(encoding="utf-8")), "root")
    version = payload.get("config_version")
    placeholder = payload.get("placeholder")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("config_version must be a non-empty string")
    if not isinstance(placeholder, bool):
        raise ValueError("placeholder must be boolean")
    feature_values = dict(_object(payload.get("feature", {}), "feature"))
    signal_values = dict(_object(payload.get("signals", {}), "signals"))
    universe_values = dict(_object(payload.get("universe", {}), "universe"))
    feature_values["placeholder"] = placeholder
    signal_values["placeholder"] = placeholder
    universe_values["placeholder"] = placeholder
    try:
        feature = FeatureConfig(**feature_values)
        signals = SignalThresholds(**signal_values)
        universe = AdaptiveUniverseConfig(**universe_values)
    except TypeError as error:
        raise ValueError(f"unknown or invalid configuration field: {error}") from error
    if feature.short_return_bars < 1 or feature.activity_window_bars < 1:
        raise ValueError("feature bar windows must be positive")
    if signals.sector_participation_fail > signals.sector_participation_pass:
        raise ValueError("sector participation fail threshold cannot exceed pass threshold")
    if (
        universe.candidate_sector_limit < 1
        or universe.stocks_per_sector < 1
        or universe.total_stock_limit < 1
    ):
        raise ValueError("adaptive universe limits must be positive")
    return AnalysisConfig(version, placeholder, feature, signals, universe)
