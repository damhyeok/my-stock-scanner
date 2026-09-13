"""Compact market-betting evidence for the read-only dashboard snapshot.

The analysis database keeps the complete audit payload.  The Streamlit copy
only needs the fields consumed by ``market_betting_engine.streamlit_tab``.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Mapping


STOCK_FEATURE_FIELDS = (
    "last_close",
    "session_vwap",
    "vwap_distance_ratio",
    "activity_acceleration",
    "short_return",
)
STOCK_RELATIVE_FIELDS = ("relative_short_return",)
UNIVERSE_STOCK_FIELDS = (
    "ticker",
    "name",
    "stock_name",
    "sector",
    "fluctuation_rate",
    "trading_value",
)
SETUP_FIELDS = (
    "setup_type",
    "trigger_price",
    "reference_level",
    "invalidation_price",
    "reward_reference",
)
POSITION_ASSESSMENT_FIELDS = (
    "current_price",
    "profit_loss_ratio",
    "decision",
    "reasons",
)


def _selected(mapping: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(mapping, Mapping):
        return {}
    return {field: mapping[field] for field in fields if field in mapping}


def _compact_stock_evidence(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    compact = {}
    features = _selected(value.get("features"), STOCK_FEATURE_FIELDS)
    relative = _selected(value.get("relative"), STOCK_RELATIVE_FIELDS)
    if features:
        compact["features"] = features
    if relative:
        compact["relative"] = relative
    # HourlyPath is already a display-sized summary of the rolling 60-minute
    # one-minute-bar path.  Keep it whole so new diagnostic columns remain safe.
    if isinstance(value.get("hourly_path"), Mapping):
        compact["hourly_path"] = dict(value["hourly_path"])
    return compact


def _compact_bundle(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        return {}
    compact: dict[str, Any] = {}
    market = value.get("market_features")
    if isinstance(market, Mapping) and market.get("as_of") is not None:
        compact["market_features"] = {"as_of": market["as_of"]}
    stocks = value.get("stocks")
    if isinstance(stocks, Mapping):
        compact["stocks"] = {
            str(ticker): _compact_stock_evidence(stock)
            for ticker, stock in stocks.items()
        }
    sectors = value.get("sectors")
    if isinstance(sectors, Mapping):
        compact["sectors"] = {
            str(name): {
                key: sector[key]
                for key in ("summary", "observed_members")
                if isinstance(sector, Mapping) and key in sector
            }
            for name, sector in sectors.items()
        }
    return compact


def compact_market_betting_evidence(value: Any) -> Any:
    """Return the evidence subset required to reproduce every dashboard view."""

    if not isinstance(value, Mapping):
        return value
    compact: dict[str, Any] = {"web_display_compacted": True}
    if "bundle" in value:
        compact["bundle"] = _compact_bundle(value.get("bundle"))

    adaptive = value.get("adaptive_universe")
    if isinstance(adaptive, Mapping):
        rows = adaptive.get("stocks")
        compact["adaptive_universe"] = {
            "stocks": [
                _selected(row, UNIVERSE_STOCK_FIELDS)
                for row in rows
                if isinstance(row, Mapping)
            ] if isinstance(rows, list) else []
        }

    setups = value.get("stock_setups")
    if isinstance(setups, Mapping):
        compact["stock_setups"] = {
            str(ticker): _selected(setup, SETUP_FIELDS)
            for ticker, setup in setups.items()
        }

    assessments = value.get("position_assessments")
    if isinstance(assessments, Mapping):
        compact["position_assessments"] = {
            str(ticker): _selected(assessment, POSITION_ASSESSMENT_FIELDS)
            for ticker, assessment in assessments.items()
        }
    return compact


def compact_market_betting_runs(connection: sqlite3.Connection) -> dict[str, int]:
    """Shrink evidence JSON in a web-copy connection, never the source DB."""

    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_betting_runs'"
    ).fetchone()
    if exists is None:
        return {"rows": 0, "before_bytes": 0, "after_bytes": 0}
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(market_betting_runs)")
    }
    if "derived_evidence_json" not in columns:
        return {"rows": 0, "before_bytes": 0, "after_bytes": 0}

    updates = []
    before_bytes = after_bytes = 0
    for run_id, payload in connection.execute(
        "SELECT run_id, derived_evidence_json FROM market_betting_runs "
        "WHERE derived_evidence_json IS NOT NULL"
    ):
        try:
            compact = compact_market_betting_evidence(json.loads(payload))
        except (TypeError, ValueError, json.JSONDecodeError):
            # Preserve an unexpected legacy payload rather than breaking a run.
            continue
        encoded = json.dumps(
            compact, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )
        before_bytes += len(payload.encode("utf-8"))
        after_bytes += len(encoded.encode("utf-8"))
        updates.append((encoded, run_id))
    connection.executemany(
        "UPDATE market_betting_runs SET derived_evidence_json=? WHERE run_id=?",
        updates,
    )
    return {
        "rows": len(updates),
        "before_bytes": before_bytes,
        "after_bytes": after_bytes,
    }
