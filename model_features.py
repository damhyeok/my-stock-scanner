import argparse
import sqlite3
from datetime import datetime, timedelta, timezone

from model_schema import init_model_tables


KST = timezone(timedelta(hours=9))


def _safe_div(numerator, denominator):
    if denominator in (None, 0):
        return None
    if numerator is None:
        return None
    return numerator / denominator


def _rolling_mean(values, window, index):
    start = index - window + 1
    if start < 0:
        return None
    window_values = values[start:index + 1]
    if any(value is None for value in window_values):
        return None
    return sum(window_values) / window


def _rolling_min(values, window, index):
    start = index - window + 1
    if start < 0:
        return None
    window_values = [value for value in values[start:index + 1] if value is not None]
    return min(window_values) if len(window_values) == window else None


def _rolling_max(values, window, index):
    start = index - window + 1
    if start < 0:
        return None
    window_values = [value for value in values[start:index + 1] if value is not None]
    return max(window_values) if len(window_values) == window else None


def _rolling_std(values, window, index):
    mean = _rolling_mean(values, window, index)
    if mean is None:
        return None
    start = index - window + 1
    variance = sum((value - mean) ** 2 for value in values[start:index + 1]) / window
    return variance ** 0.5


def _ema(values, span):
    alpha = 2 / (span + 1)
    result = []
    current = None
    for value in values:
        if value is None:
            result.append(current)
            continue
        current = value if current is None else (value * alpha) + (current * (1 - alpha))
        result.append(current)
    return result


def _rsi(closes, period=14):
    result = [None] * len(closes)
    gains = []
    losses = []
    for index in range(1, len(closes)):
        change = closes[index] - closes[index - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
        if index < period:
            continue
        window_gains = gains[index - period:index]
        window_losses = losses[index - period:index]
        avg_gain = sum(window_gains) / period
        avg_loss = sum(window_losses) / period
        if avg_loss == 0:
            result[index] = 100.0
        else:
            rs = avg_gain / avg_loss
            result[index] = 100 - (100 / (1 + rs))
    return result


def _obv(closes, volumes):
    result = [0]
    for index in range(1, len(closes)):
        if closes[index] > closes[index - 1]:
            result.append(result[-1] + volumes[index])
        elif closes[index] < closes[index - 1]:
            result.append(result[-1] - volumes[index])
        else:
            result.append(result[-1])
    return result


def _pct_return(closes, days, index):
    previous = index - days
    if previous < 0 or closes[previous] in (None, 0):
        return None
    return (closes[index] / closes[previous] - 1) * 100


def _drawdown_from_high(closes, window, index):
    high = _rolling_max(closes, window, index)
    if high in (None, 0):
        return None
    return (closes[index] / high - 1) * 100


def _distance_from_low(closes, window, index):
    low = _rolling_min(closes, window, index)
    if low in (None, 0):
        return None
    return (closes[index] / low - 1) * 100


def _distance_from_high(closes, window, index):
    high = _rolling_max(closes, window, index)
    if high in (None, 0):
        return None
    return (closes[index] / high - 1) * 100


class ModelFeatureBuilder:
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
                SELECT date, open, high, low, close, volume
                FROM model_ohlcv_daily
                WHERE ticker = ? AND universe_type = ?
                ORDER BY date
                """,
                (ticker, universe_type),
            ).fetchall()
        return [
            {
                "date": row[0],
                "open": row[1],
                "high": row[2],
                "low": row[3],
                "close": row[4],
                "volume": row[5],
            }
            for row in rows
        ]

    def _calculate_rows(self, ticker, universe_type, rows):
        closes = [row["close"] for row in rows]
        volumes = [row["volume"] for row in rows]
        rsi_14 = _rsi(closes, period=14)
        ema_12 = _ema(closes, span=12)
        ema_26 = _ema(closes, span=26)
        macd = [
            None if fast is None or slow is None else fast - slow
            for fast, slow in zip(ema_12, ema_26)
        ]
        macd_signal = _ema(macd, span=9)
        obv = _obv(closes, volumes)

        feature_rows = []
        for index, row in enumerate(rows):
            ma_20 = _rolling_mean(closes, 20, index)
            std_20 = _rolling_std(closes, 20, index)
            bb_upper = None if ma_20 is None or std_20 is None else ma_20 + (2 * std_20)
            bb_lower = None if ma_20 is None or std_20 is None else ma_20 - (2 * std_20)
            bb_position = None
            if bb_upper is not None and bb_lower is not None and bb_upper != bb_lower:
                bb_position = (row["close"] - bb_lower) / (bb_upper - bb_lower)

            volume_ma_20 = _rolling_mean(volumes, 20, index)
            obv_ma_20 = _rolling_mean(obv, 20, index)

            feature_rows.append({
                "date": row["date"],
                "ticker": ticker,
                "universe_type": universe_type,
                "rsi_14": rsi_14[index],
                "ma_5": _rolling_mean(closes, 5, index),
                "ma_20": ma_20,
                "ma_60": _rolling_mean(closes, 60, index),
                "ma_120": _rolling_mean(closes, 120, index),
                "macd": macd[index],
                "macd_signal": macd_signal[index],
                "macd_hist": None if macd[index] is None or macd_signal[index] is None else macd[index] - macd_signal[index],
                "bb_upper": bb_upper,
                "bb_mid": ma_20,
                "bb_lower": bb_lower,
                "bb_position": bb_position,
                "obv": obv[index],
                "obv_ma_20": obv_ma_20,
                "volume_ratio_20": None if volume_ma_20 in (None, 0) else row["volume"] / volume_ma_20,
                "return_5d": _pct_return(closes, 5, index),
                "return_20d": _pct_return(closes, 20, index),
                "drawdown_20d": _drawdown_from_high(closes, 20, index),
                "distance_from_20d_low": _distance_from_low(closes, 20, index),
                "distance_from_60d_low": _distance_from_low(closes, 60, index),
                "distance_from_52w_high": _distance_from_high(closes, 240, index),
            })
        return feature_rows

    def _save_rows(self, feature_rows):
        with sqlite3.connect(self.db_path) as conn:
            for row in feature_rows:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO model_feature_daily (
                        date, ticker, universe_type, rsi_14, ma_5, ma_20, ma_60,
                        ma_120, macd, macd_signal, macd_hist, bb_upper, bb_mid,
                        bb_lower, bb_position, obv, obv_ma_20, volume_ratio_20,
                        return_5d, return_20d, drawdown_20d, distance_from_20d_low,
                        distance_from_60d_low, distance_from_52w_high,
                        calculated_at_kst
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["date"], row["ticker"], row["universe_type"],
                        row["rsi_14"], row["ma_5"], row["ma_20"], row["ma_60"],
                        row["ma_120"], row["macd"], row["macd_signal"], row["macd_hist"],
                        row["bb_upper"], row["bb_mid"], row["bb_lower"], row["bb_position"],
                        row["obv"], row["obv_ma_20"], row["volume_ratio_20"],
                        row["return_5d"], row["return_20d"], row["drawdown_20d"],
                        row["distance_from_20d_low"], row["distance_from_60d_low"],
                        row["distance_from_52w_high"], self.calculated_at_kst,
                    ),
                )

    def run(self, universe_type="market_cap_top_100"):
        tickers = self._load_tickers(universe_type)
        total = 0
        for ticker in tickers:
            rows = self._load_ohlcv(ticker, universe_type)
            feature_rows = self._calculate_rows(ticker, universe_type, rows)
            self._save_rows(feature_rows)
            total += len(feature_rows)
            print(f"[ModelFeatures] {ticker}: {len(feature_rows)} rows")
        return {"tickers": len(tickers), "feature_rows": total}


def main():
    parser = argparse.ArgumentParser(description="Build model-only technical features.")
    parser.add_argument("--db-path", default="stock_data.db")
    parser.add_argument("--universe-type", default="market_cap_top_100")
    args = parser.parse_args()

    builder = ModelFeatureBuilder(db_path=args.db_path)
    summary = builder.run(universe_type=args.universe_type)
    print(
        "[ModelFeatures] done: "
        f"tickers={summary['tickers']}, feature_rows={summary['feature_rows']}"
    )


if __name__ == "__main__":
    main()
