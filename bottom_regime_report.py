import argparse
import sqlite3


def print_report(db_path="stock_data.db", label_type="bottom_10d_5pct_safe"):
    with sqlite3.connect(db_path) as conn:
        print("regime\trows\twins\twin_rate\tavg_return_10d\tavg_drawdown_10d")
        for row in conn.execute(
            """
            SELECT
                r.regime,
                COUNT(*) AS rows,
                SUM(l.is_success) AS wins,
                ROUND(SUM(l.is_success) * 100.0 / COUNT(*), 2) AS win_rate,
                ROUND(AVG(l.future_return_10d), 2) AS avg_return_10d,
                ROUND(AVG(l.future_max_drawdown_10d), 2) AS avg_drawdown_10d
            FROM model_feature_daily f
            JOIN model_backtest_labels l
              ON l.date = f.date
             AND l.ticker = f.ticker
             AND l.universe_type = f.universe_type
            JOIN model_market_regimes r
              ON r.date = f.date
             AND r.universe_type = f.universe_type
            WHERE l.label_type = ?
              AND f.rsi_14 IS NOT NULL
              AND f.ma_120 IS NOT NULL
              AND l.future_return_10d IS NOT NULL
            GROUP BY r.regime
            ORDER BY win_rate DESC
            """,
            (label_type,),
        ):
            print("\t".join(str(item) for item in row))


def main():
    parser = argparse.ArgumentParser(description="Show bottom label performance by market regime.")
    parser.add_argument("--db-path", default="stock_data.db")
    parser.add_argument("--label-type", default="bottom_10d_5pct_safe")
    args = parser.parse_args()
    print_report(db_path=args.db_path, label_type=args.label_type)


if __name__ == "__main__":
    main()
