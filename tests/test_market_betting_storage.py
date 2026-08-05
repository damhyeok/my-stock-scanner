import sqlite3
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from market_betting_engine.contracts import (
    AxisSignal,
    AxisStatus,
    CalculationMode,
    DataQualityReport,
    Evidence,
    Judgment,
    Observation,
    ObservationMeta,
    StockState,
    TradeDateProvenance,
    VerificationStatus,
)
from market_betting_engine.engines import assess_overnight_permissions
from market_betting_engine.orchestrator import DecisionCycleResult
from market_betting_engine.session import KST, SessionContext
from market_betting_engine.states import StateTransition
from market_betting_engine.storage import (
    list_decision_runs,
    load_decision_run,
    prune_decision_history,
    save_decision_cycle,
)
from market_betting_engine.streamlit_tab import (
    _sector_breadth_tier,
    _sector_reason_text,
    build_sector_action_rows,
    build_sector_strength_history,
    build_run_view,
    contextual_stock_action,
    decision_label,
    entry_condition_text,
    evidence_table,
    invalidation_condition_text,
    reason_label,
    reference_values_text,
    state_label,
    normalize_trade_date,
    select_run_for_session,
    selected_session_time,
    selection_instruction,
)


class DecisionStorageTests(unittest.TestCase):
    def test_sector_breadth_tiers_separate_candidate_expansion_and_genuine(self):
        judgment = {"decision": "NEUTRAL"}

        def summary(ratio):
            return {
                "member_count": 8,
                "above_vwap_ratio": ratio,
                "outperforming_ratio": ratio,
                "activity_confirming_ratio": ratio,
            }

        self.assertEqual(_sector_breadth_tier(judgment, summary(0.40)), "CANDIDATE")
        self.assertEqual(_sector_breadth_tier(judgment, summary(0.50)), "EXPANDING")
        self.assertEqual(_sector_breadth_tier(judgment, summary(0.60)), "GENUINE")
        self.assertEqual(
            _sector_breadth_tier(judgment, {**summary(0.75), "member_count": 3}),
            "NEUTRAL",
        )

    def test_sector_reason_is_explained_in_plain_korean(self):
        reason = _sector_reason_text(
            {
                "decision": "LEADING",
                "evidence": [
                    {
                        "code": "SECTOR_ABOVE_VWAP_RATIO",
                        "message": "equal-weight member ratio=0.8000",
                    },
                    {
                        "code": "SECTOR_OUTPERFORMING_RATIO",
                        "message": "equal-weight member ratio=0.6000",
                    },
                    {
                        "code": "SECTOR_ACTIVITY_CONFIRMING_RATIO",
                        "message": "equal-weight member ratio=0.7000",
                    },
                ],
            }
        )
        self.assertIn("80%", reason)
        self.assertIn("당일 평균 매매가격(VWAP) 위", reason)
        self.assertIn("코스피보다 강함", reason)
        self.assertIn("거래 증가와 가격 상승", reason)
        self.assertIn("흐름이 이어지는 중", reason)

    def test_dashboard_trade_date_is_normalized_for_engine_lookup(self):
        self.assertEqual(normalize_trade_date("20260731"), "2026-07-31")
        self.assertEqual(normalize_trade_date("2026-07-31"), "2026-07-31")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "stock_data.db"
        self.context = SessionContext(
            date(2026, 7, 31),
            datetime(2026, 7, 31, 15, 25, tzinfo=KST),
            True,
            "TEST_CALENDAR",
        )
        quality = DataQualityReport((), 1, 1)
        market = Judgment(
            "ALLOW",
            evidence=(Evidence("PRICE_OK", "price_action", "price is supported"),),
            confidence_label="PLACEHOLDER_THRESHOLDS",
            quality=quality,
        )
        sector = Judgment(
            "LEADING",
            evidence=(Evidence("SECTOR_OK", "sector_participation", "members participate"),),
            quality=quality,
        )
        transition = StateTransition(StockState.SETUP, StockState.TRIGGERED, "ALL_TRIGGER_GATES_CONFIRMED", True)
        self.result = DecisionCycleResult(
            quality=quality,
            market=market,
            sectors={"반도체": sector},
            stocks={"005930": transition},
            observation_count=1,
        )
        self.observation = Observation(
            "stock.005930.20260731.152500.close",
            100.0,
            ObservationMeta(
                source="KIS",
                observed_at=self.context.evaluated_at,
                source_trade_date=self.context.target_trade_date,
                unit="KRW",
                semantics_status=VerificationStatus.VERIFIED,
                calculation_mode=CalculationMode.ACTUAL,
                field_name="stck_prpr",
                trade_date_provenance=TradeDateProvenance.RESPONSE_FIELD,
            ),
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_complete_cycle_is_saved_and_loaded(self):
        overnight = assess_overnight_permissions(
            [
                AxisSignal("price", AxisStatus.PASS, "P", "price"),
                AxisSignal("flow", AxisStatus.PASS, "F", "flow"),
                AxisSignal("futures", AxisStatus.PASS, "U", "futures"),
            ],
            existing_thesis_valid=True,
        )
        receipt = save_decision_cycle(
            self.db_path,
            context=self.context,
            result=self.result,
            config_version="expert-placeholder-v1",
            engine_version="phase3-v1",
            observations=[self.observation],
            derived_evidence={"market": {"vwap_distance": 0.01}},
            overnight=overnight,
            run_id="run-1",
        )
        self.assertEqual(receipt.judgment_count, 4)
        self.assertEqual(receipt.stock_state_count, 1)
        self.assertEqual(receipt.observation_count, 1)

        rows = list_decision_runs(self.db_path, target_trade_date="2026-07-31")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["market_decision"], "ALLOW")
        self.assertEqual(rows[0]["derived_evidence"]["market"]["vwap_distance"], 0.01)

        detail = load_decision_run(self.db_path, "run-1")
        self.assertEqual(len(detail["judgments"]), 4)
        self.assertEqual(detail["stocks"][0]["current_state"], "TRIGGERED")
        view = build_run_view(detail)
        self.assertEqual(view["market"]["decision"], "ALLOW")
        self.assertEqual(view["overnight"]["CLOSE_NEW_ENTRY"]["decision"], "ALLOWED")
        self.assertEqual(view["sectors"][0]["scope_id"], "반도체")

    def test_sector_strength_history_uses_saved_market_betting_judgments(self):
        save_decision_cycle(
            self.db_path,
            context=self.context,
            result=self.result,
            config_version="v1",
            engine_version="e1",
            run_id="sector-history",
        )
        history, selected = build_sector_strength_history(
            str(self.db_path), "20260731", "정규장(16:00)"
        )
        self.assertEqual(selected["run_id"], "sector-history")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["sector"], "반도체")
        self.assertEqual(history[0]["status"], "강세 지속")
        self.assertEqual(history[0]["score"], 2)

    def test_duplicate_run_id_rolls_back_second_transaction(self):
        kwargs = dict(
            context=self.context,
            result=self.result,
            config_version="v1",
            engine_version="e1",
            run_id="duplicate",
        )
        save_decision_cycle(self.db_path, **kwargs)
        with self.assertRaises(sqlite3.IntegrityError):
            save_decision_cycle(self.db_path, **kwargs)
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM market_betting_runs").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM market_betting_judgments").fetchone()[0], 2)

    def test_missing_tables_return_empty_instead_of_breaking_dashboard(self):
        empty_db = Path(self.temp.name) / "empty.db"
        sqlite3.connect(empty_db).close()
        self.assertEqual(list_decision_runs(empty_db), [])
        self.assertIsNone(load_decision_run(empty_db, "missing"))

    def test_date_filter_does_not_fall_back_to_another_day(self):
        save_decision_cycle(
            self.db_path,
            context=self.context,
            result=self.result,
            config_version="v1",
            engine_version="e1",
            run_id="run-date",
        )
        self.assertEqual(list_decision_runs(self.db_path, target_trade_date="2026-07-30"), [])

    def test_retention_keeps_compact_runs_longer_than_raw_observations(self):
        for index, day in enumerate((29, 30, 31)):
            context = SessionContext(
                date(2026, 7, day),
                datetime(2026, 7, day, 15, 25, tzinfo=KST),
                True,
                "TEST",
            )
            observation = Observation(
                f"stock.005930.202607{day:02d}.152500.close",
                100.0,
                ObservationMeta(
                    source="KIS", observed_at=context.evaluated_at,
                    source_trade_date=context.target_trade_date, unit="KRW",
                    semantics_status=VerificationStatus.VERIFIED,
                    calculation_mode=CalculationMode.ACTUAL,
                    field_name="stck_prpr",
                    trade_date_provenance=TradeDateProvenance.RESPONSE_FIELD,
                ),
            )
            save_decision_cycle(
                self.db_path, context=context, result=self.result,
                config_version="v1", engine_version="e1",
                observations=[observation], run_id=f"retention-{index}",
            )

        receipt = prune_decision_history(
            self.db_path,
            keep_run_trade_dates=2,
            keep_raw_observation_trade_dates=1,
        )
        self.assertEqual(receipt.deleted_runs, 1)
        self.assertEqual(receipt.deleted_observations, 2)
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM market_betting_runs").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM market_betting_observations").fetchone()[0], 1)


class DashboardViewTests(unittest.TestCase):
    def test_sidebar_session_selects_closest_usable_market_data_time(self):
        runs = [
            {
                "run_id": "0940",
                "market_decision": "SELECTIVE",
                "quality_blocking": 0,
                "evaluated_at_kst": "2026-08-05T09:40:00+09:00",
                "derived_evidence": {"bundle": {"market_features": {"as_of": "2026-08-05T09:40:00+09:00"}}},
            },
            {
                "run_id": "0954",
                "market_decision": "ALLOW",
                "quality_blocking": 0,
                "evaluated_at_kst": "2026-08-05T09:54:00+09:00",
                "derived_evidence": {"bundle": {"market_features": {"as_of": "2026-08-05T09:54:00+09:00"}}},
            },
        ]
        selected = select_run_for_session(runs, "20260805", "장중(09:50)")
        self.assertEqual(selected["run_id"], "0954")
        self.assertEqual(
            selected_session_time("20260805", "장중(09:50)"),
            datetime(2026, 8, 5, 9, 50),
        )

    def test_time_matching_does_not_substitute_distant_valid_run(self):
        runs = [
            {
                "run_id": "close-valid", "market_decision": "SELECTIVE", "quality_blocking": 0,
                "evaluated_at_kst": "2026-08-05T15:30:00+09:00",
            },
            {
                "run_id": "morning-unavailable", "market_decision": "NOT_EVALUABLE", "quality_blocking": 1,
                "evaluated_at_kst": "2026-08-05T09:52:00+09:00",
            },
        ]
        selected = select_run_for_session(runs, "20260805", "장중(09:50)")
        self.assertEqual(selected["run_id"], "morning-unavailable")

    def test_sector_action_names_the_sector_and_candidate_stock(self):
        view = {
            "run": {"derived_evidence": {"adaptive_universe": {"stocks": [
                {"ticker": "005930", "name": "삼성전자", "sector": "반도체"},
            ]}}},
            "market": {"decision": "SELECTIVE"},
            "overnight": {"CLOSE_NEW_ENTRY": {"decision": "SELECTIVE"}},
            "sectors": [{"scope_id": "반도체", "decision": "LEADING"}],
            "stocks": [{"symbol": "005930", "current_state": "SETUP"}],
        }
        rows = build_sector_action_rows(view)
        self.assertEqual(rows[0]["현재 강도"], "강세 지속")
        self.assertIn("삼성전자", rows[0]["장중에는"])
        self.assertIn("조건부 후보", rows[0]["종가에는"])
        self.assertIn("반도체", selection_instruction(view))

    def test_breakout_candidate_explains_trigger_and_invalidation_prices(self):
        setup = {
            "setup_type": "BREAKOUT",
            "trigger_price": 25575.55,
            "invalidation_price": 25473.35,
            "reward_reference": 25750,
        }
        entry = entry_condition_text(setup, "SETUP")
        invalidation = invalidation_condition_text(setup, "SETUP")
        self.assertIn("25,576원", entry)
        self.assertIn("1분봉 종가", entry)
        self.assertIn("25,473원", invalidation)
        self.assertIn("2개 연속", invalidation)
        self.assertIn("추격하지 않습니다", invalidation)
        references = reference_values_text(
            setup,
            {"features": {"last_close": 25500, "session_vwap": {"value": 25454.75}}},
        )
        self.assertIn("현재 약 25,500원", references)
        self.assertIn("당일 종목 VWAP 약 25,455원", references)

    def test_stock_action_does_not_override_weak_sector_gate(self):
        action = contextual_stock_action("SETUP", "SELECTIVE", "AVOID")
        self.assertIn("가격 신호가 나와도", action)
        self.assertIn("매수하지 않기", action)

    def test_korean_labels_and_evidence_rows(self):
        self.assertEqual(decision_label("SELECTIVE"), "선별 진입")
        self.assertEqual(decision_label("UNKNOWN_STATE"), "UNKNOWN_STATE")
        rows = evidence_table([{
            "axis": "price_action",
            "code": "PRICE_VWAP_STRUCTURE",
            "message": "VWAP distance=0.0012, short return=0.0025",
        }])
        self.assertEqual(rows[0]["분석 항목"], "가격 흐름")
        self.assertIn("분봉 기반 VWAP", rows[0]["무엇을 보는지"])
        self.assertEqual(rows[0]["현재 관측"], "가격의 VWAP 대비 위치 +0.12%, 최근 수익률 +0.25%")
        self.assertEqual(state_label("EXTENDED"), "과열·추격 금지")
        self.assertIn("추격하지 않습니다", reason_label("RISK_REWARD_EXTENDED"))

    def test_view_exposes_structural_stock_setups(self):
        detail = {
            "run": {"derived_evidence": {
                "stock_setups": {"005930": {
                    "setup_type": "BREAKOUT", "entry_reference": 100,
                    "invalidation_price": 98,
                }},
                "stock_lifecycles": {"005930": {
                    "active": True, "bars_since_trigger": 3,
                    "reasons": ["TRIGGER_REMAINS_ACTIVE"],
                }},
            }},
            "judgments": [],
            "stocks": [{"symbol": "005930"}],
        }
        view = build_run_view(detail)
        self.assertEqual(view["setups"]["005930"]["setup_type"], "BREAKOUT")
        self.assertEqual(view["setups"]["005930"]["invalidation_price"], 98)
        self.assertTrue(view["lifecycles"]["005930"]["active"])
        self.assertEqual(view["lifecycles"]["005930"]["bars_since_trigger"], 3)


if __name__ == "__main__":
    unittest.main()
