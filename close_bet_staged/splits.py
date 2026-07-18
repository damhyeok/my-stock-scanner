"""Chronological split assignment and locked-test access guard."""

from __future__ import annotations

import pandas as pd

from .config import SplitConfig


def assign_splits(frame: pd.DataFrame, config: SplitConfig, date_column: str = "signal_date") -> pd.DataFrame:
    if date_column not in frame.columns:
        raise ValueError(f"missing date column: {date_column}")
    result = frame.copy()
    dates = pd.to_datetime(result[date_column]).dt.normalize()
    train_end = pd.Timestamp(config.train_end)
    validation_end = pd.Timestamp(config.validation_end)
    test_end = pd.Timestamp(config.locked_test_end)
    result["split"] = "outside_analysis"
    result.loc[dates <= train_end, "split"] = "train"
    result.loc[(dates > train_end) & (dates <= validation_end), "split"] = "validation"
    result.loc[(dates > validation_end) & (dates <= test_end), "split"] = "historical_locked_test"
    return result


def assert_locked_test_access_allowed(threshold_state: str, confirm_frozen: bool) -> None:
    """Reject outcome evaluation until thresholds have been frozen explicitly."""
    if threshold_state != "frozen" or not confirm_frozen:
        raise PermissionError(
            "historical locked-test outcomes require frozen thresholds and explicit confirmation"
        )
