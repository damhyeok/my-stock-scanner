"""Shared contracts for observed data, derived metrics, and judgments.

The module deliberately keeps those three layers separate.  An API value is not
silently promoted to a trading judgment, and a price-derived proxy is never
labelled as actual money flow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, Mapping, Optional, Sequence, Tuple


class VerificationStatus(str, Enum):
    VERIFIED = "VERIFIED"
    PARTIAL = "PARTIAL"
    UNVERIFIED = "UNVERIFIED"
    UNAVAILABLE = "UNAVAILABLE"


class CalculationMode(str, Enum):
    ACTUAL = "ACTUAL"
    PROXY = "PROXY"
    ACTIVITY = "ACTIVITY"
    DERIVED = "DERIVED"


class TradeDateProvenance(str, Enum):
    RESPONSE_FIELD = "RESPONSE_FIELD"
    REQUEST_CONTEXT = "REQUEST_CONTEXT"
    UNKNOWN = "UNKNOWN"


class QualitySeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    BLOCKING = "BLOCKING"


class AxisStatus(str, Enum):
    PASS = "PASS"
    WARNING = "WARNING"
    FAIL = "FAIL"
    UNAVAILABLE = "UNAVAILABLE"


class MarketPermission(str, Enum):
    ALLOW = "ALLOW"
    SELECTIVE = "SELECTIVE"
    BLOCK = "BLOCK"
    NOT_EVALUABLE = "NOT_EVALUABLE"


class SectorState(str, Enum):
    LEADING = "LEADING"
    EMERGING = "EMERGING"
    NEUTRAL = "NEUTRAL"
    FADING = "FADING"
    AVOID = "AVOID"
    NOT_EVALUABLE = "NOT_EVALUABLE"


class StockState(str, Enum):
    WATCH = "WATCH"
    SETUP = "SETUP"
    TRIGGERED = "TRIGGERED"
    EXTENDED = "EXTENDED"
    FAILED = "FAILED"
    INVALIDATED = "INVALIDATED"
    NOT_EVALUABLE = "NOT_EVALUABLE"


class CloseNewEntryPermission(str, Enum):
    ALLOWED = "ALLOWED"
    SELECTIVE = "SELECTIVE"
    BLOCKED = "BLOCKED"
    NOT_EVALUABLE = "NOT_EVALUABLE"


class ExistingPositionPermission(str, Enum):
    HOLD = "HOLD"
    REDUCE = "REDUCE"
    EXIT = "EXIT"
    NOT_EVALUABLE = "NOT_EVALUABLE"


@dataclass(frozen=True)
class ObservationMeta:
    source: str
    observed_at: datetime
    source_trade_date: Optional[date]
    unit: Optional[str]
    semantics_status: VerificationStatus
    calculation_mode: CalculationMode = CalculationMode.ACTUAL
    stale_after_seconds: Optional[int] = None
    environment: str = "production"
    field_name: Optional[str] = None
    trade_date_provenance: TradeDateProvenance = TradeDateProvenance.UNKNOWN


@dataclass(frozen=True)
class Observation:
    metric: str
    value: Any
    meta: ObservationMeta


@dataclass(frozen=True)
class QualityIssue:
    code: str
    severity: QualitySeverity
    message: str
    metrics: Tuple[str, ...] = ()
    sources: Tuple[str, ...] = ()


@dataclass(frozen=True)
class DataQualityReport:
    issues: Tuple[QualityIssue, ...] = ()
    observed_count: int = 0
    expected_count: Optional[int] = None

    @property
    def blocking(self) -> bool:
        return any(i.severity == QualitySeverity.BLOCKING for i in self.issues)

    @property
    def completeness_ratio(self) -> Optional[float]:
        if self.expected_count is None or self.expected_count <= 0:
            return None
        return min(1.0, self.observed_count / self.expected_count)

    def codes(self) -> Tuple[str, ...]:
        return tuple(issue.code for issue in self.issues)


@dataclass(frozen=True)
class MetricResult:
    name: str
    value: Optional[float]
    unit: Optional[str]
    calculation_mode: CalculationMode
    available: bool
    flags: Tuple[str, ...] = ()
    components: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Evidence:
    code: str
    axis: str
    message: str
    observed_metrics: Tuple[str, ...] = ()


@dataclass(frozen=True)
class EvaluationTrace:
    """Reproducibility metadata supplied by the orchestration layer."""

    evaluation_id: str
    engine_version: str
    config_version: str
    evaluated_at: datetime
    observation_keys: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Judgment:
    decision: str
    evidence: Tuple[Evidence, ...] = ()
    counter_evidence: Tuple[Evidence, ...] = ()
    warnings: Tuple[Evidence, ...] = ()
    blockers: Tuple[Evidence, ...] = ()
    invalidation_conditions: Tuple[str, ...] = ()
    quality: DataQualityReport = DataQualityReport()
    confidence_label: str = "UNSPECIFIED"
    trace: Optional[EvaluationTrace] = None


@dataclass(frozen=True)
class AxisSignal:
    axis: str
    status: AxisStatus
    reason_code: str
    message: str


def freeze_sequence(values: Sequence[Any]) -> Tuple[Any, ...]:
    """Make caller-provided lists safe for immutable result objects."""

    return tuple(values)
