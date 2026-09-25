"""Compact, date-fixed sector breadth history for the rise-rank tab."""

import pandas as pd


REGULAR_SESSION = "정규장(16:00)"
KINDS = ("rise", "overlap")


def _empty_day(reason):
    return {"leaders": [], "other": None, "reason": reason}


def _rank_rows(rows):
    rows = rows.copy()
    for column, default in (
        ("name", ""), ("sector", "기타"),
        ("fluctuation_rate", 0), ("trading_value", 0),
    ):
        if column not in rows:
            rows[column] = default
    rows["ticker"] = rows["ticker"].astype(str)
    rows["sector"] = rows["sector"].fillna("기타").astype(str).str.strip()
    rows.loc[rows["sector"] == "", "sector"] = "기타"
    for column in ("fluctuation_rate", "trading_value"):
        rows[column] = pd.to_numeric(rows[column], errors="coerce").fillna(0)
    return rows.sort_values(
        ["fluctuation_rate", "trading_value"], ascending=[False, False]
    ).drop_duplicates("ticker").head(60)


def _sector_groups(rows, keep_other):
    groups = []
    for sector, members in rows.groupby("sector", sort=False):
        stocks = [
            {
                "ticker": str(row.ticker),
                "name": str(row.name),
                "rate": float(row.fluctuation_rate),
                "trading_value": float(row.trading_value),
            }
            for row in members.sort_values("fluctuation_rate", ascending=False).itertuples()
        ]
        groups.append({
            "sector": sector,
            "count": len(stocks),
            "trading_value": sum(stock["trading_value"] for stock in stocks),
            "stocks": stocks,
        })
    groups.sort(key=lambda group: (-group["count"], -group["trading_value"], group["sector"]))
    other = next((group for group in groups if group["sector"] == "기타"), None)
    leaders = [group for group in groups if group["sector"] != "기타"][:3]
    return leaders, other if keep_other else None


def _appearance_label(key, previous, streaks, position, complete_dates, noun):
    last = previous.get(key)
    if last is None:
        streaks[key] = 1
        return "기록 내 첫 등장"
    gap = position - last
    if gap == 1:
        streaks[key] = streaks.get(key, 1) + 1
        return f"{streaks[key]}거래일 연속"
    streaks[key] = 1
    if not all(index in complete_dates for index in range(last + 1, position)):
        return f"중간 기록 부족 · {noun} 불확실"
    return f"{gap}거래일 만에 {noun}"


def build_rise_sector_history(raw_data, trading_dates, as_of_date, window=10):
    """Rank up to three sectors per actual trading day, never using holiday reruns.

    `trading_dates` comes from real index/daily OHLCV observations, not from
    `daily_stocks`: rank APIs can return stale rows even on exchange holidays.
    """
    empty = {"dates": [], "days": {}, "as_of": str(as_of_date)}
    if raw_data is None or raw_data.empty or not trading_dates:
        return empty
    required = {"date", "session", "category", "ticker"}
    if not required.issubset(raw_data.columns):
        return empty

    frame = raw_data[
        (raw_data["session"].astype(str) == REGULAR_SESSION)
        & (raw_data["date"].astype(str) <= str(as_of_date))
        & (raw_data["category"].isin(("RISE_TOP_60", "RISE_TOP_30", "VOLUME_TOP_60")))
    ].copy()
    if frame.empty:
        return empty
    frame["date"] = frame["date"].astype(str)
    dates = sorted(set(str(date) for date in trading_dates) & set(frame["date"]))
    if not dates:
        return empty

    days = {}
    complete_dates = {kind: set() for kind in KINDS}
    last_sector = {kind: {} for kind in KINDS}
    sector_streak = {kind: {} for kind in KINDS}
    last_stock = {kind: {} for kind in KINDS}
    stock_streak = {kind: {} for kind in KINDS}

    for position, date in enumerate(dates):
        session = frame[frame["date"] == date]
        rise = _rank_rows(session[session["category"] == "RISE_TOP_60"])
        volume = session[session["category"] == "VOLUME_TOP_60"].copy()
        if "trading_value" not in volume:
            volume["trading_value"] = 0
        volume["trading_value"] = pd.to_numeric(
            volume["trading_value"], errors="coerce"
        ).fillna(0)
        volume = volume.sort_values("trading_value", ascending=False).drop_duplicates("ticker").head(60)
        if rise.empty and (session["category"] == "RISE_TOP_30").any():
            rise_reason = "당시 TOP30만 수집"
        elif len(rise) < 60:
            rise_reason = f"상승률 {len(rise)}/60 · 비교 제외"
        else:
            rise_reason = ""
        days[date] = {
            "rise": _empty_day(rise_reason),
            "overlap": _empty_day(
                rise_reason or (f"거래대금 {len(volume)}/60 · 비교 제외" if len(volume) < 60 else "")
            ),
        }
        if not rise_reason:
            leaders, _ = _sector_groups(rise, keep_other=False)
            days[date]["rise"]["leaders"] = leaders
            complete_dates["rise"].add(position)
        if not days[date]["overlap"]["reason"]:
            intersection = rise[rise["ticker"].isin(set(volume["ticker"].astype(str)))]
            leaders, other = _sector_groups(intersection, keep_other=True)
            days[date]["overlap"].update({"leaders": leaders, "other": other})
            complete_dates["overlap"].add(position)

        for kind in KINDS:
            day = days[date][kind]
            if day["reason"]:
                continue
            for group in day["leaders"]:
                sector = group["sector"]
                group["tracking"] = _appearance_label(
                    sector, last_sector[kind], sector_streak[kind],
                    position, complete_dates[kind], "재진입",
                )
                last_sector[kind][sector] = position
            all_groups = day["leaders"] + ([day["other"]] if day["other"] else [])
            # Stock appearances are based on all ranked rows, not just top-three sectors.
            rows = rise if kind == "rise" else intersection
            stock_labels = {}
            for ticker in rows["ticker"].astype(str):
                stock_labels[ticker] = _appearance_label(
                    ticker, last_stock[kind], stock_streak[kind],
                    position, complete_dates[kind], "재등장",
                )
                last_stock[kind][ticker] = position
            for group in all_groups:
                for stock in group["stocks"]:
                    stock["tracking"] = stock_labels[stock["ticker"]]

    return {"dates": dates[-max(int(window), 1):], "days": days, "as_of": str(as_of_date)}
