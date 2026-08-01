"""Versioned, human-approved field verification registry."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .contracts import VerificationStatus


@dataclass(frozen=True)
class FieldApproval:
    status: VerificationStatus
    unit: str | None = None
    meaning: str | None = None
    reviewed_at_kst: str | None = None
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProbeApproval:
    contract_status: VerificationStatus
    trade_date_field: str | None
    time_field: str | None
    reviewed_at_kst: str | None
    evidence_refs: tuple[str, ...]
    fields: Mapping[str, FieldApproval]


@dataclass(frozen=True)
class VerificationRegistry:
    registry_version: str
    default_status: VerificationStatus
    probes: Mapping[str, ProbeApproval]

    def statuses_for_probe(self, probe_id: str) -> dict[str, VerificationStatus]:
        """Return field approvals only when the enclosing probe contract is verified."""

        probe = self.probes.get(probe_id)
        if probe is None or probe.contract_status != VerificationStatus.VERIFIED:
            return {}
        return {
            field_name: approval.status
            for field_name, approval in probe.fields.items()
        }


def _status(value: Any, label: str) -> VerificationStatus:
    try:
        return VerificationStatus(str(value))
    except ValueError as error:
        raise ValueError(f"{label} has invalid verification status: {value}") from error


def _evidence(value: Any, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{label}.evidence_refs must be a list of non-empty strings")
    return tuple(value)


def _require_verified_evidence(
    *,
    status: VerificationStatus,
    label: str,
    reviewed_at_kst: Any,
    evidence_refs: tuple[str, ...],
    unit: Any = None,
    meaning: Any = None,
) -> None:
    if status != VerificationStatus.VERIFIED:
        return
    if not isinstance(reviewed_at_kst, str) or not reviewed_at_kst.strip():
        raise ValueError(f"{label} cannot be VERIFIED without reviewed_at_kst")
    if not evidence_refs:
        raise ValueError(f"{label} cannot be VERIFIED without evidence_refs")
    if unit is not None and (not isinstance(unit, str) or not unit.strip() or "unverified" in unit.lower()):
        raise ValueError(f"{label} cannot be VERIFIED with an unverified unit")
    if meaning is not None and (not isinstance(meaning, str) or not meaning.strip()):
        raise ValueError(f"{label} cannot be VERIFIED without a field meaning")


def load_verification_registry(path: str | Path) -> VerificationRegistry:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("verification registry root must be an object")
    version = payload.get("registry_version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("registry_version must be a non-empty string")
    default_status = _status(payload.get("default_status", "PARTIAL"), "default_status")
    if default_status == VerificationStatus.VERIFIED:
        raise ValueError("default_status cannot be VERIFIED; approvals must be explicit")
    raw_probes = payload.get("probes", {})
    if not isinstance(raw_probes, dict):
        raise ValueError("probes must be an object")

    probes: dict[str, ProbeApproval] = {}
    for probe_id, raw_probe in raw_probes.items():
        if not isinstance(raw_probe, dict):
            raise ValueError(f"probes.{probe_id} must be an object")
        label = f"probes.{probe_id}"
        contract_status = _status(raw_probe.get("contract_status", default_status.value), label)
        contract_evidence = _evidence(raw_probe.get("evidence_refs"), label)
        reviewed_at = raw_probe.get("reviewed_at_kst")
        _require_verified_evidence(
            status=contract_status,
            label=label,
            reviewed_at_kst=reviewed_at,
            evidence_refs=contract_evidence,
        )
        raw_fields = raw_probe.get("fields", {})
        if not isinstance(raw_fields, dict):
            raise ValueError(f"{label}.fields must be an object")
        fields: dict[str, FieldApproval] = {}
        for field_name, raw_field in raw_fields.items():
            if not isinstance(raw_field, dict):
                raise ValueError(f"{label}.fields.{field_name} must be an object")
            field_label = f"{label}.fields.{field_name}"
            status = _status(raw_field.get("status", default_status.value), field_label)
            evidence_refs = _evidence(raw_field.get("evidence_refs"), field_label)
            _require_verified_evidence(
                status=status,
                label=field_label,
                reviewed_at_kst=raw_field.get("reviewed_at_kst"),
                evidence_refs=evidence_refs,
                unit=raw_field.get("unit"),
                meaning=raw_field.get("meaning"),
            )
            fields[str(field_name)] = FieldApproval(
                status=status,
                unit=raw_field.get("unit"),
                meaning=raw_field.get("meaning"),
                reviewed_at_kst=raw_field.get("reviewed_at_kst"),
                evidence_refs=evidence_refs,
            )
        probes[str(probe_id)] = ProbeApproval(
            contract_status=contract_status,
            trade_date_field=raw_probe.get("trade_date_field"),
            time_field=raw_probe.get("time_field"),
            reviewed_at_kst=reviewed_at,
            evidence_refs=contract_evidence,
            fields=fields,
        )
    return VerificationRegistry(version, default_status, probes)
