"""Step 4 feature/outcome assembly without threshold fitting."""

from __future__ import annotations

import pandas as pd

from .backtest.outcome_calculator import calculate_daily_outcomes
from .config import ProjectConfig
from .data_quality import validate_daily_frame
from .features.liquidity import build_liquidity_features
from .features.market_environment import build_market_environment_features
from .features.price_action import build_price_action_features
from .splits import assign_splits


def build_step4_dataset(daily: pd.DataFrame, config: ProjectConfig) -> pd.DataFrame:
    """Build continuous Step 4 features and outcomes; do not choose thresholds."""
    clean = validate_daily_frame(daily)
    keys = ["date", "ticker", "universe_type"]
    liquidity = build_liquidity_features(clean, config.features.liquidity_window)
    price_action = build_price_action_features(
        clean,
        config.features.breakout_windows,
        config.features.swing_left_bars,
        config.features.swing_confirmation_bars,
    )
    features = liquidity.merge(price_action, on=keys, how="inner", validate="one_to_one")
    identity = clean[["date", "ticker", "universe_type", "name", "close"]].rename(
        columns={"date": "signal_date", "close": "entry_close"}
    )
    features = features.rename(columns={"date": "signal_date"})
    features = identity.merge(
        features,
        on=["signal_date", "ticker", "universe_type"],
        how="inner",
        validate="one_to_one",
    )
    outcomes = calculate_daily_outcomes(
        clean,
        profit_target=config.outcomes.profit_target,
        stop_loss=config.outcomes.stop_loss,
    )
    outcomes = outcomes.drop(columns=["entry_close"])
    result = features.merge(outcomes, on=["signal_date", "ticker"], how="left", validate="one_to_one")
    result = result[result["signal_date"] >= pd.Timestamp(config.analysis_start)].copy()
    result = assign_splits(result, config.split)
    result["universe_membership_mode"] = "retrospective_snapshot"
    result["management_status_available"] = False
    result["halt_status_available"] = False
    result["listing_date_available"] = False
    result["quote_spread_available"] = False
    return result.sort_values(["signal_date", "ticker"]).reset_index(drop=True)


def attach_market_environment(dataset: pd.DataFrame, market: pd.DataFrame) -> pd.DataFrame:
    """Attach same-day 14:30-to-15:30 market changes without forward filling."""
    if "signal_date" not in dataset.columns:
        raise ValueError("dataset must contain signal_date")
    features = build_market_environment_features(market).rename(
        columns={"trade_date": "signal_date"}
    )
    result = dataset.merge(features, on="signal_date", how="left", validate="many_to_one")
    result["market_environment_available"] = (
        result["market_environment_available"].fillna(False).astype(bool)
    )
    return result
