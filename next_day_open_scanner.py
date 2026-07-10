"""Live scanner for the validated next-day opening-gap experimental rule."""
import sqlite3

import pandas as pd


def scan_next_day_open_candidates(db_path, universe_type="market_cap_10000eok_plus"):
    with sqlite3.connect(db_path) as conn:
        prices = pd.read_sql_query(
            """SELECT date,ticker,name,close,change_rate,market_cap
               FROM model_ohlcv_daily WHERE universe_type=?""", conn, params=(universe_type,)
        )
        features = pd.read_sql_query(
            """SELECT date,ticker,rsi_14,ma_20,obv,obv_ma_20,volume_ratio_20
               FROM model_feature_daily WHERE universe_type=?""", conn, params=(universe_type,)
        )
    if prices.empty or features.empty:
        return pd.DataFrame()
    prices.date = pd.to_datetime(prices.date); features.date = pd.to_datetime(features.date)
    features = features.sort_values(["ticker", "date"])
    features["ma20_slope"] = features.groupby("ticker").ma_20.pct_change(5, fill_method=None)
    data = prices.merge(features, on=["date", "ticker"], how="inner")
    data["above_ma20"] = data.close > data.ma_20
    market = data.groupby("date").agg(breadth=("above_ma20", "mean"), slope=("ma20_slope", "mean"))
    latest_date = data.date.max()
    latest = data[data.date == latest_date].copy()
    if latest.empty or latest_date not in market.index:
        return pd.DataFrame()
    state = market.loc[latest_date]
    if not (state.breadth >= .55 and state.slope > 0):
        return pd.DataFrame()
    signals = latest[
        (latest.close > latest.ma_20)
        & latest.rsi_14.between(55, 75)
        & (latest.obv >= latest.obv_ma_20)
    ].copy()
    signals["model"] = "다음날 시가 모델"
    signals["signal_reason"] = signals.apply(
        lambda r: f"시장 상승 · 종가>MA20 · RSI {r.rsi_14:.1f} · OBV 지지", axis=1
    )
    return signals.sort_values(["change_rate", "market_cap"], ascending=[False, False]).reset_index(drop=True)
