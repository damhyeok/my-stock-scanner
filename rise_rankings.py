import pandas as pd


RANK_TABLE_COLUMNS = [
    "name",
    "fluctuation_rate",
    "rise_rank",
    "trading_rank",
    "market_cap",
    "trading_value",
    "sector",
]


def build_rise_rank_tables(session_data):
    """Return rise TOP30 and its intersection with trading-value TOP60."""
    empty = pd.DataFrame(columns=RANK_TABLE_COLUMNS)
    if session_data is None or session_data.empty or "category" not in session_data.columns:
        return empty.copy(), empty.copy()

    rise = session_data[session_data["category"] == "RISE_TOP_30"].copy()
    volume = session_data[session_data["category"] == "VOLUME_TOP_60"].copy()
    if rise.empty:
        return empty.copy(), empty.copy()

    for frame in (rise, volume):
        for column in ("fluctuation_rate", "market_cap", "trading_value"):
            if column not in frame.columns:
                frame[column] = 0
            frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0)

    rise = (
        rise.sort_values(["fluctuation_rate", "trading_value"], ascending=[False, False])
        .drop_duplicates("ticker", keep="first")
        .head(30)
        .reset_index(drop=True)
    )
    rise["rise_rank"] = rise.index + 1

    volume = (
        volume.sort_values("trading_value", ascending=False)
        .drop_duplicates("ticker", keep="first")
        .head(60)
        .reset_index(drop=True)
    )
    trading_rank = {ticker: rank + 1 for rank, ticker in enumerate(volume["ticker"])}
    rise["trading_rank"] = rise["ticker"].map(trading_rank).astype("Int64")

    for column, default in (("name", ""), ("sector", "기타")):
        if column not in rise.columns:
            rise[column] = default
        rise[column] = rise[column].fillna(default)

    top30 = rise[RANK_TABLE_COLUMNS].copy()
    overlap = top30[top30["trading_rank"].notna()].copy().reset_index(drop=True)
    return top30, overlap
