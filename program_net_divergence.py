"""Detect rising program net-buy with falling stock return across scan snapshots."""

from __future__ import annotations

import re

import pandas as pd


def _session_minutes(session) -> int:
    match = re.search(r"\((\d{1,2}):(\d{2})\)", str(session))
    if not match:
        return -1
    hour, minute = map(int, match.groups())
    return hour * 60 + minute


def _flow_text(values, formatter) -> str:
    return " → ".join(formatter(value) for value in values)


def build_program_price_divergence(
    snapshots: pd.DataFrame,
    runs: pd.DataFrame,
    selected_date: str,
    selected_session: str,
    *,
    window_size: int = 3,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return candidates and their recent history for consecutive completed scans.

    A candidate must exist in every selected scan, finish with positive program
    net buying, and show program net buying increasing while its day return falls
    at every interval in the comparison window.
    """

    empty_summary = pd.DataFrame()
    empty_history = pd.DataFrame()
    if snapshots.empty or runs.empty:
        return empty_summary, empty_history

    selected_minutes = _session_minutes(selected_session)
    day_runs = runs[runs["trade_date"].astype(str) == str(selected_date)].copy()
    if day_runs.empty:
        return empty_summary, empty_history
    day_runs["session_minutes"] = day_runs["session"].map(_session_minutes)
    day_runs = day_runs[
        (day_runs["session_minutes"] >= 0)
        & (day_runs["session_minutes"] <= selected_minutes)
        & (day_runs["status"].astype(str).isin(["success", "partial"]))
    ].sort_values("session_minutes")
    sessions = day_runs.drop_duplicates("session", keep="last")["session"].tolist()
    sessions = sessions[-max(2, int(window_size)):]
    if len(sessions) < 2 or sessions[-1] != selected_session:
        return empty_summary, empty_history

    session_order = {session: order for order, session in enumerate(sessions)}
    history = snapshots[
        (snapshots["trade_date"].astype(str) == str(selected_date))
        & (snapshots["session"].isin(sessions))
    ].copy()
    if history.empty:
        return empty_summary, empty_history
    history["session_order"] = history["session"].map(session_order)
    history = history.dropna(subset=["session_order"])
    for column in [
        "current_price", "fluctuation_rate", "program_net_buy", "trading_value",
    ]:
        history[column] = pd.to_numeric(history[column], errors="coerce")
    history = (
        history.sort_values(["ticker", "session_order", "collected_at_kst"])
        .drop_duplicates(["ticker", "session"], keep="last")
    )

    candidates = []
    candidate_tickers = []
    for ticker, rows in history.groupby("ticker", sort=False):
        rows = rows.sort_values("session_order")
        if rows["session"].tolist() != sessions:
            continue
        program_values = rows["program_net_buy"].tolist()
        return_values = rows["fluctuation_rate"].tolist()
        if any(pd.isna(value) for value in program_values + return_values):
            continue
        if program_values[-1] <= 0:
            continue
        if not all(current > previous for previous, current in zip(program_values, program_values[1:])):
            continue
        if not all(current < previous for previous, current in zip(return_values, return_values[1:])):
            continue

        latest = rows.iloc[-1]
        candidate_tickers.append(str(ticker))
        candidates.append(
            {
                "ticker": str(ticker),
                "name": str(latest.get("name") or ticker),
                "sector": str(latest.get("sector") or "기타"),
                "current_price": latest.get("current_price"),
                "current_return": return_values[-1],
                "return_change": return_values[-1] - return_values[0],
                "program_net_buy": program_values[-1],
                "program_increase": program_values[-1] - program_values[0],
                "comparison_count": len(sessions) - 1,
                "return_flow": _flow_text(return_values, lambda value: f"{value:+.2f}%"),
                "program_flow": _flow_text(
                    program_values,
                    lambda value: f"{value / 100_000_000:+,.1f}억",
                ),
                "session_flow": " → ".join(
                    re.sub(r"^.*\((\d{1,2}:\d{2})\).*$", r"\1", str(session))
                    for session in sessions
                ),
            }
        )

    if not candidates:
        return empty_summary, empty_history
    summary = pd.DataFrame(candidates).sort_values(
        ["program_increase", "return_change"], ascending=[False, True]
    )
    candidate_history = history[history["ticker"].astype(str).isin(candidate_tickers)].copy()
    candidate_history["program_net_buy_eok"] = candidate_history["program_net_buy"] / 100_000_000
    candidate_history["session_label"] = candidate_history["session"].map(
        lambda value: re.sub(r"^.*\((\d{1,2}:\d{2})\).*$", r"\1", str(value))
    )
    return summary.reset_index(drop=True), candidate_history.reset_index(drop=True)
