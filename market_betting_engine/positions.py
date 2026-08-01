"""Persistent user positions and position-specific overnight assessments."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

from .contracts import AxisSignal, DataQualityReport
from .engines import assess_overnight_permissions


THESIS_STATUSES = {"ACTIVE", "BROKEN", "UNSPECIFIED"}


@dataclass(frozen=True)
class Position:
    ticker: str
    name: str
    average_price: float
    quantity: float
    thesis_status: str = "UNSPECIFIED"
    thesis_note: str = ""
    invalidation_price: float | None = None
    updated_at_utc: str = ""


@dataclass(frozen=True)
class PositionAssessment:
    ticker: str
    name: str
    average_price: float
    quantity: float
    current_price: float | None
    profit_loss_ratio: float | None
    profit_loss_amount: float | None
    thesis_status: str
    thesis_valid: bool | None
    invalidation_price: float | None
    decision: str
    reasons: tuple[str, ...]


def init_position_table(db_path: str | Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS market_betting_positions (
                ticker TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                average_price REAL NOT NULL CHECK (average_price > 0),
                quantity REAL NOT NULL CHECK (quantity > 0),
                thesis_status TEXT NOT NULL DEFAULT 'UNSPECIFIED',
                thesis_note TEXT NOT NULL DEFAULT '',
                invalidation_price REAL,
                updated_at_utc TEXT NOT NULL
            )
            """
        )


def list_positions(db_path: str | Path) -> list[Position]:
    init_position_table(db_path)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT ticker, name, average_price, quantity, thesis_status,
                   thesis_note, invalidation_price, updated_at_utc
            FROM market_betting_positions ORDER BY name, ticker
            """
        ).fetchall()
    return [Position(*row) for row in rows]


def upsert_position(db_path: str | Path, position: Position) -> None:
    ticker = str(position.ticker).strip().zfill(6)
    status = str(position.thesis_status).upper()
    if len(ticker) != 6 or not ticker.isdigit():
        raise ValueError("ticker must contain six digits")
    if not position.name.strip():
        raise ValueError("position name is required")
    if position.average_price <= 0 or position.quantity <= 0:
        raise ValueError("average price and quantity must be positive")
    if status not in THESIS_STATUSES:
        raise ValueError("invalid thesis status")
    if position.invalidation_price is not None and position.invalidation_price <= 0:
        raise ValueError("invalidation price must be positive")
    init_position_table(db_path)
    updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO market_betting_positions (
                ticker, name, average_price, quantity, thesis_status,
                thesis_note, invalidation_price, updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                name=excluded.name,
                average_price=excluded.average_price,
                quantity=excluded.quantity,
                thesis_status=excluded.thesis_status,
                thesis_note=excluded.thesis_note,
                invalidation_price=excluded.invalidation_price,
                updated_at_utc=excluded.updated_at_utc
            """,
            (
                ticker,
                position.name.strip(),
                float(position.average_price),
                float(position.quantity),
                status,
                position.thesis_note.strip(),
                position.invalidation_price,
                updated_at,
            ),
        )


def remove_position(db_path: str | Path, ticker: str) -> bool:
    normalized = str(ticker).strip().zfill(6)
    init_position_table(db_path)
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            "DELETE FROM market_betting_positions WHERE ticker=?", (normalized,)
        )
    return cursor.rowcount > 0


def assess_position(
    position: Position,
    current_price: Optional[float],
    market_signals: Sequence[AxisSignal],
    quality: DataQualityReport,
) -> PositionAssessment:
    reasons = []
    thesis_valid: bool | None
    if position.thesis_status == "BROKEN":
        thesis_valid = False
        reasons.append("USER_MARKED_THESIS_BROKEN")
    elif position.thesis_status == "UNSPECIFIED":
        thesis_valid = None
        reasons.append("THESIS_STATUS_NOT_SUPPLIED")
    elif position.invalidation_price is not None and current_price is None:
        thesis_valid = None
        reasons.append("CURRENT_PRICE_UNAVAILABLE_FOR_INVALIDATION_CHECK")
    elif (
        position.invalidation_price is not None
        and current_price is not None
        and current_price <= position.invalidation_price
    ):
        thesis_valid = False
        reasons.append("POSITION_INVALIDATION_PRICE_BREACHED")
    else:
        thesis_valid = True
        reasons.append("POSITION_THESIS_ACTIVE")

    overnight = assess_overnight_permissions(
        market_signals,
        existing_thesis_valid=thesis_valid,
        quality=quality,
    ).hold_existing
    ratio = (
        current_price / position.average_price - 1
        if current_price is not None and position.average_price > 0
        else None
    )
    amount = (
        (current_price - position.average_price) * position.quantity
        if current_price is not None
        else None
    )
    reasons.extend(item.code for item in overnight.blockers)
    reasons.extend(item.code for item in overnight.warnings)
    return PositionAssessment(
        ticker=position.ticker,
        name=position.name,
        average_price=position.average_price,
        quantity=position.quantity,
        current_price=current_price,
        profit_loss_ratio=ratio,
        profit_loss_amount=amount,
        thesis_status=position.thesis_status,
        thesis_valid=thesis_valid,
        invalidation_price=position.invalidation_price,
        decision=overnight.decision,
        reasons=tuple(dict.fromkeys(reasons)),
    )
