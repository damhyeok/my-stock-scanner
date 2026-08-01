import unittest
from datetime import datetime

from market_betting_engine.api_probe import KST
from market_betting_engine.live_contract_audit import audit_live_contract


LIVE = datetime(2026, 7, 31, 12, 0, tzinfo=KST)


class LiveContractAuditTests(unittest.TestCase):
    def test_weekend_or_after_hours_sample_cannot_be_review_ready(self):
        rows = [{
            "stck_bsop_date": "20260731", "stck_cntg_hour": "223000",
            "stck_oprc": "100", "stck_hgpr": "101", "stck_lwpr": "99",
            "stck_prpr": "100",
        }]
        result = audit_live_contract(
            "kis_stock_minute", rows, datetime(2026, 8, 1, 22, 30, tzinfo=KST)
        )
        self.assertEqual(result.status, "PENDING_LIVE_SESSION")
        self.assertFalse(next(c for c in result.checks if c.code == "NORMAL_MARKET_TIMES").passed)

    def test_special_index_row_is_excluded_from_normal_time_check(self):
        rows = [
            {
                "stck_bsop_date": "20260731", "stck_cntg_hour": "120000",
                "bstp_nmix_oprc": "3000", "bstp_nmix_hgpr": "3010",
                "bstp_nmix_lwpr": "2990", "bstp_nmix_prpr": "3005",
            },
            {
                "stck_bsop_date": "20260731", "stck_cntg_hour": "999999",
                "bstp_nmix_oprc": "0", "bstp_nmix_hgpr": "0",
                "bstp_nmix_lwpr": "0", "bstp_nmix_prpr": "0",
            },
        ]
        result = audit_live_contract("kis_index_minute_kospi", rows, LIVE)
        self.assertEqual(result.status, "REVIEW_READY")
        time_check = next(c for c in result.checks if c.code == "NORMAL_MARKET_TIMES")
        self.assertIn("special_rows=1", time_check.message)

    def test_bad_ohlc_blocks_contract_review(self):
        rows = [{
            "stck_bsop_date": "20260731", "stck_cntg_hour": "120000",
            "futs_oprc": "100", "futs_hgpr": "99", "futs_lwpr": "98",
            "futs_prpr": "100",
        }]
        result = audit_live_contract("kis_futures_minute_active", rows, LIVE)
        self.assertEqual(result.status, "BLOCKED")
        self.assertFalse(next(c for c in result.checks if c.code == "OHLC_INVARIANTS").passed)

    def test_program_contract_records_request_context_date_limitation(self):
        rows = [{
            "bsop_hour": "120000", "whol_smtn_ntby_tr_pbmn": "10",
            "arbt_smtn_ntby_tr_pbmn": "4", "nabt_smtn_ntby_tr_pbmn": "6",
        }]
        result = audit_live_contract("kis_program_summary_kospi", rows, LIVE)
        self.assertEqual(result.status, "REVIEW_READY")
        self.assertTrue(any(c.code == "PROGRAM_DATE_REQUEST_CONTEXT_ONLY" for c in result.checks))


if __name__ == "__main__":
    unittest.main()
