import argparse
import sqlite3
from datetime import datetime, timedelta, timezone

from model_schema import init_model_tables


KST = timezone(timedelta(hours=9))


class MarketRegimeBuilder:
    def __init__(self, db_path="stock_data.db"):
        self.db_path = db_path
        init_model_tables(db_path=db_path)
        self.calculated_at_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def classify(avg_return_20d, rising_ratio_20d, avg_drawdown_20d):
        if avg_return_20d is None or rising_ratio_20d is None:
            return "unknown"
        if avg_return_20d >= 5 and rising_ratio_20d >= 60:
            return "strong_uptrend"
        if avg_return_20d >= 1 and rising_ratio_20d >= 50:
            return "uptrend"
        if avg_return_20d <= -5 and rising_ratio_20d <= 35:
            return "strong_downtrend"
        if avg_return_20d <= -1 and rising_ratio_20d <= 45:
            return "downtrend"
        if avg_drawdown_20d is not None and avg_drawdown_20d <= -10:
            return "high_risk_pullback"
        return "sideways"

    def run(self, universe_type="market_cap_10000eok_plus"):
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT
                    date,
                    AVG(return_20d) AS avg_return_20d,
                    AVG(CASE WHEN return_20d > 0 THEN 1.0 ELSE 0.0 END) * 100 AS rising_ratio_20d,
                    AVG(drawdown_20d) AS avg_drawdown_20d
                FROM model_feature_daily
                WHERE universe_type = ?
                  AND return_20d IS NOT NULL
                  AND drawdown_20d IS NOT NULL
                GROUP BY date
                ORDER BY date
                """,
                (universe_type,),
            ).fetchall()
            for date, avg_ret, rising_ratio, avg_dd in rows:
                regime = self.classify(avg_ret, rising_ratio, avg_dd)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO model_market_regimes (
                        date, universe_type, avg_return_20d, rising_ratio_20d,
                        avg_drawdown_20d, regime, calculated_at_kst
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (date, universe_type, avg_ret, rising_ratio, avg_dd, regime, self.calculated_at_kst),
                )
        return len(rows)


def main():
    parser = argparse.ArgumentParser(description="Build market regimes from model universe breadth.")
    parser.add_argument("--db-path", default="stock_data.db")
    parser.add_argument("--universe-type", default="market_cap_10000eok_plus")
    args = parser.parse_args()

    count = MarketRegimeBuilder(db_path=args.db_path).run(args.universe_type)
    print(f"[MarketRegime] rows={count}")


if __name__ == "__main__":
    main()
