import argparse
import sqlite3


RULES = {
    "rsi_oversold_near_20d_low": """
        f.rsi_14 <= 35
        AND f.distance_from_20d_low <= 5
    """,
    "bb_lower_reclaim": """
        f.bb_position >= 0.05
        AND f.bb_position <= 0.35
        AND f.rsi_14 <= 45
        AND f.distance_from_20d_low <= 8
    """,
    "deep_pullback_volume": """
        f.return_20d <= -10
        AND f.volume_ratio_20 >= 1.2
        AND f.distance_from_60d_low <= 8
    """,
    "macd_turning_from_low": """
        f.macd_hist > -500
        AND f.rsi_14 BETWEEN 30 AND 50
        AND f.distance_from_60d_low <= 10
        AND f.return_20d <= 0
    """,
    "conservative_bottom": """
        f.rsi_14 BETWEEN 30 AND 45
        AND f.distance_from_20d_low <= 6
        AND f.volume_ratio_20 >= 0.8
        AND f.drawdown_20d <= -5
    """,
}


LABELS = (
    "bottom_5d_3pct_safe",
    "bottom_10d_5pct_safe",
    "bottom_20d_8pct",
)


class BottomBacktester:
    def __init__(self, db_path="stock_data.db"):
        self.db_path = db_path

    def evaluate_rule(self, rule_name, rule_sql, label_type, universe_type):
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                f"""
                SELECT
                    COUNT(*) AS signals,
                    SUM(l.is_success) AS wins,
                    AVG(l.future_return_5d) AS avg_return_5d,
                    AVG(l.future_return_10d) AS avg_return_10d,
                    AVG(l.future_return_20d) AS avg_return_20d,
                    AVG(l.future_max_gain_10d) AS avg_max_gain_10d,
                    AVG(l.future_max_drawdown_10d) AS avg_max_drawdown_10d,
                    MIN(l.future_max_drawdown_20d) AS worst_drawdown_20d
                FROM model_feature_daily f
                JOIN model_backtest_labels l
                  ON l.date = f.date
                 AND l.ticker = f.ticker
                 AND l.universe_type = f.universe_type
                WHERE f.universe_type = ?
                  AND l.label_type = ?
                  AND {rule_sql}
                """,
                (universe_type, label_type),
            ).fetchone()
        signals = row[0] or 0
        wins = row[1] or 0
        return {
            "rule": rule_name,
            "label_type": label_type,
            "signals": signals,
            "wins": wins,
            "win_rate": None if signals == 0 else wins * 100 / signals,
            "avg_return_5d": row[2],
            "avg_return_10d": row[3],
            "avg_return_20d": row[4],
            "avg_max_gain_10d": row[5],
            "avg_max_drawdown_10d": row[6],
            "worst_drawdown_20d": row[7],
        }

    def run(self, universe_type="market_cap_top_100", min_signals=30):
        results = []
        for rule_name, rule_sql in RULES.items():
            for label_type in LABELS:
                result = self.evaluate_rule(rule_name, rule_sql, label_type, universe_type)
                if result["signals"] >= min_signals:
                    results.append(result)
        return sorted(
            results,
            key=lambda item: (
                item["win_rate"] if item["win_rate"] is not None else -1,
                item["signals"],
            ),
            reverse=True,
        )


def _format(value):
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def main():
    parser = argparse.ArgumentParser(description="Evaluate first-pass bottom candidate rules.")
    parser.add_argument("--db-path", default="stock_data.db")
    parser.add_argument("--universe-type", default="market_cap_top_100")
    parser.add_argument("--min-signals", type=int, default=30)
    args = parser.parse_args()

    tester = BottomBacktester(db_path=args.db_path)
    results = tester.run(universe_type=args.universe_type, min_signals=args.min_signals)
    columns = [
        "rule", "label_type", "signals", "wins", "win_rate",
        "avg_return_5d", "avg_return_10d", "avg_return_20d",
        "avg_max_gain_10d", "avg_max_drawdown_10d", "worst_drawdown_20d",
    ]
    print("\t".join(columns))
    for row in results:
        print("\t".join(_format(row[column]) for column in columns))


if __name__ == "__main__":
    main()
