"""Date-fixed signal creation and history storage for the bottom scanners."""
import sqlite3
from datetime import datetime, timezone, timedelta

import pandas as pd

from fib_channel_backtester import FibChannelBacktester
from model_schema import init_model_tables

KST = timezone(timedelta(hours=9))


def scan_model_tables(db_path, universe_type="market_cap_10000eok_plus", signal_date=None):
    engine = FibChannelBacktester(db_path, universe_type)
    prices = engine.load_prices()
    candidates = engine.candidates(prices, strategy="market_trend_score", min_trend_score=2)
    if candidates.empty:
        return pd.DataFrame(), pd.DataFrame(), None
    latest_date = prices.date.max()
    target_date = pd.to_datetime(signal_date) if signal_date else latest_date
    candidates = candidates[candidates.date == target_date].copy()
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


def save_model_scan_history(db_path, universe_type="market_cap_10000eok_plus", signal_date=None):
    """Persist signals finalized with that day's closing price; never overwrite other dates."""
    init_model_tables(db_path)
    model_1, macd_obv, latest_date = scan_model_tables(db_path, universe_type, signal_date)
    target_date = (pd.to_datetime(signal_date) if signal_date else latest_date).strftime("%Y%m%d")
    created_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for model_id, signals in (("model_1", model_1), ("macd_obv", macd_obv)):
        for row in signals.itertuples(index=False):
            rows.append((target_date, model_id, row.ticker, row.name, universe_type, row.close,
                         row.change_rate, row.market_cap, row.trend_score, row.rsi_14,
                         row.volume_ratio, row.close, row.stop_price, row.fib_618,
                         row.target_room_pct, row.signal_reason, created_at))
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM model_rule_scan_signals WHERE signal_date=? AND universe_type=?",
                     (target_date, universe_type))
        conn.executemany(
            """INSERT OR REPLACE INTO model_rule_scan_signals
            (signal_date,model_id,ticker,name,universe_type,current_price,change_rate,market_cap,
             trend_score,rsi_14,volume_ratio,entry_price,stop_price,first_target_price,
             target_room_pct,signal_reason,created_at_kst)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    return len(rows)
