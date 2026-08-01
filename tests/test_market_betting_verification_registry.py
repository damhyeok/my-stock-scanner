import json
import tempfile
import unittest
from pathlib import Path

from market_betting_engine.adapters import adapt_probe_payload
from market_betting_engine.contracts import VerificationStatus
from market_betting_engine.session import SessionContext
from market_betting_engine.verification_registry import load_verification_registry
from datetime import date, datetime
from market_betting_engine.session import KST


class VerificationRegistryTests(unittest.TestCase):
    def write_registry(self, payload):
        temporary = tempfile.TemporaryDirectory()
        path = Path(temporary.name) / "registry.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        self.addCleanup(temporary.cleanup)
        return path

    def test_repository_registry_defaults_to_partial_without_approvals(self):
        path = Path(__file__).resolve().parents[1] / "config" / "market_betting_field_verification.json"
        registry = load_verification_registry(path)
        self.assertEqual(registry.default_status, VerificationStatus.PARTIAL)
        self.assertEqual(registry.statuses_for_probe("kis_stock_minute"), {})

    def test_verified_contract_requires_review_time_and_evidence(self):
        path = self.write_registry({
            "registry_version": "bad-v1",
            "default_status": "PARTIAL",
            "probes": {"kis_stock_minute": {"contract_status": "VERIFIED", "fields": {}}},
        })
        with self.assertRaisesRegex(ValueError, "reviewed_at_kst"):
            load_verification_registry(path)

    def test_explicit_verified_field_is_exposed_only_under_verified_contract(self):
        field = {
            "status": "VERIFIED",
            "unit": "KRW",
            "meaning": "one-minute opening price",
            "reviewed_at_kst": "2026-08-03T15:40:00+09:00",
            "evidence_refs": ["probe_report_20260803_152500.json"],
        }
        path = self.write_registry({
            "registry_version": "approved-v1",
            "default_status": "PARTIAL",
            "probes": {
                "kis_stock_minute": {
                    "contract_status": "VERIFIED",
                    "trade_date_field": "stck_bsop_date",
                    "time_field": "stck_cntg_hour",
                    "reviewed_at_kst": "2026-08-03T15:40:00+09:00",
                    "evidence_refs": ["probe_report_20260803_094500.json", "probe_report_20260803_152500.json"],
                    "fields": {"stck_oprc": field},
                }
            },
        })
        registry = load_verification_registry(path)
        self.assertEqual(
            registry.statuses_for_probe("kis_stock_minute")["stck_oprc"],
            VerificationStatus.VERIFIED,
        )

    def test_adapter_applies_approval_per_source_field(self):
        context = SessionContext(
            date(2026, 8, 3),
            datetime(2026, 8, 3, 10, 0, tzinfo=KST),
            True,
            "TEST",
        )
        payload = {"output2": [{
            "stck_bsop_date": "20260803", "stck_cntg_hour": "100000",
            "stck_oprc": "100", "stck_hgpr": "110", "stck_lwpr": "90",
            "stck_prpr": "105", "cntg_vol": "10", "acml_tr_pbmn": "1000",
        }]}
        adapted = adapt_probe_payload(
            "kis_stock_minute",
            payload,
            context=context,
            instrument="005930",
            verification_status=VerificationStatus.PARTIAL,
            field_verification_statuses={"stck_oprc": VerificationStatus.VERIFIED},
        )
        statuses = {item.meta.field_name: item.meta.semantics_status for item in adapted.observations}
        self.assertEqual(statuses["stck_oprc"], VerificationStatus.VERIFIED)
        self.assertEqual(statuses["stck_prpr"], VerificationStatus.PARTIAL)


if __name__ == "__main__":
    unittest.main()
