"""Read-only SQLite loaders for raw source data."""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pandas as pd

from .schema import DAILY_COLUMNS


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _quote_identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"unsafe SQLite identifier: {value!r}")
    return f'"{value}"'


@contextmanager
def open_read_only(path: str | Path) -> Iterator[sqlite3.Connection]:
    """Open an existing SQLite database without creating journals or files."""
    resolved = Path(path).expanduser().resolve(strict=True)
    uri = resolved.as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        yield connection
    finally:
        connection.close()


def load_daily(
    database_path: str | Path,
    table: str,
    universe_type: str,
) -> pd.DataFrame:
    """Load one universe from the daily source with stable dtypes and keys."""
    quoted_table = _quote_identifier(table)
    selected = ", ".join(_quote_identifier(column) for column in DAILY_COLUMNS)
    query = f"SELECT {selected} FROM {quoted_table} WHERE universe_type = ? ORDER BY ticker, date"
    with open_read_only(database_path) as connection:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if exists is None:
            raise RuntimeError(f"required source table does not exist: {table}")
        frame = pd.read_sql_query(query, connection, params=(universe_type,))
    if frame.empty:
        raise RuntimeError(f"daily source has no rows for universe_type={universe_type!r}")
    frame["date"] = pd.to_datetime(frame["date"], format="%Y%m%d", errors="raise").dt.normalize()
    frame["ticker"] = frame["ticker"].astype("string").str.zfill(6)
    return frame


def load_market_closing(
    database_path: str | Path,
    table: str,
) -> pd.DataFrame:
    """Load raw closing-analysis market snapshots without filling missing times."""
    quoted_table = _quote_identifier(table)
    columns = ("trade_date", "analysis_type", "snapshot_time", "program_net", "basis")
    selected = ", ".join(_quote_identifier(column) for column in columns)
    query = (
        f"SELECT {selected} FROM {quoted_table} "
        "WHERE analysis_type = 'closing' AND snapshot_time IN ('14:30', '15:30') "
        "ORDER BY trade_date, snapshot_time"
    )
    with open_read_only(database_path) as connection:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if exists is None:
            raise RuntimeError(f"required source table does not exist: {table}")
        frame = pd.read_sql_query(query, connection)
    if frame.empty:
        raise RuntimeError("market source has no standard closing snapshots")
    frame["trade_date"] = pd.to_datetime(
        frame["trade_date"], format="%Y%m%d", errors="raise"
    ).dt.normalize()
    return frame


def load_selected_minute_bars(database_path: str | Path) -> pd.DataFrame:
    """Load the stored regular-session sampled bars without treating them as full intraday data."""
    columns = ("timestamp", "ticker", "open", "high", "low", "close", "volume")
    selected = ", ".join(_quote_identifier(column) for column in columns)
    with open_read_only(database_path) as connection:
        legacy_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='stock_minute_bars'"
        ).fetchone()
        if legacy_exists is not None:
            frame = pd.read_sql_query(
                f"SELECT {selected} FROM stock_minute_bars ORDER BY ticker, timestamp",
                connection,
            )
        else:
            intraday_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='intraday_stock_bars'"
            ).fetchone()
            if intraday_exists is None:
                raise RuntimeError(
                    "required minute source table does not exist: "
                    "stock_minute_bars or intraday_stock_bars"
                )
            frame = pd.read_sql_query(
                """
                SELECT trade_date || ' ' || bar_time AS timestamp,
                       ticker, open, high, low, close, volume
                FROM intraday_stock_bars
                ORDER BY ticker, trade_date, bar_time
                """,
                connection,
            )
    if frame.empty:
        raise RuntimeError("minute source table has no rows")
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="raise")
    frame["ticker"] = frame["ticker"].astype("string").str.zfill(6)
    return frame
