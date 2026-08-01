"""Data freshness and cross-source consistency checks."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Iterable, Optional, Sequence

from .contracts import (
    DataQualityReport,
    Observation,
    QualityIssue,
    QualitySeverity,
    VerificationStatus,
)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def evaluate_observations(
    observations: Sequence[Observation],
    *,
    target_trade_date: Optional[date],
    now: Optional[datetime] = None,
    expected_metrics: Optional[Iterable[str]] = None,
    require_verified_semantics: bool = False,
    require_source_trade_date: bool = False,
) -> DataQualityReport:
    """Validate raw observations without guessing an exchange business date.

    ``target_trade_date`` must be supplied by the session/calendar layer.  This
    prevents a weekend or holiday from being mistaken for a stale trading day.
    """

    current = _aware_utc(now or datetime.now(timezone.utc))
    issues = []
    seen_metrics = {item.metric for item in observations}

    for item in observations:
        meta = item.meta
        if item.value is None:
            issues.append(
                QualityIssue(
                    "MISSING_VALUE",
                    QualitySeverity.BLOCKING,
                    f"{item.metric} has no value",
                    (item.metric,),
                    (meta.source,),
                )
            )
        if meta.source_trade_date is None:
            issues.append(
                QualityIssue(
                    "SOURCE_TRADE_DATE_MISSING",
                    QualitySeverity.BLOCKING if require_source_trade_date else QualitySeverity.WARNING,
                    f"{item.metric} has no source trade date",
                    (item.metric,),
                    (meta.source,),
                )
            )
        elif target_trade_date is not None and meta.source_trade_date != target_trade_date:
            issues.append(
                QualityIssue(
                    "SOURCE_TRADE_DATE_MISMATCH",
                    QualitySeverity.BLOCKING,
                    f"{item.metric} is dated {meta.source_trade_date}, expected {target_trade_date}",
                    (item.metric,),
                    (meta.source,),
                )
            )
        if meta.stale_after_seconds is not None:
            age = (current - _aware_utc(meta.observed_at)).total_seconds()
            if age > meta.stale_after_seconds:
                issues.append(
                    QualityIssue(
                        "OBSERVATION_STALE",
                        QualitySeverity.BLOCKING,
                        f"{item.metric} is {age:.1f}s old (limit {meta.stale_after_seconds}s)",
                        (item.metric,),
                        (meta.source,),
                    )
                )
            elif age < -1:
                issues.append(
                    QualityIssue(
                        "OBSERVATION_FROM_FUTURE",
                        QualitySeverity.BLOCKING,
                        f"{item.metric} timestamp is ahead of evaluator clock",
                        (item.metric,),
                        (meta.source,),
                    )
                )
        if meta.semantics_status == VerificationStatus.PARTIAL:
            issues.append(
                QualityIssue(
                    "FIELD_SEMANTICS_PARTIAL",
                    QualitySeverity.BLOCKING if require_verified_semantics else QualitySeverity.WARNING,
                    f"{item.metric} field semantics or units are only partially verified",
                    (item.metric,),
                    (meta.source,),
                )
            )
        elif meta.semantics_status == VerificationStatus.UNVERIFIED:
            issues.append(
                QualityIssue(
                    "FIELD_SEMANTICS_UNVERIFIED",
                    QualitySeverity.BLOCKING if require_verified_semantics else QualitySeverity.WARNING,
                    f"{item.metric} field semantics are unverified",
                    (item.metric,),
                    (meta.source,),
                )
            )
        elif meta.semantics_status == VerificationStatus.UNAVAILABLE:
            issues.append(
                QualityIssue(
                    "SOURCE_UNAVAILABLE",
                    QualitySeverity.BLOCKING,
                    f"{item.metric} source is unavailable",
                    (item.metric,),
                    (meta.source,),
                )
            )

    dated = {item.meta.source_trade_date for item in observations if item.meta.source_trade_date}
    if len(dated) > 1:
        issues.append(
            QualityIssue(
                "CROSS_SOURCE_TRADE_DATE_CONFLICT",
                QualitySeverity.BLOCKING,
                "observations contain multiple source trade dates",
                tuple(sorted(seen_metrics)),
                tuple(sorted({item.meta.source for item in observations})),
            )
        )

    expected = set(expected_metrics) if expected_metrics is not None else None
    if expected is not None:
        for metric in sorted(expected - seen_metrics):
            issues.append(
                QualityIssue(
                    "EXPECTED_METRIC_MISSING",
                    QualitySeverity.BLOCKING,
                    f"required metric {metric} is missing",
                    (metric,),
                )
            )

    return DataQualityReport(
        issues=tuple(issues),
        observed_count=len(seen_metrics),
        expected_count=len(expected) if expected is not None else None,
    )


def combine_quality(*reports: DataQualityReport) -> DataQualityReport:
    """Combine reports while de-duplicating identical issue records."""

    issues = []
    seen = set()
    for report in reports:
        for issue in report.issues:
            key = (issue.code, issue.severity, issue.metrics, issue.sources, issue.message)
            if key not in seen:
                issues.append(issue)
                seen.add(key)
    expected_values = [r.expected_count for r in reports if r.expected_count is not None]
    return DataQualityReport(
        issues=tuple(issues),
        observed_count=sum(r.observed_count for r in reports),
        expected_count=sum(expected_values) if expected_values else None,
    )
