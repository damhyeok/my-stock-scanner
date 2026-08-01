"""Summarize multi-checkpoint live evidence without promoting any field."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


REQUIRED_PROBES = (
    "kis_stock_minute",
    "kis_index_minute_kospi",
    "kis_program_summary_kospi",
    "kis_futures_minute_active",
)
WINDOWS = {
    "OPEN": ("093000", "103000"),
    "MID": ("113000", "133000"),
    "CLOSE": ("150000", "153000"),
}


def _window(value: datetime) -> str | None:
    hhmmss = value.strftime("%H%M%S")
    for name, (lower, upper) in WINDOWS.items():
        if lower <= hhmmss <= upper:
            return name
    return None


def build_readiness(db_path: str | Path) -> dict[str, Any]:
    rows = []
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                """
                SELECT probe_id, started_at_kst, contract_review_status,
                       contract_checks_json, run_id
                FROM api_probe_runs
                WHERE probe_id IN (?,?,?,?)
                ORDER BY started_at_kst DESC
                """,
                REQUIRED_PROBES,
            ).fetchall()
    except sqlite3.OperationalError:
        pass

    by_date: dict[str, dict[str, dict[str, Any]]] = {}
    for probe_id, started_text, status, checks_json, run_id in rows:
        try:
            started = datetime.fromisoformat(started_text)
        except (TypeError, ValueError):
            continue
        checkpoint = _window(started)
        if checkpoint is None or started.weekday() >= 5:
            continue
        trade_date = started.date().isoformat()
        probe_windows = by_date.setdefault(trade_date, {}).setdefault(probe_id, {})
        if checkpoint in probe_windows:
            continue
        try:
            checks = json.loads(checks_json or "[]")
        except json.JSONDecodeError:
            checks = []
        probe_windows[checkpoint] = {
            "status": status,
            "run_id": run_id,
            "started_at_kst": started_text,
            "all_checks_passed": bool(checks) and all(item.get("passed") is True for item in checks),
        }

    latest_date = max(by_date, default=None)
    probes = {}
    selected = by_date.get(latest_date, {}) if latest_date else {}
    for probe_id in REQUIRED_PROBES:
        checkpoints = selected.get(probe_id, {})
        ready = all(
            checkpoints.get(name, {}).get("status") == "REVIEW_READY"
            and checkpoints.get(name, {}).get("all_checks_passed") is True
            for name in WINDOWS
        )
        probes[probe_id] = {
            "status": "READY_FOR_MANUAL_REVIEW" if ready else "PENDING_CHECKPOINTS",
            "checkpoints": checkpoints,
            "missing_checkpoints": [name for name in WINDOWS if name not in checkpoints],
        }
    return {
        "trade_date": latest_date,
        "required_windows": list(WINDOWS),
        "auto_promotes_registry": False,
        "overall_status": (
            "READY_FOR_MANUAL_REVIEW"
            if probes and all(item["status"] == "READY_FOR_MANUAL_REVIEW" for item in probes.values())
            else "PENDING_CHECKPOINTS"
        ),
        "probes": probes,
    }


def save_readiness_report(db_path: str | Path, output_dir: str | Path) -> Path:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "contract_readiness.json"
    path.write_text(
        json.dumps(build_readiness(db_path), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path
