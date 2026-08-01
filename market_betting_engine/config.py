"""Versioned JSON configuration loader for expert placeholder thresholds."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .features import FeatureConfig
from .signals import SignalThresholds


@dataclass(frozen=True)
class AnalysisConfig:
    config_version: str
    placeholder: bool
    feature: FeatureConfig
    signals: SignalThresholds


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
    feature_values["placeholder"] = placeholder
    signal_values["placeholder"] = placeholder
    try:
        feature = FeatureConfig(**feature_values)
        signals = SignalThresholds(**signal_values)
    except TypeError as error:
        raise ValueError(f"unknown or invalid configuration field: {error}") from error
    if feature.short_return_bars < 1 or feature.activity_window_bars < 1:
        raise ValueError("feature bar windows must be positive")
    if signals.sector_participation_fail > signals.sector_participation_pass:
        raise ValueError("sector participation fail threshold cannot exceed pass threshold")
    return AnalysisConfig(version, placeholder, feature, signals)
