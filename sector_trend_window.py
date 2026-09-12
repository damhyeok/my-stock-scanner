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
