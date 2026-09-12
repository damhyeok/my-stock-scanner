"""Compact, display-ready bottom-candidate rows for the web snapshot."""

import sqlite3

import pandas as pd


RULE_COLUMNS = [
    "signal_date", "scanner_model", "ticker", "name", "current_price",
    "today_change_rate", "market_cap", "trend_score", "rsi_14",
    "volume_ratio", "entry_price", "stop_price", "first_target_price",
    "target_room_pct", "decision_risk_summary",
]
LEGACY_COLUMNS = [
    "signal_date", "ticker", "name", "current_price", "today_change_rate",
    "market_cap", "bottom_score", "grade", "chart_score", "supply_score",
    "sector_market_score", "risk_penalty", "reasons", "risk_reasons",
]
STORED_COLUMNS = list(dict.fromkeys(
    ["source_kind", "display_order"] + RULE_COLUMNS + LEGACY_COLUMNS
))


def _table_exists(conn, table):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _ordered(frame, source_kind):
    if frame.empty:
        return frame
    frame.insert(0, "source_kind", source_kind)
    frame.insert(1, "display_order", frame.groupby("signal_date").cumcount())
    return frame


def build_bottom_candidate_display(conn):
    """Build compact rule and legacy rows without changing source tables."""
    frames = []
    if _table_exists(conn, "model_rule_scan_signals"):
        rules = pd.read_sql_query(
            """SELECT signal_date, model_id AS scanner_model, ticker, name,
               current_price, change_rate AS today_change_rate, market_cap,
               trend_score, rsi_14, volume_ratio, entry_price, stop_price,
               first_target_price, target_room_pct,
               signal_reason AS decision_risk_summary
               FROM model_rule_scan_signals
               WHERE universe_type = 'market_cap_10000eok_plus'
               ORDER BY signal_date, model_id, trend_score DESC, target_room_pct DESC, ticker""",
            conn,
        )
        if not rules.empty:
            frames.append(_ordered(rules, "RULE"))
    if (
        _table_exists(conn, "model_bottom_signals")
        and _table_exists(conn, "model_ohlcv_daily")
    ):
        legacy = pd.read_sql_query(
            """SELECT b.signal_date, b.ticker, b.name, b.current_price,
               (SELECT o.change_rate FROM model_ohlcv_daily o
                WHERE o.date = b.signal_date AND o.ticker = b.ticker
                  AND o.universe_type = b.universe_type LIMIT 1) AS today_change_rate,
               (SELECT o.market_cap FROM model_ohlcv_daily o
                WHERE o.date = b.signal_date AND o.ticker = b.ticker
                  AND o.universe_type = b.universe_type LIMIT 1) AS market_cap,
               b.bottom_score, b.grade, b.chart_score, b.supply_score,
               b.sector_market_score, b.risk_penalty, b.reasons, b.risk_reasons
               FROM model_bottom_signals b
               WHERE b.universe_type = 'market_cap_10000eok_plus'
               ORDER BY b.signal_date, b.bottom_score DESC, b.ticker""",
            conn,
        )
        if not legacy.empty:
            frames.append(_ordered(legacy, "LEGACY"))
    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    for column in STORED_COLUMNS:
        if column not in combined.columns:
            combined[column] = None
    combined[STORED_COLUMNS].to_sql(
        "web_bottom_candidates", conn, if_exists="replace", index=False
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_web_bottom_candidates_order "
        "ON web_bottom_candidates(source_kind, signal_date, display_order)"
    )
    return len(combined)


def read_bottom_candidate_display(conn, selected_date, target_signal_date=None):
    """Apply the legacy source/date precedence to compact stored rows."""
    selected = str(selected_date)
    for kind, columns in (("RULE", RULE_COLUMNS), ("LEGACY", LEGACY_COLUMNS)):
        if target_signal_date:
            date_sql, params = "signal_date = ?", (str(target_signal_date),)
        else:
            date_sql = (
                "signal_date = (SELECT MAX(signal_date) FROM web_bottom_candidates "
                "WHERE signal_date <= ? AND source_kind = ?)"
            )
            params = (selected, kind)
        query = (
            f"SELECT {', '.join(columns)} FROM web_bottom_candidates "
            f"WHERE {date_sql} AND source_kind = ? ORDER BY display_order"
        )
        frame = pd.read_sql_query(query, conn, params=params + (kind,))
        if not frame.empty:
            return frame
    return pd.DataFrame()
