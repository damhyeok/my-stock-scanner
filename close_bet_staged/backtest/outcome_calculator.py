"""Next-trading-day outcomes from daily bars."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..features.common import safe_divide
from ..schema import OUTCOME_COLUMNS


def calculate_daily_outcomes(
    daily: pd.DataFrame,
    profit_target: float = 0.02,
    stop_loss: float = -0.02,
) -> pd.DataFrame:
    required = {"date", "ticker", "open", "high", "low", "close"}
    missing = required - set(daily.columns)
    if missing:
        raise ValueError(f"daily data missing columns: {sorted(missing)}")
    if profit_target <= 0 or stop_loss >= 0:
        raise ValueError("profit_target must be positive and stop_loss must be negative")
    frame = daily.copy().sort_values(["ticker", "date"]).reset_index(drop=True)
    grouped = frame.groupby("ticker", sort=False)
    result = pd.DataFrame({
        "signal_date": pd.to_datetime(frame["date"]).dt.normalize(),
        "ticker": frame["ticker"],
        "next_trade_date": grouped["date"].shift(-1),
        "entry_close": frame["close"],
        "next_open": grouped["open"].shift(-1),
        "next_high": grouped["high"].shift(-1),
        "next_low": grouped["low"].shift(-1),
        "next_close": grouped["close"].shift(-1),
    })
    result["next_trade_date"] = pd.to_datetime(result["next_trade_date"]).dt.normalize()
    result["next_open_gap_return"] = safe_divide(result["next_open"], result["entry_close"]) - 1.0
    result["next_close_return"] = safe_divide(result["next_close"], result["entry_close"]) - 1.0
    result["mfe"] = safe_divide(result["next_high"], result["entry_close"]) - 1.0
    result["mae"] = safe_divide(result["next_low"], result["entry_close"]) - 1.0
    available = result["next_trade_date"].notna()
    plus = available & result["mfe"].ge(profit_target)
    minus = available & result["mae"].le(stop_loss)
    result["hit_plus_2pct"] = plus.astype("boolean")
    result["hit_minus_2pct"] = minus.astype("boolean")
    result.loc[~available, ["hit_plus_2pct", "hit_minus_2pct"]] = pd.NA
    result["first_touch"] = np.select(
        [~available, plus & minus, plus, minus],
        ["unavailable", "ambiguous", "plus_first", "minus_first"],
        default="neither",
    )
    result["intraday_path_available"] = False
    return result[list(OUTCOME_COLUMNS)]
