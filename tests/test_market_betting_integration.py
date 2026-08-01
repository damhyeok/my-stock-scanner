import unittest
from datetime import date, datetime, timedelta, timezone

from market_betting_engine.adapters import adapt_probe_payload
from market_betting_engine.contracts import (
    AxisSignal,
    AxisStatus,
    TradeDateProvenance,
    VerificationStatus,
    StockState,
)
from market_betting_engine.collector import collect_probe_observations
from market_betting_engine.engines import SectorCoverage
from market_betting_engine.orchestrator import (
    SectorDecisionInput,
    StockDecisionInput,
    run_intraday_decision_cycle,
)
from market_betting_engine.quality import evaluate_observations
from market_betting_engine.session import KST, SessionContext, SessionPhase
from market_betting_engine.states import StockGateSignals


TARGET = date(2026, 7, 31)
NOW = datetime(2026, 7, 31, 14, 45, tzinfo=KST)
CONTEXT = SessionContext(TARGET, NOW, True, "TEST_CALENDAR")


def signal(axis, status=AxisStatus.PASS):
    return AxisSignal(axis, status, f"{axis}_{status.value}", f"{axis} {status.value}")


class SessionTests(unittest.TestCase):
    def test_closing_periods_are_isolated(self):
        cases = [
            ((14, 29), SessionPhase.CONTINUOUS),
            ((14, 30), SessionPhase.PRE_CLOSE_FLOW),
            ((15, 0), SessionPhase.CLOSE_CONTINUITY),
            ((15, 20), SessionPhase.CLOSING_AUCTION),
            ((15, 30), SessionPhase.POST_CLOSE),
        ]
        for (hour, minute), expected in cases:
            context = SessionContext(TARGET, datetime(2026, 7, 31, hour, minute, tzinfo=KST), True, "TEST")
            self.assertEqual(context.phase, expected)

    def test_holiday_status_is_explicit_not_weekday_guess(self):
        context = SessionContext(TARGET, NOW, False, "EXCHANGE_CALENDAR")
        self.assertEqual(context.phase, SessionPhase.NON_SESSION)


class AdapterTests(unittest.TestCase):
    def test_kis_minute_keeps_only_target_trade_date(self):
        payload = {
            "output2": [
                {"stck_bsop_date": "20260731", "stck_cntg_hour": "153000", "stck_oprc": "100", "stck_hgpr": "110", "stck_lwpr": "90", "stck_prpr": "105", "cntg_vol": "10", "acml_tr_pbmn": "1000"},
                {"stck_bsop_date": "20260730", "stck_cntg_hour": "153000", "stck_oprc": "90", "stck_hgpr": "100", "stck_lwpr": "80", "stck_prpr": "95", "cntg_vol": "20", "acml_tr_pbmn": "2000"},
            ]
        }
        result = adapt_probe_payload("kis_stock_minute", payload, context=CONTEXT, instrument="005930")
        self.assertEqual(result.included_rows, 1)
        self.assertEqual(result.excluded_rows, 1)
        self.assertEqual(len(result.observations), 6)
        self.assertTrue(all(item.meta.source_trade_date == TARGET for item in result.observations))

    def test_index_special_time_rows_are_excluded(self):
        common = {"stck_bsop_date": "20260731", "bstp_nmix_oprc": "1", "bstp_nmix_hgpr": "2", "bstp_nmix_lwpr": "1", "bstp_nmix_prpr": "2", "cntg_vol": "3", "acml_tr_pbmn": "4"}
        payload = {"output2": [dict(common, stck_cntg_hour="999999"), dict(common, stck_cntg_hour="152900")]}
        result = adapt_probe_payload("kis_index_minute_kospi", payload, context=CONTEXT, instrument="KOSPI")
        self.assertEqual(result.included_rows, 1)
        self.assertEqual(result.excluded_rows, 1)
        self.assertTrue(any(issue.code == "SPECIAL_INDEX_TIME_ROW_EXCLUDED" for issue in result.issues))

    def test_kiwoom_signed_price_prefix_is_normalized_to_absolute_price(self):
        payload = {"stk_min_pole_chart_qry": [{"cntr_tm": "20260731153000", "cur_prc": "+262500", "open_pric": "-260000", "high_pric": "+263000", "low_pric": "-259000", "trde_qty": "10", "acc_trde_qty": "100"}]}
        result = adapt_probe_payload("kiwoom_stock_minute", payload, context=CONTEXT, instrument="005930")
        values = {item.meta.field_name: item.value for item in result.observations}
        self.assertEqual(values["cur_prc"], 262500.0)
        self.assertEqual(values["open_pric"], 260000.0)

    def test_program_date_is_marked_as_request_context(self):
        payload = {"output": [{"bsop_hour": "150000", "whol_smtn_ntby_tr_pbmn": "30", "arbt_smtn_ntby_tr_pbmn": "10", "nabt_smtn_ntby_tr_pbmn": "20"}]}
        result = adapt_probe_payload("kis_program_summary_kospi", payload, context=CONTEXT, instrument="KOSPI")
        self.assertTrue(result.observations)
        self.assertTrue(all(item.meta.trade_date_provenance == TradeDateProvenance.REQUEST_CONTEXT for item in result.observations))

    def test_partial_verification_propagates_quality_warning(self):
        payload = {"output": {"stck_prpr": "100", "stck_oprc": "90", "stck_hgpr": "110", "stck_lwpr": "80", "acml_vol": "10", "acml_tr_pbmn": "1000"}}
        adapted = adapt_probe_payload("kis_stock_price", payload, context=CONTEXT, instrument="005930", verification_status=VerificationStatus.PARTIAL)
        report = evaluate_observations(adapted.observations, target_trade_date=TARGET, now=NOW)
        self.assertFalse(report.blocking)
        self.assertIn("FIELD_SEMANTICS_PARTIAL", report.codes())

    def test_stale_after_is_enforced_by_common_quality_gate(self):
        payload = {"output": {"stck_prpr": "100", "stck_oprc": "90", "stck_hgpr": "110", "stck_lwpr": "80", "acml_vol": "10", "acml_tr_pbmn": "1000"}}
        adapted = adapt_probe_payload(
            "kis_stock_price", payload, context=CONTEXT, instrument="005930",
            observed_at=NOW - timedelta(seconds=20), stale_after_seconds=10,
        )
        report = evaluate_observations(adapted.observations, target_trade_date=TARGET, now=NOW)
        self.assertTrue(report.blocking)
        self.assertIn("OBSERVATION_STALE", report.codes())

    def test_safe_probe_to_observation_bridge_uses_in_memory_payload(self):
        def request_override(spec, ticker, current):
            return {
                "rt_cd": "0",
                "output2": [{
                    "stck_bsop_date": "20260731", "stck_cntg_hour": "153000",
                    "stck_oprc": "100", "stck_hgpr": "110", "stck_lwpr": "90",
                    "stck_prpr": "105", "cntg_vol": "10", "acml_tr_pbmn": "1000",
                }],
            }, 200

        result = collect_probe_observations(
            "kis_stock_minute",
            context=CONTEXT,
            instrument="005930",
            request_override=request_override,
        )
        self.assertEqual(result.probe.execution_status, "SUCCESS")
        self.assertEqual(result.probe.verification_status, "PARTIAL")
        self.assertEqual(result.adapted.included_rows, 1)
        self.assertEqual(len(result.adapted.observations), 6)


class OrchestratorTests(unittest.TestCase):
    def _adapter(self):
        payload = {"output": {"stck_prpr": "100", "stck_oprc": "90", "stck_hgpr": "110", "stck_lwpr": "80", "acml_vol": "10", "acml_tr_pbmn": "1000"}}
        return adapt_probe_payload(
            "kis_stock_price", payload, context=CONTEXT, instrument="005930",
            verification_status=VerificationStatus.VERIFIED,
        )

    def _sector(self):
        return SectorDecisionInput(
            "semiconductor",
            (signal("relative_strength"), signal("activity")),
            SectorCoverage(20, 10, 1000, 800, 300),
            True,
        )

    def test_cycle_runs_market_then_sector_then_stock(self):
        result = run_intraday_decision_cycle(
            context=CONTEXT,
            adapter_results=[self._adapter()],
            market_signals=[signal("price"), signal("flow"), signal("breadth")],
            sector_inputs=[self._sector()],
            stock_inputs=[StockDecisionInput("005930", StockState.SETUP, StockGateSignals(True, True, True, True, True), "semiconductor")],
        )
        self.assertEqual(result.market.decision, "ALLOW")
        self.assertEqual(result.sectors["semiconductor"].decision, "LEADING")
        self.assertEqual(result.stocks["005930"].current, StockState.TRIGGERED)

    def test_market_block_prevents_new_stock_trigger(self):
        result = run_intraday_decision_cycle(
            context=CONTEXT,
            adapter_results=[self._adapter()],
            market_signals=[signal("price", AxisStatus.FAIL), signal("flow", AxisStatus.FAIL), signal("breadth")],
            sector_inputs=[self._sector()],
            stock_inputs=[StockDecisionInput("005930", StockState.SETUP, StockGateSignals(True, True, True, True, True), "semiconductor")],
        )
        self.assertEqual(result.market.decision, "BLOCK")
        self.assertEqual(result.stocks["005930"].current, StockState.WATCH)

    def test_adapter_blocking_issue_makes_entire_cycle_not_evaluable(self):
        empty = adapt_probe_payload("kis_stock_price", {}, context=CONTEXT, instrument="005930")
        result = run_intraday_decision_cycle(
            context=CONTEXT,
            adapter_results=[empty],
            market_signals=[signal("price"), signal("flow"), signal("breadth")],
            sector_inputs=[self._sector()],
            stock_inputs=[],
        )
        self.assertEqual(result.market.decision, "NOT_EVALUABLE")
        self.assertTrue(result.quality.blocking)

    def test_partial_api_fields_cannot_be_promoted_to_allow(self):
        payload = {"output": {"stck_prpr": "100", "stck_oprc": "90", "stck_hgpr": "110", "stck_lwpr": "80", "acml_vol": "10", "acml_tr_pbmn": "1000"}}
        partial = adapt_probe_payload("kis_stock_price", payload, context=CONTEXT, instrument="005930")
        result = run_intraday_decision_cycle(
            context=CONTEXT,
            adapter_results=[partial],
            market_signals=[signal("price"), signal("flow"), signal("breadth")],
            sector_inputs=[self._sector()],
            stock_inputs=[],
        )
        self.assertEqual(result.market.decision, "NOT_EVALUABLE")
        self.assertIn("FIELD_SEMANTICS_PARTIAL", result.quality.codes())

    def test_undated_weekend_payload_is_not_assigned_previous_trade_date(self):
        weekend_context = SessionContext(
            TARGET,
            datetime(2026, 8, 1, 12, 0, tzinfo=KST),
            False,
            "TEST_CALENDAR",
        )
        payload = {"output": {"stck_prpr": "100", "stck_oprc": "90", "stck_hgpr": "110", "stck_lwpr": "80", "acml_vol": "10", "acml_tr_pbmn": "1000"}}
        adapted = adapt_probe_payload(
            "kis_stock_price", payload, context=weekend_context, instrument="005930",
            verification_status=VerificationStatus.VERIFIED,
        )
        self.assertTrue(all(item.meta.source_trade_date is None for item in adapted.observations))
        report = evaluate_observations(
            adapted.observations,
            target_trade_date=TARGET,
            now=weekend_context.evaluated_at,
            require_source_trade_date=True,
        )
        self.assertTrue(report.blocking)


if __name__ == "__main__":
    unittest.main()
