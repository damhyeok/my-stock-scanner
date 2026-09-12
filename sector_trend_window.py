import pandas as pd


def recent_sector_window(frame, selected_date, count=10):
    """Use observed trading dates, not calendar weeks (holidays included)."""
    eligible = frame[
        (frame["date"].astype(str) <= str(selected_date))
        & (frame["session"] == "정규장(16:00)")
        & (frame["category"] == "VOLUME_TOP_60")
    ].copy()
    eligible["date"] = eligible["date"].astype(str)
    dates = sorted(eligible["date"].unique())[-count:]
    return eligible[eligible["date"].isin(dates)].drop_duplicates(
        subset=["date", "session", "ticker"]
    ).copy(), dates


SUMMARY_COLUMNS = [
    "date", "trend_kind", "sector", "trading_value", "stock_count",
    "included_stocks", "trading_rank",
]


def build_sector_trend_summary(frame):
    """Materialize the exact daily inputs needed by both sector trend charts."""
    required = {
        "date", "session", "category", "ticker", "name", "sector",
        "trading_value", "fluctuation_rate",
    }
    if frame.empty or not required.issubset(frame.columns):
        return pd.DataFrame(columns=SUMMARY_COLUMNS)
    source = frame[
        (frame["session"] == "정규장(16:00)")
        & (frame["category"] == "VOLUME_TOP_60")
        & (frame["sector"] != "기타")
    ].drop_duplicates(subset=["date", "session", "ticker"]).copy()
    if source.empty:
        return pd.DataFrame(columns=SUMMARY_COLUMNS)
    source["date"] = source["date"].astype(str)
    source["trading_value"] = pd.to_numeric(
        source["trading_value"], errors="coerce"
    ).fillna(0)
    source["fluctuation_rate"] = pd.to_numeric(
        source["fluctuation_rate"], errors="coerce"
    ).fillna(0)

    def aggregate(rows, kind, label_column):
        if rows.empty:
            return pd.DataFrame(columns=SUMMARY_COLUMNS)
        result = (
            rows.groupby(["date", "sector"])
            .agg(
                trading_value=("trading_value", "sum"),
                stock_count=("ticker", "nunique"),
                included_stocks=(
                    label_column,
                    lambda labels: ", ".join(dict.fromkeys(labels.astype(str))),
                ),
            )
            .reset_index()
        )
        result["trading_rank"] = (
            result.groupby("date")["trading_value"]
            .rank(method="min", ascending=False)
            .astype(int)
        )
        result["trend_kind"] = kind
        return result[SUMMARY_COLUMNS]

    all_summary = aggregate(source, "ALL", "name")
    rising = source[source["fluctuation_rate"] > 0].copy()
    rising["stock_label"] = (
        rising["name"].astype(str)
        + rising["fluctuation_rate"].map(lambda rate: f" ({rate:+.2f}%)")
    )
    return pd.concat(
        [all_summary, aggregate(rising, "RISING", "stock_label")],
        ignore_index=True,
    )


def recent_sector_summary_window(summary, selected_date, count=10):
    if summary.empty:
        return summary.copy(), []
    eligible = summary[summary["date"].astype(str) <= str(selected_date)].copy()
    eligible["date"] = eligible["date"].astype(str)
    dates = sorted(eligible["date"].unique())[-count:]
    return eligible[eligible["date"].isin(dates)].copy(), dates
