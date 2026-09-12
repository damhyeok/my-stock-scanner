"""In-memory bridge from allow-listed probes to normalized observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping, Optional

from .adapters import AdapterResult, adapt_probe_payload
from .api_probe import ProbeResult, ProbeSpec, execute_probe, _kis_sector_stock_window_request
from .contracts import QualityIssue, QualitySeverity
from .session import SessionContext


@dataclass(frozen=True)
class ProbeCollectionResult:
    probe: ProbeResult
    adapted: AdapterResult
    sector_adapted: Optional[AdapterResult] = None


def collect_probe_observations(
    probe_id: str,
    *,
    context: SessionContext,
    instrument: str,
    ticker: str = "005930",
    stale_after_seconds: Optional[int] = None,
    force_session_probe: bool = False,
    request_override: Optional[
        Callable[[ProbeSpec, str, datetime], tuple[dict[str, Any], int]]
    ] = None,
    field_verification_statuses: Optional[Mapping[str, str]] = None,
    sector_window_minutes: Optional[int] = None,
) -> ProbeCollectionResult:
    """Execute one safe probe and adapt its raw payload without persisting it.

    Only the existing allow-listed probe implementation performs network I/O.
    The raw response is handed to the adapter in memory; normal probe reports
    remain redacted and bounded.
    """

    captured: dict[str, Mapping[str, Any]] = {}

    def capture(payload: Mapping[str, Any]) -> None:
        captured["payload"] = payload

    if probe_id == "kis_stock_minute" and sector_window_minutes and request_override is None:
        def window_request(spec, ticker, current):
            return _kis_sector_stock_window_request(
                spec, ticker, current, minutes=sector_window_minutes,
                initial_consumer=lambda payload: captured.update(initial=payload),
            )
        request_override = window_request

    probe = execute_probe(
        probe_id,
        ticker=ticker,
        force_session_probe=force_session_probe,
        current=context.evaluated_at,
        request_override=request_override,
        payload_consumer=capture,
    )
    payload = captured.get("payload")
    if payload is None:
        adapted = AdapterResult(
            probe_id=probe_id,
            observations=(),
            issues=(
                QualityIssue(
                    "COLLECTION_PAYLOAD_UNAVAILABLE",
                    QualitySeverity.BLOCKING,
                    f"{probe_id} produced no adaptable payload ({probe.execution_status})",
                ),
            ),
        )
    else:
        adapted = adapt_probe_payload(
            probe_id,
            payload,
            context=context,
            instrument=instrument,
            observed_at=datetime.fromisoformat(probe.completed_at_kst),
            verification_status=probe.verification_status,
            field_verification_statuses=field_verification_statuses,
            stale_after_seconds=stale_after_seconds,
        )
    sector_adapted = None
    if "initial" in captured:
        sector_adapted = adapted
        adapted = adapt_probe_payload(
            probe_id, captured["initial"], context=context, instrument=instrument,
            observed_at=datetime.fromisoformat(probe.completed_at_kst),
            verification_status=probe.verification_status,
            field_verification_statuses=field_verification_statuses,
            stale_after_seconds=stale_after_seconds,
        )
    return ProbeCollectionResult(probe, adapted, sector_adapted)
