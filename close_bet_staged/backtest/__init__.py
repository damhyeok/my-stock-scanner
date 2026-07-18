"""Backtest components for the staged close-bet project."""

from .outcome_calculator import calculate_daily_outcomes
from .signal_generator import apply_stage1_filters
from .threshold_analysis import quantile_bucket_report, summarize_mask

__all__ = [
    "apply_stage1_filters", "calculate_daily_outcomes", "quantile_bucket_report", "summarize_mask",
]
