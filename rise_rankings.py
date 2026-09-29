import pandas as pd


RISE_LARGE_CAP_CATEGORY = "RISE_TOP_60_300B"
RISE_MARKET_CAP_MIN = 300_000_000_000


RANK_TABLE_COLUMNS = [
    "name",
    "fluctuation_rate",
    "previous_day_rate",
    "rise_rank",
    "trading_rank",
    "market_cap",
    "trading_value",
    "sector",
]


def build_rise_rank_tables(session_data):
    """Return the large-cap rise TOP60 and its trading-value intersection."""
    empty = pd.DataFrame(columns=RANK_TABLE_COLUMNS)
    if session_data is None or session_data.empty or "category" not in session_data.columns:
        return empty.copy(), empty.copy()

    rise = session_data[session_data["category"] == RISE_LARGE_CAP_CATEGORY].copy()
    volume = session_data[session_data["category"] == "VOLUME_TOP_60"].copy()
    if rise.empty:
        return empty.copy(), empty.copy()

    for frame in (rise, volume):
        for column in ("fluctuation_rate", "previous_day_rate", "market_cap", "trading_value"):
            if column not in frame.columns:
                frame[column] = pd.NA if column == "previous_day_rate" else 0
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
            if column != "previous_day_rate":
                frame[column] = frame[column].fillna(0)

    # Defense in depth: only rows verified by the collector belong in this view.
    rise = rise[rise["market_cap"] >= RISE_MARKET_CAP_MIN]
    rise = (
        rise.sort_values(["fluctuation_rate", "trading_value"], ascending=[False, False])
        .drop_duplicates("ticker", keep="first")
        .head(60)
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

    top60 = rise[RANK_TABLE_COLUMNS].copy()
    overlap = top60[top60["trading_rank"].notna()].copy().reset_index(drop=True)
    return top60, overlap
