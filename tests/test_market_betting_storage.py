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
from market_betting_engine.streamlit_tab import build_run_view, decision_label, evidence_table


class DecisionStorageTests(unittest.TestCase):
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
    def test_korean_labels_and_evidence_rows(self):
        self.assertEqual(decision_label("SELECTIVE"), "선별 진입")
        self.assertEqual(decision_label("UNKNOWN_STATE"), "UNKNOWN_STATE")
        rows = evidence_table([{"axis": "price", "code": "P", "message": "supported"}])
        self.assertEqual(rows, [{"축": "price", "코드": "P", "설명": "supported"}])

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
