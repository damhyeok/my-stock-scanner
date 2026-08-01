"""Explicit exchange-session context; holidays are never guessed locally."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum


KST = timezone(timedelta(hours=9))


class SessionPhase(str, Enum):
    PRE_OPEN = "PRE_OPEN"
    CONTINUOUS = "CONTINUOUS"
    PRE_CLOSE_FLOW = "PRE_CLOSE_FLOW"       # 14:30-15:00
    CLOSE_CONTINUITY = "CLOSE_CONTINUITY"   # 15:00-15:20
    CLOSING_AUCTION = "CLOSING_AUCTION"     # 15:20-15:30
    POST_CLOSE = "POST_CLOSE"
    NON_SESSION = "NON_SESSION"


@dataclass(frozen=True)
class SessionContext:
    target_trade_date: date
    evaluated_at: datetime
    is_exchange_session_date: bool
    calendar_source: str

    @property
    def evaluated_at_kst(self) -> datetime:
        value = self.evaluated_at
        if value.tzinfo is None:
            value = value.replace(tzinfo=KST)
        return value.astimezone(KST)

    @property
    def phase(self) -> SessionPhase:
        if not self.is_exchange_session_date or self.evaluated_at_kst.date() != self.target_trade_date:
            return SessionPhase.NON_SESSION
        current = self.evaluated_at_kst.time().replace(tzinfo=None)
        if current < time(9, 0):
            return SessionPhase.PRE_OPEN
        if current < time(14, 30):
            return SessionPhase.CONTINUOUS
        if current < time(15, 0):
            return SessionPhase.PRE_CLOSE_FLOW
        if current < time(15, 20):
            return SessionPhase.CLOSE_CONTINUITY
        if current < time(15, 30):
            return SessionPhase.CLOSING_AUCTION
        return SessionPhase.POST_CLOSE
