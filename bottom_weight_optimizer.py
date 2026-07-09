import argparse
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from model_schema import init_model_tables


KST = timezone(timedelta(hours=9))


FEATURES = (
    "rsi_bottom",
    "near_20d_low",
    "near_60d_low",
    "pullback_20d",
    "bb_lower",
    "volume_energy",
    "macd_turn",
    "obv_support",
)


def _clip(value, low=0.0, high=1.0):
    return max(low, min(high, value))


def _feature_vector(row):
    rsi = row["rsi_14"]
    distance_20 = row["distance_from_20d_low"]
    distance_60 = row["distance_from_60d_low"]
    return_20d = row["return_20d"]
    bb_position = row["bb_position"]
    volume_ratio = row["volume_ratio_20"]
    macd_hist = row["macd_hist"]
    obv = row["obv"]
    obv_ma_20 = row["obv_ma_20"]

    rsi_bottom = 0.0
    if rsi is not None:
        if 30 <= rsi <= 45:
            rsi_bottom = 1.0
        elif rsi < 30:
            rsi_bottom = 0.75
        elif rsi <= 55:
            rsi_bottom = _clip((55 - rsi) / 10)

    return {
        "rsi_bottom": rsi_bottom,
        "near_20d_low": 0.0 if distance_20 is None else _clip((12 - distance_20) / 12),
        "near_60d_low": 0.0 if distance_60 is None else _clip((18 - distance_60) / 18),
        "pullback_20d": 0.0 if return_20d is None else _clip((-return_20d) / 18),
        "bb_lower": 0.0 if bb_position is None else _clip((0.45 - bb_position) / 0.45),
        "volume_energy": 0.0 if volume_ratio is None else _clip((volume_ratio - 0.7) / 1.1),
        "macd_turn": 0.0 if macd_hist is None else _clip((macd_hist + 1500) / 3000),
        "obv_support": 1.0 if obv is not None and obv_ma_20 is not None and obv >= obv_ma_20 else 0.0,
    }


def _score(vector, weights):
    total_weight = sum(weights.values())
    if total_weight <= 0:
        return 0.0
    return sum(vector[name] * weights[name] for name in FEATURES) / total_weight * 100


class BottomWeightOptimizer:
    def __init__(self, db_path="stock_data.db"):
        self.db_path = db_path
        init_model_tables(db_path=db_path)
        self.created_at_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

    def _load_dataset(self, universe_type, label_type):
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT
                    f.date, f.ticker, f.rsi_14, f.distance_from_20d_low,
                    f.distance_from_60d_low, f.return_20d, f.bb_position,
                    f.volume_ratio_20, f.macd_hist, f.obv, f.obv_ma_20,
                    l.is_success, l.future_return_10d, l.future_max_gain_10d,
                    l.future_max_drawdown_10d
                FROM model_feature_daily f
                JOIN model_backtest_labels l
                  ON l.date = f.date
                 AND l.ticker = f.ticker
                 AND l.universe_type = f.universe_type
                WHERE f.universe_type = ?
                  AND l.label_type = ?
                  AND f.rsi_14 IS NOT NULL
                  AND f.ma_120 IS NOT NULL
                  AND l.future_return_10d IS NOT NULL
                ORDER BY f.date, f.ticker
                """,
                (universe_type, label_type),
            ).fetchall()
        dataset = []
        for row in rows:
            item = dict(row)
            item["vector"] = _feature_vector(item)
            dataset.append(item)
        return dataset

    @staticmethod
    def _split_dataset(dataset, train_ratio=0.7):
        dates = sorted({row["date"] for row in dataset})
        split_index = max(1, int(len(dates) * train_ratio))
        train_end = dates[split_index - 1]
        test_start = dates[split_index] if split_index < len(dates) else dates[-1]
        train = [row for row in dataset if row["date"] <= train_end]
        test = [row for row in dataset if row["date"] >= test_start]
        return train, test, train_end, test_start

    @staticmethod
    def _evaluate(dataset, weights, top_fraction=0.08, min_selected=30):
        scored = []
        for row in dataset:
            scored.append((_score(row["vector"], weights), row))
        scored.sort(key=lambda item: item[0], reverse=True)
        selected_count = max(min_selected, int(len(scored) * top_fraction))
        selected = [row for _, row in scored[:selected_count]]
        if not selected:
            return {
                "selected_count": 0,
                "precision": 0.0,
                "avg_return_10d": 0.0,
                "avg_max_gain_10d": 0.0,
                "avg_drawdown_10d": 0.0,
            }
        wins = sum(row["is_success"] for row in selected)
        return {
            "selected_count": len(selected),
            "precision": wins * 100 / len(selected),
            "avg_return_10d": sum(row["future_return_10d"] for row in selected) / len(selected),
            "avg_max_gain_10d": sum(row["future_max_gain_10d"] for row in selected) / len(selected),
            "avg_drawdown_10d": sum(row["future_max_drawdown_10d"] for row in selected) / len(selected),
        }

    def _evaluate_by_time_chunks(self, dataset, weights, chunks=4):
        dates = sorted({row["date"] for row in dataset})
        if not dates:
            return {
                "avg_precision": 0.0,
                "min_precision": 0.0,
                "avg_return_10d": 0.0,
                "chunk_count": 0,
            }
        chunk_size = max(1, len(dates) // chunks)
        metrics = []
        for start in range(0, len(dates), chunk_size):
            chunk_dates = set(dates[start:start + chunk_size])
            chunk_rows = [row for row in dataset if row["date"] in chunk_dates]
            if len(chunk_rows) < 100:
                continue
            metrics.append(self._evaluate(chunk_rows, weights, min_selected=15))
        if not metrics:
            return {
                "avg_precision": 0.0,
                "min_precision": 0.0,
                "avg_return_10d": 0.0,
                "chunk_count": 0,
            }
        return {
            "avg_precision": sum(item["precision"] for item in metrics) / len(metrics),
            "min_precision": min(item["precision"] for item in metrics),
            "avg_return_10d": sum(item["avg_return_10d"] for item in metrics) / len(metrics),
            "chunk_count": len(metrics),
        }

    def _candidate_weights(self):
        # Fast first-pass search over interpretable feature groups.
        groups = [
            ("rsi_bottom", "near_20d_low"),
            ("rsi_bottom", "near_60d_low"),
            ("pullback_20d", "volume_energy"),
            ("near_20d_low", "bb_lower"),
            ("near_60d_low", "macd_turn"),
            ("rsi_bottom", "near_20d_low", "volume_energy"),
            ("rsi_bottom", "near_20d_low", "bb_lower"),
            ("pullback_20d", "near_60d_low", "volume_energy"),
            ("pullback_20d", "near_20d_low", "macd_turn"),
            ("bb_lower", "volume_energy", "obv_support"),
            ("rsi_bottom", "near_20d_low", "near_60d_low", "pullback_20d"),
            ("rsi_bottom", "near_20d_low", "bb_lower", "volume_energy"),
            ("near_20d_low", "near_60d_low", "pullback_20d", "volume_energy"),
            ("rsi_bottom", "near_20d_low", "pullback_20d", "macd_turn", "obv_support"),
            FEATURES,
        ]
        emphasis_values = (1, 2, 3)
        seen = set()
        for group in groups:
            for emphasis_feature in group:
                for emphasis in emphasis_values:
                    weights = {name: 0 for name in FEATURES}
                    for name in group:
                        weights[name] = 1
                    weights[emphasis_feature] = emphasis
                    key = tuple(weights[name] for name in FEATURES)
                    if key not in seen:
                        seen.add(key)
                        yield weights

    def optimize(self, universe_type="market_cap_top_100", label_type="bottom_10d_5pct_safe"):
        dataset = self._load_dataset(universe_type, label_type)
        train, test, train_end, test_start = self._split_dataset(dataset)
        best = None
        best_train_metrics = None

        for weights in self._candidate_weights():
            stability = self._evaluate_by_time_chunks(train, weights)
            train_metrics = self._evaluate(train, weights)
            # Prefer weights that work across multiple time windows, not only one regime.
            objective = (
                stability["avg_precision"],
                stability["min_precision"],
                train_metrics["avg_return_10d"],
            )
            if best is None or objective > best:
                best = objective
                best_weights = weights
                best_train_metrics = train_metrics
                best_stability = stability

        test_metrics = self._evaluate(test, best_weights)
        run_id = str(uuid.uuid4())
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO model_bottom_weight_runs (
                    run_id, universe_type, label_type, train_end_date,
                    test_start_date, candidate_count, selected_count,
                    train_precision, test_precision, test_avg_return_10d,
                    test_avg_max_gain_10d, test_avg_drawdown_10d,
                    weights_json, created_at_kst
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id, universe_type, label_type, train_end, test_start,
                    len(dataset), test_metrics["selected_count"],
                    best_train_metrics["precision"], test_metrics["precision"],
                    test_metrics["avg_return_10d"], test_metrics["avg_max_gain_10d"],
                    test_metrics["avg_drawdown_10d"],
                    json.dumps(best_weights, ensure_ascii=False),
                    self.created_at_kst,
                ),
            )

        return {
            "run_id": run_id,
            "candidate_count": len(dataset),
            "train_end_date": train_end,
            "test_start_date": test_start,
            "weights": best_weights,
            "train": best_train_metrics,
            "train_stability": best_stability,
            "test": test_metrics,
        }


def main():
    parser = argparse.ArgumentParser(description="Optimize bottom model feature weights.")
    parser.add_argument("--db-path", default="stock_data.db")
    parser.add_argument("--universe-type", default="market_cap_top_100")
    parser.add_argument("--label-type", default="bottom_10d_5pct_safe")
    args = parser.parse_args()

    optimizer = BottomWeightOptimizer(db_path=args.db_path)
    result = optimizer.optimize(universe_type=args.universe_type, label_type=args.label_type)
    print(f"[BottomWeightOptimizer] run_id={result['run_id']}")
    print(f"dataset={result['candidate_count']} train_end={result['train_end_date']} test_start={result['test_start_date']}")
    print("weights=" + json.dumps(result["weights"], ensure_ascii=False, sort_keys=True))
    print("train=" + json.dumps(result["train"], ensure_ascii=False, sort_keys=True))
    print("train_stability=" + json.dumps(result["train_stability"], ensure_ascii=False, sort_keys=True))
    print("test=" + json.dumps(result["test"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
