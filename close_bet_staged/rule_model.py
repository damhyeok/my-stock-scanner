"""Transparent close-bet rule model with a manual external market gate."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


BREAKOUT_WINDOWS = (5, 10, 20, 60)


def load_rule_config(path: str | Path) -> dict[str, Any]:
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    required = {
        "price_action", "location", "afternoon_flow", "relative_strength",
        "volatility", "liquidity",
    }
    missing = required - set(config)
    if missing:
        raise ValueError(f"rule model config missing sections: {sorted(missing)}")
    return config


def _percentile_by_date(frame: pd.DataFrame, column: str) -> pd.Series:
    return frame.groupby("signal_date", sort=False)[column].rank(pct=True, method="average")


def _condition_count(*conditions: pd.Series) -> pd.Series:
    return pd.concat([condition.fillna(False).astype(int) for condition in conditions], axis=1).sum(axis=1)


def _join_reasons(frame: pd.DataFrame) -> pd.Series:
    stage_columns = (
        ("liquidity_pass", "유동성"),
        ("price_action_pass", "Price Action"),
        ("location_pass", "AVWAP·위치"),
        ("afternoon_flow_pass", "오후 흐름"),
        ("relative_strength_pass", "상대강도"),
        ("volatility_pass", "변동성"),
    )
    return frame.apply(
        lambda row: "통과" if row["technical_pass"] else "탈락: " + ", ".join(
            label for column, label in stage_columns if not bool(row[column])
        ),
        axis=1,
    )


def evaluate_rule_model(
    dataset: pd.DataFrame,
    config: dict[str, Any],
    manual_market_pass: bool | None = None,
) -> pd.DataFrame:
    """Evaluate hard stage gates; no blended score is calculated."""
    frame = dataset.copy().sort_values(["signal_date", "ticker"]).reset_index(drop=True)
    price = config["price_action"]
    location = config["location"]
    flow = config["afternoon_flow"]
    rs = config["relative_strength"]
    volatility = config["volatility"]
    liquidity = config["liquidity"]

    frame["prior_value_percentile"] = _percentile_by_date(
        frame, "avg_trading_value_20d_prior"
    )
    frame["today_value_ratio_percentile"] = _percentile_by_date(
        frame, "trading_value_ratio_20d_prior"
    )
    frame["liquidity_pass"] = (
        frame["prior_value_percentile"].ge(liquidity["min_prior_value_percentile"])
        & frame["today_value_ratio_percentile"].ge(
            liquidity["min_today_value_ratio_percentile"]
        )
    )

    frame["setup_a"] = (
        frame["higher_low_pct"].ge(0)
        & frame["low_vs_support_pct"].ge(-price["support_break_tolerance"])
        & frame["low_vs_support_pct"].le(price["max_support_distance"])
        & frame["clv"].ge(price["min_clv"])
    )
    breakout_masks = []
    for window in BREAKOUT_WINDOWS:
        mask = (
            frame[f"breakout_pct_{window}"].ge(0)
            & frame["clv"].ge(price["min_clv"])
            & frame["upper_wick_ratio"].le(price["max_upper_wick_ratio"])
        )
        frame[f"setup_b_{window}"] = mask
        breakout_masks.append(mask)
    frame["setup_b"] = pd.concat(breakout_masks, axis=1).any(axis=1)
    frame["setup_c"] = (
        frame["low_vs_support_pct"].lt(0)
        & frame["close_vs_support_pct"].ge(0)
        & frame["lower_wick_ratio"].ge(price["min_lower_wick_ratio"])
        & frame["volume_ratio_20d_prior"].ge(price["min_recovery_volume_ratio"])
    )
    frame["price_action_pass"] = frame[["setup_a", "setup_b", "setup_c"]].any(axis=1)
    frame["price_action_types"] = frame.apply(
        lambda row: "|".join(
            label for label, column in (("A", "setup_a"), ("B", "setup_b"), ("C", "setup_c"))
            if bool(row[column])
        ),
        axis=1,
    )

    frame["above_swing_avwap"] = frame["close_vs_swing_avwap_pct"].ge(0)
    frame["above_volume_anchor_avwap"] = frame["close_vs_volume_anchor_avwap_pct"].ge(0)
    frame["avwap_above_count"] = _condition_count(
        frame["above_swing_avwap"], frame["above_volume_anchor_avwap"]
    )
    frame["sampled_poc_recovered"] = frame["close_vs_sampled_poc_pct"].ge(0)
    frame["location_data_available"] = (
        frame[["close_vs_swing_avwap_pct", "close_vs_volume_anchor_avwap_pct"]]
        .notna().any(axis=1)
        & frame["close_vs_sampled_poc_pct"].notna()
    )
    poc_gate = (
        frame["sampled_poc_recovered"]
        if location["require_sampled_poc_recovery"]
        else pd.Series(True, index=frame.index)
    )
    frame["location_pass"] = (
        frame["location_data_available"]
        & frame["avwap_above_count"].ge(location["minimum_avwaps_above"])
        & poc_gate
    )

    frame["flow_price_not_weaker"] = frame["return_after_1430"].ge(
        flow["min_return_after_1430"]
    )
    frame["flow_no_new_low"] = frame["sampled_new_low_count"].le(
        flow["max_new_low_count"]
    )
    frame["flow_up_volume_dominant"] = frame["sampled_up_down_volume_ratio"].ge(
        flow["min_up_down_volume_ratio"]
    )
    frame["flow_strong_close"] = frame["sampled_afternoon_clv"].ge(
        flow["min_sampled_clv"]
    )
    frame["afternoon_data_available"] = frame[
        [
            "return_after_1430", "sampled_new_low_count",
            "sampled_up_down_volume_ratio", "sampled_afternoon_clv",
        ]
    ].notna().all(axis=1)
    frame["afternoon_condition_count"] = _condition_count(
        frame["flow_price_not_weaker"], frame["flow_no_new_low"],
        frame["flow_up_volume_dominant"], frame["flow_strong_close"],
    )
    frame["afternoon_flow_pass"] = (
        frame["afternoon_data_available"]
        & frame["afternoon_condition_count"].ge(flow["min_conditions"])
    )

    frame["market_return_20d_proxy"] = frame.groupby("signal_date", sort=False)[
        "stock_return_20d"
    ].transform("median")
    sector_count = frame.groupby(["signal_date", "sector"], sort=False)["ticker"].transform("count")
    sector_median = frame.groupby(["signal_date", "sector"], sort=False)[
        "stock_return_5d"
    ].transform("median")
    frame["sector_proxy_available"] = frame["sector"].notna() & sector_count.ge(
        rs["minimum_sector_members"]
    )
    universe_median_5d = frame.groupby("signal_date", sort=False)["stock_return_5d"].transform("median")
    frame["sector_return_5d_proxy"] = sector_median.where(
        frame["sector_proxy_available"], universe_median_5d
    )
    frame["rs5_sector_proxy"] = frame["stock_return_5d"] - frame["sector_return_5d_proxy"]
    frame["rs20_market_proxy"] = frame["stock_return_20d"] - frame["market_return_20d_proxy"]
    frame["sector_strength_percentile"] = frame.groupby(
        ["signal_date", "sector"], sort=False
    )["stock_return_5d"].rank(pct=True, method="average")
    universe_percentile_5d = _percentile_by_date(frame, "stock_return_5d")
    frame["sector_strength_percentile"] = frame["sector_strength_percentile"].where(
        frame["sector_proxy_available"], universe_percentile_5d
    )
    frame["market_strength_percentile"] = _percentile_by_date(frame, "stock_return_20d")
    frame["relative_strength_pass"] = (
        frame["rs5_sector_proxy"].ge(0)
        & frame["rs20_market_proxy"].ge(0)
        & frame["sector_strength_percentile"].ge(rs["min_sector_percentile"])
        & frame["market_strength_percentile"].ge(rs["min_market_percentile"])
    )
    frame["relative_strength_quality"] = np.where(
        frame["sector_proxy_available"], "sector_constituent_proxy", "universe_proxy"
    )

    frame["atr_percentile"] = _percentile_by_date(frame, "atr14_pct")
    frame["volatility_pass"] = (
        frame["atr14_pct"].notna()
        & frame["atr_percentile"].between(
            volatility["min_atr_percentile"], volatility["max_atr_percentile"], inclusive="both"
        )
        & frame["atr14_pct"].le(volatility["max_atr_pct"])
    )

    stage_columns = [
        "liquidity_pass", "price_action_pass", "location_pass", "afternoon_flow_pass",
        "relative_strength_pass", "volatility_pass",
    ]
    frame["technical_pass"] = frame[stage_columns].all(axis=1)
    if manual_market_pass is None:
        frame["market_manual_state"] = "pending"
        frame["final_pass"] = False
        frame["decision"] = np.where(
            frame["technical_pass"], "기술조건 통과·시장판단 대기", "기술조건 탈락"
        )
    else:
        frame["market_manual_state"] = "pass" if manual_market_pass else "blocked"
        frame["final_pass"] = frame["technical_pass"] & bool(manual_market_pass)
        frame["decision"] = np.select(
            [frame["final_pass"], frame["technical_pass"]],
            ["최종 통과", "기술조건 통과·시장 차단"],
            default="기술조건 탈락",
        )
    frame["stage_reason"] = _join_reasons(frame)
    return frame
