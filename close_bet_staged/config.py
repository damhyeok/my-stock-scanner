"""Typed configuration for the staged close-bet project."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DatabaseConfig:
    path: str
    daily_table: str = "model_ohlcv_daily"
    market_table: str = "market_strength_snapshots"


@dataclass(frozen=True)
class SplitConfig:
    train_end: str
    validation_end: str
    locked_test_end: str


@dataclass(frozen=True)
class FeatureConfig:
    liquidity_window: int
    breakout_windows: tuple[int, ...]
    swing_left_bars: int
    swing_confirmation_bars: int


@dataclass(frozen=True)
class OutcomeConfig:
    profit_target: float = 0.02
    stop_loss: float = -0.02


@dataclass(frozen=True)
class ProjectConfig:
    database: DatabaseConfig
    split: SplitConfig
    features: FeatureConfig
    outcomes: OutcomeConfig
    universe_type: str
    analysis_start: str
    timezone: str = "Asia/Seoul"


def _required(mapping: dict[str, Any], key: str) -> Any:
    if key not in mapping:
        raise ValueError(f"missing configuration key: {key}")
    return mapping[key]


def load_config(path: str | Path) -> ProjectConfig:
    """Load and validate a JSON configuration file."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    database = _required(raw, "database")
    split = _required(raw, "split")
    features = _required(raw, "features")
    outcomes = _required(raw, "outcomes")
    breakout_windows = tuple(int(value) for value in _required(features, "breakout_windows"))
    if not breakout_windows or any(value < 2 for value in breakout_windows):
        raise ValueError("breakout_windows must contain integers greater than one")
    if sorted(set(breakout_windows)) != list(breakout_windows):
        raise ValueError("breakout_windows must be unique and sorted")
    feature_config = FeatureConfig(
        liquidity_window=int(_required(features, "liquidity_window")),
        breakout_windows=breakout_windows,
        swing_left_bars=int(_required(features, "swing_left_bars")),
        swing_confirmation_bars=int(_required(features, "swing_confirmation_bars")),
    )
    if feature_config.liquidity_window < 2:
        raise ValueError("liquidity_window must be greater than one")
    if feature_config.swing_left_bars < 1 or feature_config.swing_confirmation_bars < 1:
        raise ValueError("swing windows must be positive")
    split_config = SplitConfig(
        train_end=str(_required(split, "train_end")),
        validation_end=str(_required(split, "validation_end")),
        locked_test_end=str(_required(split, "locked_test_end")),
    )
    if not (split_config.train_end < split_config.validation_end < split_config.locked_test_end):
        raise ValueError("split boundaries must be strictly chronological")
    outcome_config = OutcomeConfig(
        profit_target=float(_required(outcomes, "profit_target")),
        stop_loss=float(_required(outcomes, "stop_loss")),
    )
    if outcome_config.profit_target <= 0 or outcome_config.stop_loss >= 0:
        raise ValueError("profit_target must be positive and stop_loss must be negative")
    return ProjectConfig(
        database=DatabaseConfig(
            path=str(_required(database, "path")),
            daily_table=str(database.get("daily_table", "model_ohlcv_daily")),
            market_table=str(database.get("market_table", "market_strength_snapshots")),
        ),
        split=split_config,
        features=feature_config,
        outcomes=outcome_config,
        universe_type=str(_required(raw, "universe_type")),
        analysis_start=str(_required(raw, "analysis_start")),
        timezone=str(raw.get("timezone", "Asia/Seoul")),
    )
