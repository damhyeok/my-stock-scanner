"""SQLite persistence for auditable market-betting engine decisions."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from .contracts import Judgment, Observation
from .orchestrator import DecisionCycleResult
from .engines import OvernightAssessment
from .session import SessionContext


SCHEMA_VERSION = 1


@dataclass(frozen=True)
class PersistenceReceipt:
    run_id: str
    database_path: str
    judgment_count: int
    stock_state_count: int
    observation_count: int


@dataclass(frozen=True)
class PruneReceipt:
    deleted_runs: int
    deleted_observations: int


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set)):
        return [_jsonable(item) for item in value]
    return value


def _json(value: Any) -> str:
    return json.dumps(_jsonable(value), ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def init_decision_db(db_path: str | Path) -> Path:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS market_betting_runs (
                run_id TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                engine_version TEXT NOT NULL,
                config_version TEXT NOT NULL,
                target_trade_date TEXT NOT NULL,
                evaluated_at_kst TEXT NOT NULL,
                session_phase TEXT NOT NULL,
                calendar_source TEXT NOT NULL,
                market_decision TEXT NOT NULL,
                quality_blocking INTEGER NOT NULL,
                observation_count INTEGER NOT NULL,
                quality_json TEXT NOT NULL,
                derived_evidence_json TEXT,
                created_at_utc TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS market_betting_judgments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                scope_type TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                decision TEXT NOT NULL,
                confidence_label TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                counter_evidence_json TEXT NOT NULL,
                warnings_json TEXT NOT NULL,
                blockers_json TEXT NOT NULL,
                invalidation_conditions_json TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES market_betting_runs(run_id) ON DELETE CASCADE,
                UNIQUE (run_id, scope_type, scope_id)
            );

            CREATE TABLE IF NOT EXISTS market_betting_stock_states (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                previous_state TEXT NOT NULL,
                current_state TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                transition_allowed INTEGER NOT NULL,
                FOREIGN KEY (run_id) REFERENCES market_betting_runs(run_id) ON DELETE CASCADE,
                UNIQUE (run_id, symbol)
            );

            CREATE TABLE IF NOT EXISTS market_betting_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                metric TEXT NOT NULL,
                value_json TEXT NOT NULL,
                source TEXT NOT NULL,
                source_trade_date TEXT,
                observed_at TEXT NOT NULL,
                unit TEXT,
                semantics_status TEXT NOT NULL,
                calculation_mode TEXT NOT NULL,
                field_name TEXT,
                trade_date_provenance TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES market_betting_runs(run_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_market_betting_runs_date
                ON market_betting_runs(target_trade_date, evaluated_at_kst DESC);
            CREATE INDEX IF NOT EXISTS idx_market_betting_judgments_run
                ON market_betting_judgments(run_id, scope_type);
            CREATE INDEX IF NOT EXISTS idx_market_betting_stock_states_run
                ON market_betting_stock_states(run_id);
            CREATE INDEX IF NOT EXISTS idx_market_betting_observations_run
                ON market_betting_observations(run_id);
            """
        )
    return path


def _judgment_row(run_id: str, scope_type: str, scope_id: str, judgment: Judgment) -> tuple[Any, ...]:
    return (
        run_id,
        scope_type,
        scope_id,
        judgment.decision,
        judgment.confidence_label,
        _json(judgment.evidence),
        _json(judgment.counter_evidence),
        _json(judgment.warnings),
        _json(judgment.blockers),
        _json(judgment.invalidation_conditions),
    )


def save_decision_cycle(
    db_path: str | Path,
    *,
    context: SessionContext,
    result: DecisionCycleResult,
    config_version: str,
    engine_version: str,
    observations: Sequence[Observation] = (),
    derived_evidence: Any = None,
    overnight: Optional[OvernightAssessment] = None,
    run_id: Optional[str] = None,
) -> PersistenceReceipt:
    """Persist one complete cycle in a single transaction."""

    if not config_version.strip() or not engine_version.strip():
        raise ValueError("config_version and engine_version are required")
    identifier = run_id or str(uuid.uuid4())
    path = init_decision_db(db_path)
    judgment_rows = [_judgment_row(identifier, "MARKET", "KOSPI", result.market)]
    judgment_rows.extend(
        _judgment_row(identifier, "SECTOR", name, judgment)
        for name, judgment in result.sectors.items()
    )
    if overnight is not None:
        judgment_rows.extend(
            (
                _judgment_row(identifier, "OVERNIGHT", "CLOSE_NEW_ENTRY", overnight.close_new_entry),
                _judgment_row(identifier, "OVERNIGHT", "HOLD_EXISTING", overnight.hold_existing),
            )
        )

    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            """
            INSERT INTO market_betting_runs (
                run_id, schema_version, engine_version, config_version,
                target_trade_date, evaluated_at_kst, session_phase, calendar_source,
                market_decision, quality_blocking, observation_count, quality_json,
                derived_evidence_json, created_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                identifier,
                SCHEMA_VERSION,
                engine_version,
                config_version,
                context.target_trade_date.isoformat(),
                context.evaluated_at_kst.isoformat(),
                context.phase.value,
                context.calendar_source,
                result.market.decision,
                int(result.quality.blocking),
                result.observation_count,
                _json(result.quality),
                None if derived_evidence is None else _json(derived_evidence),
                datetime.utcnow().isoformat(timespec="seconds") + "Z",
            ),
        )
        conn.executemany(
            """
            INSERT INTO market_betting_judgments (
                run_id, scope_type, scope_id, decision, confidence_label,
                evidence_json, counter_evidence_json, warnings_json, blockers_json,
                invalidation_conditions_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            judgment_rows,
        )
        conn.executemany(
            """
            INSERT INTO market_betting_stock_states (
                run_id, symbol, previous_state, current_state, reason_code, transition_allowed
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    identifier,
                    symbol,
                    transition.previous.value,
                    transition.current.value,
                    transition.reason_code,
                    int(transition.allowed),
                )
                for symbol, transition in result.stocks.items()
            ],
        )
        if observations:
            conn.executemany(
                """
                INSERT INTO market_betting_observations (
                    run_id, metric, value_json, source, source_trade_date, observed_at,
                    unit, semantics_status, calculation_mode, field_name, trade_date_provenance
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        identifier,
                        item.metric,
                        _json(item.value),
                        item.meta.source,
                        item.meta.source_trade_date.isoformat() if item.meta.source_trade_date else None,
                        item.meta.observed_at.isoformat(),
                        item.meta.unit,
                        item.meta.semantics_status.value,
                        item.meta.calculation_mode.value,
                        item.meta.field_name,
                        item.meta.trade_date_provenance.value,
                    )
                    for item in observations
                ],
            )
    return PersistenceReceipt(
        identifier,
        str(path),
        len(judgment_rows),
        len(result.stocks),
        len(observations),
    )


def prune_decision_history(
    db_path: str | Path,
    *,
    keep_run_trade_dates: int = 30,
    keep_raw_observation_trade_dates: int = 2,
) -> PruneReceipt:
    """Bound operational storage while retaining compact decisions longer."""

    if keep_run_trade_dates < 1 or keep_raw_observation_trade_dates < 0:
        raise ValueError("retention values must be non-negative and keep_run_trade_dates must be positive")
    path = Path(db_path)
    if not path.exists():
        return PruneReceipt(0, 0)
    try:
        with sqlite3.connect(path) as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            run_dates = [
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT target_trade_date FROM market_betting_runs "
                    "ORDER BY target_trade_date DESC"
                ).fetchall()
            ]
            deleted_observations = 0
            if len(run_dates) > keep_raw_observation_trade_dates:
                raw_cutoff = (
                    run_dates[keep_raw_observation_trade_dates - 1]
                    if keep_raw_observation_trade_dates
                    else "9999-12-31"
                )
                cursor = conn.execute(
                    """
                    DELETE FROM market_betting_observations
                    WHERE run_id IN (
                        SELECT run_id FROM market_betting_runs WHERE target_trade_date < ?
                    )
                    """,
                    (raw_cutoff,),
                )
                deleted_observations = cursor.rowcount
            deleted_runs = 0
            if len(run_dates) > keep_run_trade_dates:
                run_cutoff = run_dates[keep_run_trade_dates - 1]
                cursor = conn.execute(
                    "DELETE FROM market_betting_runs WHERE target_trade_date < ?",
                    (run_cutoff,),
                )
                deleted_runs = cursor.rowcount
            return PruneReceipt(deleted_runs, deleted_observations)
    except sqlite3.OperationalError:
        return PruneReceipt(0, 0)


def _parse_json_columns(row: dict[str, Any], columns: Iterable[str]) -> dict[str, Any]:
    for column in columns:
        if row.get(column) is not None:
            row[column.removesuffix("_json")] = json.loads(row.pop(column))
    return row


def list_decision_runs(
    db_path: str | Path,
    *,
    target_trade_date: Optional[str] = None,
    limit: int = 30,
) -> list[dict[str, Any]]:
    path = Path(db_path)
    if not path.exists() or limit < 1:
        return []
    try:
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            if target_trade_date:
                rows = conn.execute(
                    "SELECT * FROM market_betting_runs WHERE target_trade_date=? "
                    "ORDER BY evaluated_at_kst DESC LIMIT ?",
                    (target_trade_date, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM market_betting_runs ORDER BY evaluated_at_kst DESC LIMIT ?",
                    (limit,),
                ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [
        _parse_json_columns(dict(row), ("quality_json", "derived_evidence_json"))
        for row in rows
    ]


def load_decision_run(db_path: str | Path, run_id: str) -> Optional[dict[str, Any]]:
    path = Path(db_path)
    if not path.exists():
        return None
    try:
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            run = conn.execute(
                "SELECT * FROM market_betting_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if run is None:
                return None
            judgments = conn.execute(
                "SELECT * FROM market_betting_judgments WHERE run_id=? ORDER BY scope_type, scope_id",
                (run_id,),
            ).fetchall()
            stocks = conn.execute(
                "SELECT * FROM market_betting_stock_states WHERE run_id=? ORDER BY symbol",
                (run_id,),
            ).fetchall()
    except sqlite3.OperationalError:
        return None

    parsed_run = _parse_json_columns(dict(run), ("quality_json", "derived_evidence_json"))
    parsed_judgments = [
        _parse_json_columns(
            dict(row),
            (
                "evidence_json",
                "counter_evidence_json",
                "warnings_json",
                "blockers_json",
                "invalidation_conditions_json",
            ),
        )
        for row in judgments
    ]
    return {"run": parsed_run, "judgments": parsed_judgments, "stocks": [dict(row) for row in stocks]}
