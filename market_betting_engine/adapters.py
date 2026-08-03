"""Normalize verified shapes from KIS/Kiwoom read-only quotation responses."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple

from .contracts import (
    CalculationMode,
    Observation,
    ObservationMeta,
    QualityIssue,
    QualitySeverity,
    TradeDateProvenance,
    VerificationStatus,
)
from .session import KST, SessionContext


@dataclass(frozen=True)
class AdapterResult:
    probe_id: str
    observations: Tuple[Observation, ...]
    issues: Tuple[QualityIssue, ...] = ()
    included_rows: int = 0
    excluded_rows: int = 0


@dataclass(frozen=True)
class FieldMapping:
    source_field: str
    metric_suffix: str
    unit: str
    mode: CalculationMode
    absolute_numeric: bool = False


_FIELD_MAPPINGS = {
    "kis_stock_price": (
        FieldMapping("stck_prpr", "price", "KRW", CalculationMode.ACTUAL),
        FieldMapping("stck_oprc", "open", "KRW", CalculationMode.ACTUAL),
        FieldMapping("stck_hgpr", "high", "KRW", CalculationMode.ACTUAL),
        FieldMapping("stck_lwpr", "low", "KRW", CalculationMode.ACTUAL),
        FieldMapping("acml_vol", "cumulative_volume", "shares", CalculationMode.ACTIVITY),
        FieldMapping("acml_tr_pbmn", "cumulative_turnover", "KRW_unverified", CalculationMode.ACTIVITY),
    ),
    "kis_stock_minute": (
        FieldMapping("stck_oprc", "open", "KRW", CalculationMode.ACTUAL),
        FieldMapping("stck_hgpr", "high", "KRW", CalculationMode.ACTUAL),
        FieldMapping("stck_lwpr", "low", "KRW", CalculationMode.ACTUAL),
        FieldMapping("stck_prpr", "close", "KRW", CalculationMode.ACTUAL),
        FieldMapping("cntg_vol", "volume", "shares", CalculationMode.ACTIVITY),
    ),
    "kis_index_minute_kospi": (
        FieldMapping("bstp_nmix_oprc", "open", "index_point", CalculationMode.ACTUAL),
        FieldMapping("bstp_nmix_hgpr", "high", "index_point", CalculationMode.ACTUAL),
        FieldMapping("bstp_nmix_lwpr", "low", "index_point", CalculationMode.ACTUAL),
        FieldMapping("bstp_nmix_prpr", "close", "index_point", CalculationMode.ACTUAL),
        FieldMapping("cntg_vol", "volume", "market_volume_unit", CalculationMode.ACTIVITY),
    ),
    "kis_program_summary_kospi": (
        FieldMapping("whol_smtn_ntby_tr_pbmn", "total_net_amount", "provider_native_amount", CalculationMode.ACTUAL),
        FieldMapping("arbt_smtn_ntby_tr_pbmn", "arbitrage_net_amount", "provider_native_amount", CalculationMode.ACTUAL),
        FieldMapping("nabt_smtn_ntby_tr_pbmn", "non_arbitrage_net_amount", "provider_native_amount", CalculationMode.ACTUAL),
    ),
    "kis_investor_stock": (
        FieldMapping("frgn_ntby_tr_pbmn", "foreign_net_amount", "provider_amount_unit_unverified", CalculationMode.ACTUAL),
        FieldMapping("orgn_ntby_tr_pbmn", "institution_net_amount", "provider_amount_unit_unverified", CalculationMode.ACTUAL),
        FieldMapping("prsn_ntby_tr_pbmn", "individual_net_amount", "provider_amount_unit_unverified", CalculationMode.ACTUAL),
        FieldMapping("frgn_ntby_qty", "foreign_net_quantity", "shares", CalculationMode.ACTUAL),
        FieldMapping("orgn_ntby_qty", "institution_net_quantity", "shares", CalculationMode.ACTUAL),
    ),
    "kis_futures_board": (
        FieldMapping("futs_prpr", "price", "index_point", CalculationMode.ACTUAL),
        FieldMapping("hts_thpr", "theoretical_price", "index_point", CalculationMode.ACTUAL),
        FieldMapping("acml_vol", "cumulative_volume", "contracts", CalculationMode.ACTIVITY),
        FieldMapping("hts_otst_stpl_qty", "open_interest", "contracts", CalculationMode.ACTIVITY),
    ),
    "kis_futures_minute_active": (
        FieldMapping("futs_oprc", "open", "index_point", CalculationMode.ACTUAL),
        FieldMapping("futs_hgpr", "high", "index_point", CalculationMode.ACTUAL),
        FieldMapping("futs_lwpr", "low", "index_point", CalculationMode.ACTUAL),
        FieldMapping("futs_prpr", "close", "index_point", CalculationMode.ACTUAL),
        FieldMapping("cntg_vol", "volume", "contracts", CalculationMode.ACTIVITY),
    ),
    "kiwoom_stock_minute": (
        FieldMapping("open_pric", "open", "KRW", CalculationMode.ACTUAL, True),
        FieldMapping("high_pric", "high", "KRW", CalculationMode.ACTUAL, True),
        FieldMapping("low_pric", "low", "KRW", CalculationMode.ACTUAL, True),
        FieldMapping("cur_prc", "close", "KRW", CalculationMode.ACTUAL, True),
        FieldMapping("trde_qty", "volume", "shares", CalculationMode.ACTIVITY, True),
        FieldMapping("acc_trde_qty", "cumulative_volume", "shares", CalculationMode.ACTIVITY, True),
    ),
}


_CONTAINER = {
    "kis_stock_price": "output",
    "kis_stock_minute": "output2",
    "kis_index_minute_kospi": "output2",
    "kis_program_summary_kospi": "output",
    "kis_investor_stock": "output",
    "kis_futures_board": "output",
    "kis_futures_minute_active": "output2",
    "kiwoom_stock_minute": "stk_min_pole_chart_qry",
}


def _status(value: str | VerificationStatus) -> VerificationStatus:
    if isinstance(value, VerificationStatus):
        return value
    try:
        return VerificationStatus(value)
    except ValueError:
        return VerificationStatus.UNVERIFIED


def _rows(payload: Mapping[str, Any], container: str) -> Sequence[Mapping[str, Any]]:
    value = payload.get(container)
    if isinstance(value, Mapping):
        return (value,)
    if isinstance(value, list):
        return tuple(
            item
            for item in value
            if isinstance(item, Mapping) and "_truncated_items" not in item
        )
    return ()


def _numeric(value: Any, *, absolute: bool) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return abs(number) if absolute else number


def _parse_date(value: Any) -> Optional[date]:
    raw = str(value or "").strip()
    if len(raw) != 8 or not raw.isdigit():
        return None
    try:
        return datetime.strptime(raw, "%Y%m%d").date()
    except ValueError:
        return None


def _row_date_and_time(probe_id: str, row: Mapping[str, Any]) -> tuple[Optional[date], Optional[str]]:
    if probe_id == "kiwoom_stock_minute":
        raw = str(row.get("cntr_tm", "")).strip()
        return (_parse_date(raw[:8]), raw[8:14] if len(raw) >= 14 else None)
    source_date = _parse_date(row.get("stck_bsop_date"))
    source_time = row.get("stck_cntg_hour") or row.get("bsop_hour")
    return source_date, str(source_time).zfill(6) if source_time not in (None, "") else None


def _prefix(probe_id: str, instrument: str) -> str:
    if probe_id in ("kis_stock_price", "kis_stock_minute", "kiwoom_stock_minute"):
        return f"stock.{instrument}"
    if probe_id == "kis_investor_stock":
        return f"investor.stock.{instrument}"
    if probe_id == "kis_index_minute_kospi":
        return "index.KOSPI"
    if probe_id == "kis_program_summary_kospi":
        return "program.KOSPI"
    return f"futures.{instrument}"


def _normal_session_time(probe_id: str, row_time: Optional[str]) -> bool:
    if row_time is None:
        return True
    if probe_id == "kis_futures_minute_active":
        return "084500" <= row_time <= "154500"
    if probe_id in {
        "kis_stock_minute",
        "kis_index_minute_kospi",
        "kis_program_summary_kospi",
        "kiwoom_stock_minute",
    }:
        return "090000" <= row_time <= "153000"
    return True


def adapt_probe_payload(
    probe_id: str,
    payload: Mapping[str, Any],
    *,
    context: SessionContext,
    instrument: str,
    observed_at: Optional[datetime] = None,
    verification_status: str | VerificationStatus = VerificationStatus.PARTIAL,
    field_verification_statuses: Optional[Mapping[str, str | VerificationStatus]] = None,
    environment: str = "production",
    stale_after_seconds: Optional[int] = None,
) -> AdapterResult:
    """Convert one quotation payload and keep only the explicit target date."""

    if probe_id not in _FIELD_MAPPINGS:
        raise KeyError(f"No adapter mapping for {probe_id}")
    retrieved_at = observed_at or context.evaluated_at
    if retrieved_at.tzinfo is None:
        retrieved_at = retrieved_at.replace(tzinfo=KST)
    status = _status(verification_status)
    field_statuses = {
        str(field_name): _status(field_status)
        for field_name, field_status in (field_verification_statuses or {}).items()
    }
    raw_rows = _rows(payload, _CONTAINER[probe_id])
    observations = []
    issues = []
    included = 0
    excluded = 0

    for index, row in enumerate(raw_rows):
        row_date, row_time = _row_date_and_time(probe_id, row)
        provenance = TradeDateProvenance.RESPONSE_FIELD
        if (
            row_date is None
            and context.is_exchange_session_date
            and context.evaluated_at_kst.date() == context.target_trade_date
        ):
            row_date = context.target_trade_date
            provenance = TradeDateProvenance.REQUEST_CONTEXT
        elif row_date is None:
            provenance = TradeDateProvenance.UNKNOWN
        if row_date is not None and row_date != context.target_trade_date:
            excluded += 1
            continue
        if probe_id == "kis_index_minute_kospi" and row_time in {"999999", "888888"}:
            excluded += 1
            issues.append(
                QualityIssue(
                    "SPECIAL_INDEX_TIME_ROW_EXCLUDED",
                    QualitySeverity.INFO,
                    f"special index row {row_time} was not normalized as a minute bar",
                    sources=("KIS",),
                )
            )
            continue
        if not _normal_session_time(probe_id, row_time):
            excluded += 1
            issues.append(
                QualityIssue(
                    "OUT_OF_SESSION_ROW_EXCLUDED",
                    QualitySeverity.INFO,
                    f"{probe_id} row {row_time} is outside the regular-session contract",
                    sources=("KIWOOM" if probe_id.startswith("kiwoom") else "KIS",),
                )
            )
            continue
        included += 1
        time_key = row_time or f"row{index:04d}"
        date_key = f"{row_date:%Y%m%d}" if row_date is not None else "UNKNOWN_DATE"
        base = f"{_prefix(probe_id, instrument)}.{date_key}.{time_key}"
        for mapping in _FIELD_MAPPINGS[probe_id]:
            value = _numeric(row.get(mapping.source_field), absolute=mapping.absolute_numeric)
            if value is None:
                issues.append(
                    QualityIssue(
                        "ADAPTER_FIELD_MISSING_OR_NON_NUMERIC",
                        QualitySeverity.WARNING,
                        f"{probe_id}.{mapping.source_field} could not be normalized",
                        (f"{base}.{mapping.metric_suffix}",),
                        ("KIWOOM" if probe_id.startswith("kiwoom") else "KIS",),
                    )
                )
                continue
            observations.append(
                Observation(
                    metric=f"{base}.{mapping.metric_suffix}",
                    value=value,
                    meta=ObservationMeta(
                        source="KIWOOM" if probe_id.startswith("kiwoom") else "KIS",
                        observed_at=retrieved_at,
                        source_trade_date=row_date,
                        unit=mapping.unit,
                        semantics_status=field_statuses.get(mapping.source_field, status),
                        calculation_mode=mapping.mode,
                        environment=environment,
                        field_name=mapping.source_field,
                        trade_date_provenance=provenance,
                        stale_after_seconds=stale_after_seconds,
                    ),
                )
            )

    if excluded:
        issues.append(
            QualityIssue(
                "NON_TARGET_ROWS_EXCLUDED",
                QualitySeverity.INFO,
                f"excluded {excluded} rows outside the target date or normal minute-bar contract",
                sources=("KIWOOM" if probe_id.startswith("kiwoom") else "KIS",),
            )
        )
    if not raw_rows:
        issues.append(
            QualityIssue(
                "ADAPTER_OUTPUT_EMPTY",
                QualitySeverity.BLOCKING,
                f"{probe_id} response container is empty or missing",
            )
        )
    return AdapterResult(probe_id, tuple(observations), tuple(issues), included, excluded)
