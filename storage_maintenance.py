"""Bound Oracle operational storage without discarding long-horizon daily data."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from market_betting_engine.storage import prune_decision_history


KST = timezone(timedelta(hours=9))
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

STOCK_DATA_RETENTION = {
    "daily_stocks": ("date", 260),
    "stock_news": ("date", 90),
    "intraday_stock_bars": ("trade_date", 60),
    "intraday_index_bars": ("trade_date", 260),
    "intraday_relative_strength_snapshots": ("trade_date", 260),
    "intraday_relative_strength_runs": ("trade_date", 260),
    "market_strength_snapshots": ("trade_date", 260),
    "market_program_snapshots": ("trade_date", 260),
    "stock_program_net_snapshots": ("trade_date", 260),
    "stock_program_net_runs": ("trade_date", 260),
    "sector_flow_windows": ("trade_date", 260),
    "close_bet_scans": ("trade_date", 260),
    "close_bet_scan_runs": ("trade_date", 260),
    "close_bet_model3_scans": ("trade_date", 260),
    "close_bet_model3_runs": ("trade_date", 260),
    "close_bet_rule_model_evaluations": ("trade_date", 260),
    "model_universe_snapshots": ("snapshot_date", 260),
    "model_ohlcv_daily": ("date", 1260),
    "model_feature_daily": ("date", 1260),
    "model_market_regimes": ("date", 1260),
    "model_bottom_signals": ("signal_date", 260),
    "model_rule_scan_signals": ("signal_date", 260),
    "model_bottom_scan_runs": ("signal_date", 260),
}

PROGRAM_DATA_RETENTION = {
    "market_program_snapshots": ("trade_date", 260),
}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _database_stats(conn: sqlite3.Connection) -> dict:
    page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
    page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
    free_pages = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
    return {
        "page_size": page_size,
        "page_count": page_count,
        "free_pages": free_pages,
        "free_ratio": (free_pages / page_count) if page_count else 0.0,
    }


def prune_database(db_path, policies, *, allow_vacuum=False) -> dict:
    path = Path(db_path)
    result = {
        "path": str(path),
        "exists": path.is_file(),
        "bytes_before": path.stat().st_size if path.is_file() else 0,
        "bytes_after": 0,
        "deleted_rows": {},
        "vacuumed": False,
    }
    if not path.is_file():
        return result

    with sqlite3.connect(path, timeout=60) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        for table, (date_column, keep_dates) in policies.items():
            if not IDENTIFIER.fullmatch(table) or not IDENTIFIER.fullmatch(date_column):
                raise ValueError(f"Unsafe retention identifier: {table}.{date_column}")
            if not _table_exists(conn, table):
                continue
            cutoff_row = conn.execute(
                f'''SELECT MIN("{date_column}") FROM (
                        SELECT DISTINCT "{date_column}" FROM "{table}"
                        WHERE "{date_column}" IS NOT NULL
                        ORDER BY "{date_column}" DESC LIMIT ?
                    )''',
                (int(keep_dates),),
            ).fetchone()
            cutoff = cutoff_row[0] if cutoff_row else None
            if cutoff is None:
                continue
            cursor = conn.execute(
                f'DELETE FROM "{table}" WHERE "{date_column}" < ?', (cutoff,)
            )
            result["deleted_rows"][table] = max(0, int(cursor.rowcount))
        conn.commit()
        conn.execute("PRAGMA optimize")
        stats = _database_stats(conn)

    if allow_vacuum and stats["free_ratio"] >= 0.20:
        disk_free = shutil.disk_usage(path.parent).free
        if disk_free >= max(result["bytes_before"] * 2, 512 * 1024 * 1024):
            with sqlite3.connect(path, timeout=60) as conn:
                conn.execute("VACUUM")
            result["vacuumed"] = True

    with sqlite3.connect(path, timeout=60) as conn:
        result["sqlite"] = _database_stats(conn)
        result["integrity"] = conn.execute("PRAGMA quick_check").fetchone()[0]
    result["bytes_after"] = path.stat().st_size
    return result


def _git_storage(project_dir: Path) -> dict:
    git_dir = project_dir / ".git"
    result = {"exists": git_dir.exists(), "count_objects": ""}
    if not git_dir.exists():
        return result
    try:
        completed = subprocess.run(
            ["git", "count-objects", "-v"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        result["count_objects"] = completed.stdout.strip()
        result["return_code"] = completed.returncode
        values = {}
        for line in completed.stdout.splitlines():
            key, separator, value = line.partition(":")
            if separator:
                values[key.strip()] = value.strip()
        result["loose_objects"] = int(values.get("count", "0") or 0)
        result["loose_bytes"] = int(values.get("size", "0") or 0) * 1024
        result["packed_bytes"] = int(values.get("size-pack", "0") or 0) * 1024
    except (OSError, subprocess.SubprocessError) as error:
        result["error"] = str(error)
    return result


def _compact_git_repository(project_dir: Path, before: dict) -> dict:
    threshold = 512 * 1024 * 1024
    result = {
        "attempted": False,
        "threshold_bytes": threshold,
        "return_code": None,
    }
    if not before.get("exists") or before.get("loose_bytes", 0) < threshold:
        return result
    result["attempted"] = True
    git_commands = [
        [
            "git",
            "-c",
            "pack.threads=1",
            "-c",
            "pack.windowMemory=64m",
            "repack",
            "-d",
            "--window=5",
            "--depth=20",
        ],
        ["git", "prune-packed"],
        ["git", "prune", "--expire", "now"],
    ]
    priority_prefix = []
    if os.name != "nt":
        if shutil.which("ionice"):
            priority_prefix.extend(["ionice", "-c", "3"])
        if shutil.which("nice"):
            priority_prefix.extend(["nice", "-n", "19"])
    commands = [priority_prefix + command for command in git_commands]
    result["commands"] = []
    try:
        for command in commands:
            completed = subprocess.run(
                command,
                cwd=project_dir,
                capture_output=True,
                text=True,
                timeout=14400,
                check=False,
            )
            command_result = {
                "command": " ".join(command),
                "return_code": completed.returncode,
                "stdout": completed.stdout.strip(),
                "stderr": completed.stderr.strip(),
            }
            result["commands"].append(command_result)
            if completed.returncode:
                result["return_code"] = completed.returncode
                break
        else:
            result["return_code"] = 0
    except (OSError, subprocess.SubprocessError) as error:
        result["error"] = str(error)
    return result


def _write_report(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f"{path.stem}_", suffix=".json", dir=path.parent
    )
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def run_storage_maintenance(project_dir, *, allow_vacuum=False) -> dict:
    root = Path(project_dir).resolve()
    started = datetime.now(KST)
    disk_before = shutil.disk_usage(root)
    git_before = _git_storage(root)

    stock_result = prune_database(
        root / "stock_data.db", STOCK_DATA_RETENTION, allow_vacuum=allow_vacuum
    )
    program_result = prune_database(
        root / "program_snapshots.db",
        PROGRAM_DATA_RETENTION,
        allow_vacuum=allow_vacuum,
    )
    decision_prune = prune_decision_history(
        root / "stock_data.db",
        keep_run_trade_dates=30,
        keep_raw_observation_trade_dates=2,
    )
    git_gc = _compact_git_repository(root, git_before)
    git_after = _git_storage(root)

    disk_after = shutil.disk_usage(root)
    used_ratio = disk_after.used / disk_after.total if disk_after.total else 0.0
    report = {
        "started_at_kst": started.isoformat(timespec="seconds"),
        "completed_at_kst": datetime.now(KST).isoformat(timespec="seconds"),
        "policy": {
            "stock_data": STOCK_DATA_RETENTION,
            "program_data": PROGRAM_DATA_RETENTION,
            "market_betting_run_dates": 30,
            "market_betting_raw_dates": 2,
        },
        "disk": {
            "total_bytes": disk_after.total,
            "used_bytes_before": disk_before.used,
            "used_bytes_after": disk_after.used,
            "free_bytes_after": disk_after.free,
            "used_percent": round(used_ratio * 100, 2),
            "status": "CRITICAL" if used_ratio >= 0.85 else "WARNING" if used_ratio >= 0.70 else "OK",
        },
        "databases": {
            "stock_data": stock_result,
            "program_snapshots": program_result,
        },
        "market_betting": {
            "deleted_runs": decision_prune.deleted_runs,
            "deleted_observations": decision_prune.deleted_observations,
        },
        "git": {
            "before": git_before,
            "gc": git_gc,
            "after": git_after,
        },
    }
    _write_report(root / "reports" / "storage_maintenance_latest.json", report)
    return report
