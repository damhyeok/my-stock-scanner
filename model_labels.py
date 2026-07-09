import argparse
import sqlite3
from datetime import datetime, timedelta, timezone

from model_schema import init_model_tables


KST = timezone(timedelta(hours=9))


LABEL_RULES = {
    "bottom_5d_3pct_safe": {"days": 5, "gain": 3.0, "max_drawdown": -4.0},
    "bottom_10d_5pct_safe": {"days": 10, "gain": 5.0, "max_drawdown": -5.0},
    "bottom_20d_8pct": {"days": 20, "gain": 8.0, "max_drawdown": -8.0},
    "surge_5d_10pct": {"days": 5, "gain": 10.0, "max_drawdown": None},
    "surge_20d_20pct": {"days": 20, "gain": 20.0, "max_drawdown": None},
}


def _future_metrics(rows, index, horizon):
    current_close = rows[index]["close"]
    future = rows[index + 1:index + horizon + 1]
    if not future or current_close in (None, 0):
        return None
    closes = [row["close"] for row in future if row["close"] is not None]
    highs = [row["high"] for row in future if row["high"] is not None]
    lows = [row["low"] for row in future if row["low"] is not None]
    if not closes or not highs or not lows:
        return None
    return {
        "return": (closes[-1] / current_close - 1) * 100,
        "max_gain": (max(highs) / current_close - 1) * 100,
        "max_drawdown": (min(lows) / current_close - 1) * 100,
    }


class ModelLabelBuilder:
    def __init__(self, db_path="stock_data.db"):
        self.db_path = db_path
        init_model_tables(db_path=db_path)
        self.calculated_at_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

    def _load_tickers(self, universe_type):
        with sqlite3.connect(self.db_path) as conn:
            return [
                row[0]
                for row in conn.execute(
                    """
                    SELECT DISTINCT ticker
                    FROM model_ohlcv_daily
                    WHERE universe_type = ?
                    ORDER BY ticker
                    """,
                    (universe_type,),
                ).fetchall()
            ]

    def _load_ohlcv(self, ticker, universe_type):
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT date, high, low, close
                FROM model_ohlcv_daily
                WHERE ticker = ? AND universe_type = ?
                ORDER BY date
                """,
                (ticker, universe_type),
            ).fetchall()
        return [
            {"date": row[0], "high": row[1], "low": row[2], "close": row[3]}
            for row in rows
        ]

    def _build_rows(self, ticker, universe_type, rows):
        label_rows = []
        for index, row in enumerate(rows):
            metrics_5 = _future_metrics(rows, index, 5)
            metrics_10 = _future_metrics(rows, index, 10)
            metrics_20 = _future_metrics(rows, index, 20)
            if metrics_5 is None and metrics_10 is None and metrics_20 is None:
                continue

            for label_type, rule in LABEL_RULES.items():
                horizon_metrics = {
                    5: metrics_5,
                    10: metrics_10,
                    20: metrics_20,
                }[rule["days"]]
                if horizon_metrics is None:
                    continue
                passed_gain = horizon_metrics["max_gain"] >= rule["gain"]
                passed_risk = (
                    rule["max_drawdown"] is None
                    or horizon_metrics["max_drawdown"] >= rule["max_drawdown"]
                )
                label_rows.append({
                    "date": row["date"],
                    "ticker": ticker,
                    "universe_type": universe_type,
                    "label_type": label_type,
                    "future_return_5d": None if metrics_5 is None else metrics_5["return"],
                    "future_return_10d": None if metrics_10 is None else metrics_10["return"],
                    "future_return_20d": None if metrics_20 is None else metrics_20["return"],
                    "future_max_gain_10d": None if metrics_10 is None else metrics_10["max_gain"],
                    "future_max_gain_20d": None if metrics_20 is None else metrics_20["max_gain"],
                    "future_max_drawdown_10d": None if metrics_10 is None else metrics_10["max_drawdown"],
                    "future_max_drawdown_20d": None if metrics_20 is None else metrics_20["max_drawdown"],
                    "market_relative_return_10d": None,
                    "is_success": 1 if passed_gain and passed_risk else 0,
                })
        return label_rows

    def _save_rows(self, label_rows):
        with sqlite3.connect(self.db_path) as conn:
            for row in label_rows:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO model_backtest_labels (
                        date, ticker, universe_type, label_type, future_return_5d,
                        future_return_10d, future_return_20d, future_max_gain_10d,
                        future_max_gain_20d, future_max_drawdown_10d,
                        future_max_drawdown_20d, market_relative_return_10d,
                        is_success, calculated_at_kst
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["date"], row["ticker"], row["universe_type"],
                        row["label_type"], row["future_return_5d"],
                        row["future_return_10d"], row["future_return_20d"],
                        row["future_max_gain_10d"], row["future_max_gain_20d"],
                        row["future_max_drawdown_10d"], row["future_max_drawdown_20d"],
                        row["market_relative_return_10d"], row["is_success"],
                        self.calculated_at_kst,
                    ),
                )

    def run(self, universe_type="market_cap_top_100"):
        tickers = self._load_tickers(universe_type)
        total = 0
        for ticker in tickers:
            rows = self._load_ohlcv(ticker, universe_type)
            label_rows = self._build_rows(ticker, universe_type, rows)
            self._save_rows(label_rows)
            total += len(label_rows)
            print(f"[ModelLabels] {ticker}: {len(label_rows)} rows")
        return {"tickers": len(tickers), "label_rows": total}


def main():
    parser = argparse.ArgumentParser(description="Build model-only future outcome labels.")
    parser.add_argument("--db-path", default="stock_data.db")
    parser.add_argument("--universe-type", default="market_cap_top_100")
    args = parser.parse_args()

    builder = ModelLabelBuilder(db_path=args.db_path)
    summary = builder.run(universe_type=args.universe_type)
    print(
        "[ModelLabels] done: "
        f"tickers={summary['tickers']}, label_rows={summary['label_rows']}"
    )


if __name__ == "__main__":
    main()
