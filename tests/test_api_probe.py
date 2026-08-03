import json
import sqlite3
import unittest
from datetime import datetime

from market_betting_engine.api_probe import (
    KST,
    ProbeResult,
    execute_probe,
    infer_schema,
    redact_sensitive,
    save_probe_result,
    validate_read_only_path,
)
from market_betting_engine import api_probe


class ApiProbeTests(unittest.TestCase):
    def test_post_close_stock_minute_request_is_anchored_at_regular_close(self):
        class FakeClient:
            def __init__(self):
                self.params = None

            def get(self, path, operation_code, params):
                self.params = params
                return {"rt_cd": "0", "output2": []}, 200

        client = FakeClient()
        original = api_probe._KIS_CLIENT
        api_probe._KIS_CLIENT = client
        try:
            api_probe._kis_request(
                api_probe.PROBE_SPECS["kis_stock_minute"],
                "005930",
                datetime(2026, 8, 3, 23, 26, tzinfo=KST),
            )
        finally:
            api_probe._KIS_CLIENT = original

        self.assertEqual(client.params["FID_INPUT_HOUR_1"], "153000")

    def test_intraday_stock_minute_request_keeps_current_time(self):
        class FakeClient:
            def __init__(self):
                self.params = None

            def get(self, path, operation_code, params):
                self.params = params
                return {"rt_cd": "0", "output2": []}, 200

        client = FakeClient()
        original = api_probe._KIS_CLIENT
        api_probe._KIS_CLIENT = client
        try:
            api_probe._kis_request(
                api_probe.PROBE_SPECS["kis_stock_minute"],
                "005930",
                datetime(2026, 8, 3, 12, 5, 7, tzinfo=KST),
            )
        finally:
            api_probe._KIS_CLIENT = original

        self.assertEqual(client.params["FID_INPUT_HOUR_1"], "120507")

    def test_redaction_removes_nested_credentials_and_bearer_tokens(self):
        payload = {
            "authorization": "Bearer abc.secret.token",
            "nested": {
                "appsecret": "secret-value",
                "message": "failed with Bearer visible-token",
                "market_value": "12345",
            },
        }
        redacted = redact_sensitive(payload)
        rendered = json.dumps(redacted)
        self.assertNotIn("abc.secret.token", rendered)
        self.assertNotIn("secret-value", rendered)
        self.assertNotIn("visible-token", rendered)
        self.assertEqual(redacted["nested"]["market_value"], "12345")

    def test_redaction_truncates_large_lists(self):
        redacted = redact_sensitive({"output": [{"x": i} for i in range(5)]}, max_list_items=2)
        self.assertEqual(redacted["output"][-1], {"_truncated_items": 3})

    def test_schema_records_structure_without_values(self):
        schema = infer_schema({"output": [{"price": "100", "volume": 2}]})
        self.assertEqual(schema["fields"]["output"]["length"], 1)
        self.assertEqual(
            schema["fields"]["output"]["item"]["fields"]["price"], {"type": "string"}
        )
        self.assertNotIn("100", json.dumps(schema))

    def test_read_only_allowlist_rejects_order_and_unknown_paths(self):
        validate_read_only_path("KIS", "/uapi/domestic-stock/v1/quotations/inquire-price")
        with self.assertRaisesRegex(ValueError, "blocked"):
            validate_read_only_path("KIS", "/uapi/domestic-stock/v1/trading/order-cash")
        with self.assertRaisesRegex(ValueError, "allow-list"):
            validate_read_only_path("KIS", "/uapi/domestic-stock/v1/quotations/unknown")

    def test_successful_probe_is_partial_until_semantics_and_units_are_reviewed(self):
        def request_override(spec, ticker, current):
            self.assertEqual(ticker, "005930")
            return {
                "rt_cd": "0",
                "msg1": "success",
                "output": {
                    "stck_prpr": "100", "stck_oprc": "99", "stck_hgpr": "101",
                    "stck_lwpr": "98", "acml_vol": "1000",
                },
            }, 200

        result = execute_probe("kis_stock_price", request_override=request_override)
        self.assertEqual(result.execution_status, "SUCCESS")
        self.assertEqual(result.verification_status, "PARTIAL")
        self.assertEqual(result.output_row_count, 1)
        self.assertEqual(result.missing_expected_fields, [])

    def test_schema_mismatch_is_saved_without_promoting_verification(self):
        def request_override(spec, ticker, current):
            return {"rt_cd": "0", "output": {"stck_prpr": "100"}}, 200

        result = execute_probe("kis_stock_price", request_override=request_override)
        self.assertEqual(result.execution_status, "SCHEMA_MISMATCH")
        self.assertIn("stck_oprc", result.missing_expected_fields)
        self.assertEqual(result.verification_status, "PARTIAL")

    def test_probe_extracts_source_trade_date_and_time(self):
        def request_override(spec, ticker, current):
            return {
                "rt_cd": "0",
                "output2": [{
                    "stck_bsop_date": "20260731", "stck_cntg_hour": "153000",
                    "stck_prpr": "100", "stck_oprc": "99", "stck_hgpr": "101",
                    "stck_lwpr": "98", "cntg_vol": "10",
                }],
            }, 200

        result = execute_probe(
            "kis_stock_minute",
            current=datetime(2026, 7, 31, 15, 30, tzinfo=KST),
            request_override=request_override,
        )
        self.assertEqual(result.source_trade_dates, ["20260731"])
        self.assertEqual(result.source_times, ["153000"])
        self.assertEqual(result.contract_review_status, "REVIEW_READY")
        self.assertTrue(all(check["passed"] for check in result.contract_checks))

    def test_kis_market_probe_is_skipped_on_weekend_without_force(self):
        calls = []

        def request_override(spec, ticker, current):
            calls.append(spec.probe_id)
            return {}, 200

        result = execute_probe(
            "kis_stock_minute",
            current=datetime(2026, 8, 1, 12, 0, tzinfo=KST),
            request_override=request_override,
        )
        self.assertEqual(result.execution_status, "SKIPPED")
        self.assertEqual(result.verification_status, "PENDING_MARKET_SESSION")
        self.assertEqual(calls, [])

    def test_weekend_market_session_probe_is_skipped_without_network_call(self):
        calls = []

        def request_override(spec, ticker, current):
            calls.append(spec.probe_id)
            return {}, 200

        result = execute_probe(
            "kiwoom_program_basis_current_session",
            current=datetime(2026, 8, 1, 12, 0, tzinfo=KST),
            request_override=request_override,
        )
        self.assertEqual(result.execution_status, "SKIPPED")
        self.assertEqual(result.verification_status, "PENDING_MARKET_SESSION")
        self.assertEqual(calls, [])

    def test_non_executable_ws_probe_is_pending_market_session(self):
        result = execute_probe("kis_trade_ws")
        self.assertEqual(result.execution_status, "SKIPPED")
        self.assertEqual(result.verification_status, "PENDING_MARKET_SESSION")

    def test_probe_result_persists_to_dedicated_table(self):
        import tempfile
        from pathlib import Path

        result = ProbeResult(
            run_id="run-1", probe_id="probe", provider="KIS", transport="REST",
            started_at_kst="2026-08-01T12:00:00+09:00",
            completed_at_kst="2026-08-01T12:00:01+09:00",
            execution_status="SUCCESS", verification_status="PARTIAL",
            endpoint="/read-only", operation_code="TR", observed_fields=["field"],
            schema={"type": "object"}, sanitized_sample={"field": "value"},
        )
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "probe.db"
            save_probe_result(result, db_path)
            conn = sqlite3.connect(db_path)
            try:
                row = conn.execute(
                    "SELECT execution_status, verification_status, sanitized_sample_json "
                    "FROM api_probe_runs WHERE run_id='run-1'"
                ).fetchone()
            finally:
                conn.close()
        self.assertEqual(row[:2], ("SUCCESS", "PARTIAL"))
        self.assertEqual(json.loads(row[2]), {"field": "value"})


if __name__ == "__main__":
    unittest.main()
