"""Named strategy registry for rule-based and future AI scanner models."""
import argparse
from datetime import datetime, timezone, timedelta
import json
import sqlite3

from model_schema import init_model_tables


MODEL_1_PARAMETERS = {
    "entry": {
        "price": "signal_day_close",
        "channel_window_days": 20,
        "breakdown_recovery_days": [3, 10],
        "minimum_fibonacci_recovery": 0.382,
        "minimum_target_room": 0.03,
    },
    "market_filter": {
        "universe": "market_cap_10000eok_plus",
        "minimum_breadth_above_ma20": 0.55,
        "average_ma20_slope_positive": True,
    },
    "trend_score": {
        "minimum_score": 2,
        "items": [
            "close_above_and_rising_ma20",
            "rsi_14_at_least_50",
            "volume_at_least_1_2x_20d_average",
            "higher_low_after_breakdown",
        ],
    },
    "exit": {
        "hard_stop_loss": -0.03,
        "partial_take_profit": "50pct_at_fibonacci_618",
        "remainder_exit": "close_below_ma5_or_10_trading_days",
    },
}

MODEL_1_VALIDATION = {
    "source_db": "stock_data.locked_local.db",
    "period": "2025-07-07_to_2026-07-09",
    "time_split": "train_through_2026-03-25__test_from_2026-03-26",
    "train": {"trades": 85, "win_rate_pct": 42.35, "total_return_pct": 6.97, "mdd_pct": -7.17},
    "test": {"trades": 18, "win_rate_pct": 66.67, "total_return_pct": 3.80, "mdd_pct": -1.14},
    "note": "Small out-of-sample sample; rule-based model retained, AI filter not activated.",
}


def register_model_1(db_path="stock_data.db"):
    init_model_tables(db_path)
    now = datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="seconds")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO model_strategy_registry (
                model_id, model_name, model_type, status, source_db,
                parameters_json, validation_json, description, created_at_kst, updated_at_kst
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(model_id) DO UPDATE SET
                model_name=excluded.model_name,
                model_type=excluded.model_type,
                status=excluded.status,
                source_db=excluded.source_db,
                parameters_json=excluded.parameters_json,
                validation_json=excluded.validation_json,
                description=excluded.description,
                updated_at_kst=excluded.updated_at_kst
            """,
            (
                "model_1", "반등 추세전환 (룰 기반)", "rule_based", "active",
                MODEL_1_VALIDATION["source_db"],
                json.dumps(MODEL_1_PARAMETERS, ensure_ascii=False),
                json.dumps(MODEL_1_VALIDATION, ensure_ascii=False),
                "시장 국면과 추세전환 점수를 통과한 바닥 반등 초입만 종가 기준으로 탐색합니다.",
                now, now,
            ),
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Register named scanner strategies.")
    parser.add_argument("--db-path", default="stock_data.db")
    args = parser.parse_args()
    register_model_1(args.db_path)
    print("Registered model_1: 반등 추세전환 (룰 기반)")
