import tempfile
import unittest
from pathlib import Path

from market_betting_engine.api_probe import ProbeResult, save_probe_result
from market_betting_engine.verification_readiness import REQUIRED_PROBES, build_readiness


class VerificationReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "probe.db"

    def tearDown(self):
        self.temp.cleanup()

    def save(self, probe, clock, status="REVIEW_READY", passed=True):
        save_probe_result(
            ProbeResult(
                run_id=f"{probe}-{clock}",
                probe_id=probe,
                provider="KIS",
                transport="REST",
                started_at_kst=f"2026-07-31T{clock}:00+09:00",
                completed_at_kst=f"2026-07-31T{clock}:01+09:00",
                execution_status="SUCCESS",
                verification_status="PARTIAL",
                endpoint="/read-only",
                operation_code="TR",
                contract_review_status=status,
                contract_checks=[{"code": "CHECK", "passed": passed, "message": "evidence"}],
            ),
            self.db_path,
        )

    def test_three_live_windows_are_required_for_each_probe(self):
        for probe in REQUIRED_PROBES:
            for clock in ("09:45", "12:00", "15:25"):
                self.save(probe, clock)
        result = build_readiness(self.db_path)
        self.assertEqual(result["overall_status"], "READY_FOR_MANUAL_REVIEW")
        self.assertFalse(result["auto_promotes_registry"])

    def test_failed_or_missing_checkpoint_stays_pending(self):
        for probe in REQUIRED_PROBES:
            self.save(probe, "09:45")
            self.save(probe, "12:00")
        self.save(REQUIRED_PROBES[0], "15:25", status="BLOCKED", passed=False)
        result = build_readiness(self.db_path)
        self.assertEqual(result["overall_status"], "PENDING_CHECKPOINTS")
        self.assertIn("CLOSE", result["probes"][REQUIRED_PROBES[1]]["missing_checkpoints"])


if __name__ == "__main__":
    unittest.main()
