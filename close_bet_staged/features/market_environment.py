"""Continuous late-session market environment features."""

from __future__ import annotations

import pandas as pd


MARKET_VALUE_COLUMNS = (
    "program_net_1430", "program_net_1530", "program_net_change_1430_1530",
    "basis_1430", "basis_1530", "basis_change_1430_1530",
)


def build_market_environment_features(
    market: pd.DataFrame,
    start_time: str = "14:30",
    end_time: str = "15:30",
) -> pd.DataFrame:
    required = {"trade_date", "analysis_type", "snapshot_time", "program_net", "basis"}
    missing = required - set(market.columns)
    if missing:
        raise ValueError(f"market data missing columns: {sorted(missing)}")
    frame = market.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.normalize()
    frame = frame[frame["analysis_type"].eq("closing")].copy()
    relevant = frame[frame["snapshot_time"].isin([start_time, end_time])]
    if relevant.duplicated(["trade_date", "snapshot_time"]).any():
        raise ValueError("market data contains duplicate date/time closing snapshots")
    start = relevant[relevant["snapshot_time"].eq(start_time)][
        ["trade_date", "program_net", "basis"]
    ].rename(columns={"program_net": "program_net_1430", "basis": "basis_1430"})
    end = relevant[relevant["snapshot_time"].eq(end_time)][
        ["trade_date", "program_net", "basis"]
    ].rename(columns={"program_net": "program_net_1530", "basis": "basis_1530"})
    result = start.merge(end, on="trade_date", how="inner", validate="one_to_one")
    result["program_net_change_1430_1530"] = result["program_net_1530"] - result["program_net_1430"]
    result["basis_change_1430_1530"] = result["basis_1530"] - result["basis_1430"]
    result["market_environment_available"] = result[[
        "program_net_1430", "program_net_1530", "basis_1430", "basis_1530"
    ]].notna().all(axis=1)
    return result.sort_values("trade_date").reset_index(drop=True)


def market_time_variation_diagnostics(features: pd.DataFrame) -> dict[str, int]:
    """Count temporal variation; repeated backfill constants are not valid history."""
    missing = set(MARKET_VALUE_COLUMNS) - set(features.columns)
    if missing:
        raise ValueError(f"market features missing columns: {sorted(missing)}")
    return {column: int(features[column].nunique(dropna=True)) for column in MARKET_VALUE_COLUMNS}


def validate_market_time_variation(features: pd.DataFrame) -> dict[str, int]:
    diagnostics = market_time_variation_diagnostics(features)
    constant = [column for column, unique_count in diagnostics.items() if unique_count <= 1]
    if len(features) > 1 and constant:
        raise ValueError(
            "market history has no temporal variation in columns: " + ", ".join(constant)
        )
    return diagnostics
