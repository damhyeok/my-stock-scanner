"""Small date/session catalog for dashboard controls."""

from __future__ import annotations

import re
import sqlite3

import pandas as pd


SESSION_COLUMNS = ["date", "session", "session_order"]


def _session_order(value) -> int:
    match = re.search(r"\((\d{1,2}):(\d{2})\)", str(value))
    if not match:
        return -1
    return int(match.group(1)) * 60 + int(match.group(2))


def build_dashboard_sessions(connection: sqlite3.Connection) -> pd.DataFrame:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='daily_stocks'"
    ).fetchone()
    if exists is None:
        result = pd.DataFrame(columns=SESSION_COLUMNS)
    else:
        result = pd.read_sql_query(
            "SELECT DISTINCT date, session FROM daily_stocks "
            "WHERE session NOT LIKE '%시간외%'",
            connection,
        )
        result["date"] = result["date"].astype(str)
        result["session_order"] = result["session"].map(_session_order)
        result = result.sort_values(
            ["date", "session_order", "session"],
            ascending=[False, False, False],
        ).reset_index(drop=True)
    result.to_sql(
        "web_dashboard_sessions", connection, if_exists="replace", index=False
    )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_web_dashboard_sessions "
        "ON web_dashboard_sessions(date, session)"
    )
    return result


def read_dashboard_sessions(connection: sqlite3.Connection) -> pd.DataFrame:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='web_dashboard_sessions'"
    ).fetchone()
    if exists is not None:
        return pd.read_sql_query(
            "SELECT * FROM web_dashboard_sessions "
            "ORDER BY date DESC, session_order DESC, session DESC",
            connection,
        )
    raw_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='daily_stocks'"
    ).fetchone()
    if raw_exists is None:
        return pd.DataFrame(columns=SESSION_COLUMNS)
    result = pd.read_sql_query(
        "SELECT DISTINCT date, session FROM daily_stocks "
        "WHERE session NOT LIKE '%시간외%'",
        connection,
    )
    result["date"] = result["date"].astype(str)
    result["session_order"] = result["session"].map(_session_order)
    return result.sort_values(
        ["date", "session_order", "session"], ascending=[False, False, False]
    ).reset_index(drop=True)
