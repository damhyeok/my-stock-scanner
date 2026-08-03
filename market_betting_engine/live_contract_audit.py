"""Deterministic live-session checks for quotation probe contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence


AUDITED_PROBES = {
    "kis_stock_minute",
    "kis_index_minute_kospi",
    "kis_program_summary_kospi",
    "kis_futures_minute_active",
}
SPECIAL_TIMES = {"888888", "999999"}


@dataclass(frozen=True)
class ContractCheck:
    code: str
    passed: bool
    message: str


@dataclass(frozen=True)
class ContractAudit:
    status: str
    checks: tuple[ContractCheck, ...]

    def as_dicts(self) -> list[dict[str, Any]]:
        return [asdict(check) for check in self.checks]


def _number(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _time_in_range(raw: Any, lower: str, upper: str) -> bool:
    value = str(raw or "").strip().zfill(6)
    return len(value) == 6 and value.isdigit() and lower <= value <= upper


def _ohlc_check(
    rows: Sequence[Mapping[str, Any]], fields: tuple[str, str, str, str]
) -> ContractCheck:
    invalid = 0
    checked = 0
    for row in rows:
        values = [_number(row.get(field)) for field in fields]
        if any(value is None for value in values):
            invalid += 1
            continue
        open_price, high, low, close = values
        checked += 1
        if low > min(open_price, close) or high < max(open_price, close) or low > high:
            invalid += 1
    return ContractCheck(
        "OHLC_INVARIANTS",
        checked > 0 and invalid == 0,
        f"checked={checked}, invalid={invalid}",
    )


def audit_live_contract(
    probe_id: str,
    rows: Sequence[Mapping[str, Any]],
    started_at_kst: datetime,
) -> ContractAudit:
    if probe_id not in AUDITED_PROBES:
        return ContractAudit("NOT_APPLICABLE", ())
    checks = []
    live_request = (
        started_at_kst.weekday() < 5
        and _time_in_range(started_at_kst.strftime("%H%M%S"), "090000", "153000")
    )
    checks.append(
        ContractCheck(
            "LIVE_SESSION_REQUEST",
            live_request,
            f"requested_at={started_at_kst.isoformat(timespec='seconds')}",
        )
    )
    checks.append(ContractCheck("NON_EMPTY_OUTPUT", bool(rows), f"rows={len(rows)}"))

    time_field = "bsop_hour" if probe_id == "kis_program_summary_kospi" else "stck_cntg_hour"
    non_special_rows = [
        row for row in rows
        if str(row.get(time_field, "")).zfill(6) not in SPECIAL_TIMES
    ]
    expected_date = started_at_kst.strftime("%Y%m%d")
    if probe_id == "kis_program_summary_kospi":
        normal_rows = non_special_rows
        prior_date_rows = 0
    else:
        # KIS can append prior-session rows even when the requested current-day
        # window is present. The runtime excludes them, so contract review must
        # validate the same target-date subset instead of rejecting the payload.
        normal_rows = [
            row for row in non_special_rows
            if str(row.get("stck_bsop_date", "")) == expected_date
        ]
        prior_date_rows = len(non_special_rows) - len(normal_rows)
    upper = "154500" if probe_id == "kis_futures_minute_active" else "153000"
    invalid_times = [
        str(row.get(time_field, ""))
        for row in normal_rows
        if not _time_in_range(row.get(time_field), "084500" if probe_id == "kis_futures_minute_active" else "090000", upper)
    ]
    checks.append(
        ContractCheck(
            "NORMAL_MARKET_TIMES",
            bool(normal_rows) and not invalid_times,
            f"normal_rows={len(normal_rows)}, invalid_times={len(invalid_times)}, "
            f"special_rows={len(rows) - len(non_special_rows)}, prior_date_rows={prior_date_rows}",
        )
    )

    if probe_id != "kis_program_summary_kospi":
        dates = {str(row.get("stck_bsop_date", "")) for row in normal_rows}
        checks.append(
            ContractCheck(
                "SOURCE_TRADE_DATE_MATCH",
                dates == {expected_date},
                f"expected={expected_date}, observed={sorted(dates)}",
            )
        )
    else:
        checks.append(
            ContractCheck(
                "PROGRAM_DATE_REQUEST_CONTEXT_ONLY",
                live_request,
                "response has no trade-date field; date must remain request-context provenance",
            )
        )

    ohlc_fields = {
        "kis_stock_minute": ("stck_oprc", "stck_hgpr", "stck_lwpr", "stck_prpr"),
        "kis_index_minute_kospi": (
            "bstp_nmix_oprc", "bstp_nmix_hgpr", "bstp_nmix_lwpr", "bstp_nmix_prpr",
        ),
        "kis_futures_minute_active": ("futs_oprc", "futs_hgpr", "futs_lwpr", "futs_prpr"),
    }
    if probe_id in ohlc_fields:
        checks.append(_ohlc_check(normal_rows, ohlc_fields[probe_id]))
    else:
        required = (
            "whol_smtn_ntby_tr_pbmn",
            "arbt_smtn_ntby_tr_pbmn",
            "nabt_smtn_ntby_tr_pbmn",
        )
        numeric_rows = sum(
            all(_number(row.get(field)) is not None for field in required)
            for row in normal_rows
        )
        checks.append(
            ContractCheck(
                "PROGRAM_FLOW_FIELDS_NUMERIC",
                numeric_rows == len(normal_rows) and numeric_rows > 0,
                f"numeric_rows={numeric_rows}, normal_rows={len(normal_rows)}",
            )
        )

    if not live_request:
        status = "PENDING_LIVE_SESSION"
    elif all(check.passed for check in checks):
        status = "REVIEW_READY"
    else:
        status = "BLOCKED"
    return ContractAudit(status, tuple(checks))
