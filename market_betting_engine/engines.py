"""High-level, analysis-only gate skeletons for sector and overnight decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Tuple

from .contracts import (
    AxisSignal,
    AxisStatus,
    CloseNewEntryPermission,
    DataQualityReport,
    Evidence,
    ExistingPositionPermission,
    Judgment,
    SectorState,
)


@dataclass(frozen=True)
class SectorCoverage:
    total_members: int
    observed_members: int
    total_universe_turnover: float
    observed_turnover: float
    leader_turnover: float

    @property
    def member_ratio(self) -> float:
        return self.observed_members / self.total_members if self.total_members > 0 else 0.0

    @property
    def turnover_ratio(self) -> float:
        return self.observed_turnover / self.total_universe_turnover if self.total_universe_turnover > 0 else 0.0

    @property
    def leader_concentration(self) -> float:
        return self.leader_turnover / self.observed_turnover if self.observed_turnover > 0 else 0.0


@dataclass(frozen=True)
class SectorGateConfig:
    minimum_member_coverage_ratio: float = 0.30
    minimum_turnover_coverage_ratio: float = 0.60
    single_name_concentration_warning_ratio: float = 0.50
    hard_veto_independent_failures: int = 2
    placeholder: bool = True


def assess_sector_state(
    signals: Iterable[AxisSignal],
    coverage: SectorCoverage,
    *,
    persistence_confirmed: bool,
    quality: DataQualityReport = DataQualityReport(),
    config: SectorGateConfig = SectorGateConfig(),
) -> Judgment:
    """Assess sector participation without treating a Top-N sample as the market."""

    signal_list = tuple(signals)

    def ev(code: str, axis: str, message: str) -> Evidence:
        return Evidence(code, axis, message)

    warnings = []
    blockers = []
    if coverage.member_ratio < config.minimum_member_coverage_ratio:
        blockers.append(ev("SECTOR_MEMBER_COVERAGE_LOW", "coverage", "sector member coverage is insufficient"))
    if coverage.turnover_ratio < config.minimum_turnover_coverage_ratio:
        blockers.append(ev("SECTOR_TURNOVER_COVERAGE_LOW", "coverage", "sector turnover coverage is insufficient"))
    if coverage.leader_concentration > config.single_name_concentration_warning_ratio:
        warnings.append(ev("SECTOR_SINGLE_NAME_CONCENTRATION", "concentration", "leader dominates observed turnover"))

    failures = {item.axis for item in signal_list if item.status == AxisStatus.FAIL}
    unavailable = [item for item in signal_list if item.status == AxisStatus.UNAVAILABLE]
    axis_warnings = [item for item in signal_list if item.status == AxisStatus.WARNING]
    supporting = tuple(
        ev(item.reason_code, item.axis, item.message) for item in signal_list if item.status == AxisStatus.PASS
    )
    counter = tuple(ev(item.reason_code, item.axis, item.message) for item in signal_list if item.status == AxisStatus.FAIL)
    warnings.extend(ev(item.reason_code, item.axis, item.message) for item in axis_warnings)

    if quality.blocking or blockers or unavailable:
        decision = SectorState.NOT_EVALUABLE
        blockers.extend(ev(item.reason_code, item.axis, item.message) for item in unavailable)
    elif len(failures) >= config.hard_veto_independent_failures:
        decision = SectorState.AVOID
        blockers.extend(counter)
    elif failures:
        decision = SectorState.FADING
    elif warnings:
        decision = SectorState.NEUTRAL
    elif persistence_confirmed:
        decision = SectorState.LEADING
    else:
        decision = SectorState.EMERGING

    return Judgment(
        decision=decision.value,
        evidence=supporting,
        counter_evidence=counter,
        warnings=tuple(warnings),
        blockers=tuple(blockers),
        quality=quality,
        confidence_label="PLACEHOLDER_THRESHOLDS" if config.placeholder else "CONFIGURED_THRESHOLDS",
    )


@dataclass(frozen=True)
class OvernightAssessment:
    close_new_entry: Judgment
    hold_existing: Judgment


@dataclass(frozen=True)
class OvernightGateConfig:
    minimum_available_axes: int = 3
    new_entry_blocking_failure_axes: int = 2
    existing_exit_failure_axes: int = 2
    placeholder: bool = True


def assess_overnight_permissions(
    signals: Iterable[AxisSignal],
    *,
    existing_thesis_valid: bool,
    quality: DataQualityReport = DataQualityReport(),
    config: OvernightGateConfig = OvernightGateConfig(),
) -> OvernightAssessment:
    """Evaluate close-new-entry and hold-existing as separate decisions.

    Profit cushion is intentionally absent: it may size risk elsewhere, but it
    cannot rescue an invalid thesis or poor closing evidence.
    """

    items = tuple(signals)
    available = [item for item in items if item.status != AxisStatus.UNAVAILABLE]
    fail_axes = {item.axis for item in available if item.status == AxisStatus.FAIL}
    warning_items = [item for item in available if item.status == AxisStatus.WARNING]
    unavailable_items = [item for item in items if item.status == AxisStatus.UNAVAILABLE]

    def evidence(item: AxisSignal) -> Evidence:
        return Evidence(item.reason_code, item.axis, item.message)

    support = tuple(evidence(item) for item in available if item.status == AxisStatus.PASS)
    counter = tuple(evidence(item) for item in available if item.status == AxisStatus.FAIL)
    warnings = tuple(evidence(item) for item in warning_items)
    not_evaluable = quality.blocking or len(available) < config.minimum_available_axes

    if not_evaluable:
        new_decision = CloseNewEntryPermission.NOT_EVALUABLE
    elif len(fail_axes) >= config.new_entry_blocking_failure_axes:
        new_decision = CloseNewEntryPermission.BLOCKED
    elif fail_axes or warning_items or unavailable_items:
        new_decision = CloseNewEntryPermission.SELECTIVE
    else:
        new_decision = CloseNewEntryPermission.ALLOWED

    if not_evaluable:
        hold_decision = ExistingPositionPermission.NOT_EVALUABLE
    elif not existing_thesis_valid or len(fail_axes) >= config.existing_exit_failure_axes:
        hold_decision = ExistingPositionPermission.EXIT
    elif fail_axes:
        hold_decision = ExistingPositionPermission.REDUCE
    else:
        hold_decision = ExistingPositionPermission.HOLD

    unavailable_evidence = tuple(evidence(item) for item in unavailable_items)
    common = {
        "evidence": support,
        "counter_evidence": counter,
        "warnings": warnings,
        "quality": quality,
        "confidence_label": "PLACEHOLDER_THRESHOLDS" if config.placeholder else "CONFIGURED_THRESHOLDS",
    }
    new_judgment = Judgment(
        decision=new_decision.value,
        blockers=(counter + unavailable_evidence) if new_decision in (
            CloseNewEntryPermission.BLOCKED,
            CloseNewEntryPermission.NOT_EVALUABLE,
        ) else (),
        **common,
    )
    thesis_blocker = () if existing_thesis_valid else (
        Evidence("EXISTING_THESIS_INVALID", "stock_thesis", "existing position thesis is no longer valid"),
    )
    hold_judgment = Judgment(
        decision=hold_decision.value,
        blockers=(counter + unavailable_evidence + thesis_blocker) if hold_decision in (
            ExistingPositionPermission.EXIT,
            ExistingPositionPermission.NOT_EVALUABLE,
        ) else (),
        **common,
    )
    return OvernightAssessment(new_judgment, hold_judgment)
