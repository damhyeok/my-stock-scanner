"""Live signal tables for the independent model_1 and MACD+OBV scanners."""
import sqlite3

import pandas as pd

from fib_channel_backtester import FibChannelBacktester


def scan_model_tables(db_path, universe_type="market_cap_10000eok_plus"):
    engine = FibChannelBacktester(db_path, universe_type)
    prices = engine.load_prices()
    candidates = engine.candidates(prices, strategy="market_trend_score", min_trend_score=2)
    if candidates.empty:
        return pd.DataFrame(), pd.DataFrame(), None
    latest_date = prices.date.max()
    candidates = candidates[candidates.date == latest_date].copy()
    if candidates.empty:
        return pd.DataFrame(), pd.DataFrame(), latest_date
    with sqlite3.connect(db_path) as conn:
        features = pd.read_sql_query(
            """SELECT date, ticker, rsi_14, macd_hist, obv, obv_ma_20
               FROM model_feature_daily WHERE universe_type = ?""",
            conn, params=(universe_type,),
        )
    features.date = pd.to_datetime(features.date)
    features = features.sort_values(["ticker", "date"])
    features["previous_macd_hist"] = features.groupby("ticker").macd_hist.shift(1)
    signals = candidates.merge(features, on=["date", "ticker"], how="left", suffixes=("", "_feature"))
    signals["target_room_pct"] = (signals.fib_618 / signals.close - 1) * 100
    signals["stop_price"] = signals.close * .97
    signals["macd_obv_ok"] = (
        (signals.macd_hist > signals.previous_macd_hist)
        & (signals.obv >= signals.obv_ma_20)
    )
    signals["signal_reason"] = signals.apply(
        lambda r: f"추세점수 {int(r.trend_score)}/4 · RSI {r.rsi_14:.1f} · 목표여유 {r.target_room_pct:.1f}%", axis=1
    )
    columns = [
        "date", "ticker", "name", "close", "change_rate", "market_cap", "trend_score",
        "rsi_14", "volume_ratio", "target_room_pct", "fib_618", "stop_price", "signal_reason",
    ]
    model_1 = signals[columns].sort_values(["trend_score", "target_room_pct"], ascending=False)
    macd_obv = signals[signals.macd_obv_ok][columns].sort_values(
        ["trend_score", "target_room_pct"], ascending=False
    )
    return model_1.reset_index(drop=True), macd_obv.reset_index(drop=True), latest_date
