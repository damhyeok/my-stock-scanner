import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from market_betting_display import (
    compact_market_betting_evidence,
    compact_market_betting_runs,
)
from market_betting_engine.streamlit_tab import (
    _run_data_time,
    _sector_member_groups,
    _sector_summary,
    build_run_view,
    build_sector_action_rows,
    build_sector_member_rows,
    hourly_member_rows,
)
from web_database import build_web_database


def metric(value):
    return {"value": value, "source": "large-audit-source", "unused": "x" * 100}


def evidence():
    hourly = {
        "mode": "ROLLING_60M_PATH_V1", "status": "COMPLETE",
        "window_start": "2026-09-11T14:20:00+09:00",
        "window_end": "2026-09-11T15:19:00+09:00",
        "expected_minutes": 60, "aligned_minutes": 60,
        "return_ratio": .02, "relative_retention": .7,
        "vwap_retention": .8, "higher_low_ratio": .6,
        "up_turnover_share": .7, "activity_change": .1,
        "above_vwap": True, "outperforming": True,
        "activity_confirming": True, "structure_confirming": True,
    }
    return {
        "bundle": {
            "market_features": {"as_of": "2026-09-11T15:19:00+09:00", "unused": "x" * 100},
            "stocks": {
                "005930": {
                    "features": {
                        "last_close": 70000, "session_vwap": metric(69000),
                        "vwap_distance_ratio": metric(.01),
                        "activity_acceleration": metric(.2), "short_return": metric(.01),
                        "unused_metric": metric(999),
                    },
                    "relative": {"relative_short_return": metric(.02), "unused": metric(1)},
                    "signals": [{"unused": "x" * 100}], "closing": {"unused": "x" * 100},
                    "hourly_path": hourly,
                }
            },
            "sectors": {
                "반도체": {
                    "summary": {
                        "analysis_mode": "ROLLING_60M_PATH_V1", "window_status": "COMPLETE",
                        "window_start": hourly["window_start"], "window_end": hourly["window_end"],
                        "member_count": 1, "evaluable_members": 1,
                        "above_vwap_ratio": 1.0, "outperforming_ratio": 1.0,
                        "activity_confirming_ratio": 1.0, "structure_confirming_ratio": 1.0,
                    },
                    "observed_members": ["005930"], "signals": [{"unused": "x" * 100}],
                }
            },
            "futures_features": {"unused": "x" * 100},
        },
        "adaptive_universe": {"stocks": [{
            "ticker": "005930", "name": "삼성전자", "sector": "반도체",
            "fluctuation_rate": 2.5, "trading_value": 1000000000, "unused": "x" * 100,
        }], "unused": "x" * 100},
        "stock_setups": {"005930": {
            "setup_type": "BREAKOUT", "trigger_price": 70500, "reference_level": 70400,
            "invalidation_price": 68000, "reward_reference": 74000, "unused": "x" * 100,
        }},
        "stock_lifecycles": {"005930": {"unused": "x" * 100}},
        "position_assessments": {"005930": {
            "current_price": 70000, "profit_loss_ratio": .05,
            "decision": "HOLD", "reasons": ["유지"], "unused": "x" * 100,
        }},
        "probe_statuses": {"unused": "x" * 100},
    }


def detail(derived):
    return {
        "run": {"derived_evidence": derived, "evaluated_at_kst": "2026-09-11T15:20:00+09:00"},
        "judgments": [
            {"scope_type": "MARKET", "scope_id": "KOSPI", "decision": "ALLOW"},
            {"scope_type": "SECTOR", "scope_id": "반도체", "decision": "LEADING",
             "evidence": [], "counter_evidence": [], "warnings": [], "blockers": []},
            {"scope_type": "OVERNIGHT", "scope_id": "CLOSE_NEW_ENTRY", "decision": "ALLOWED"},
        ],
        "stocks": [{"symbol": "005930", "current_state": "SETUP", "reason_code": "SETUP_READY_TRIGGER_PENDING"}],
    }


class MarketBettingDisplayTests(unittest.TestCase):
    def test_compaction_preserves_all_dashboard_builders(self):
        original = evidence()
        compact = compact_market_betting_evidence(original)
        original_detail, compact_detail = detail(original), detail(compact)
        original_view, compact_view = build_run_view(original_detail), build_run_view(compact_detail)
        self.assertEqual(_run_data_time(original_detail["run"]), _run_data_time(compact_detail["run"]))
        self.assertEqual(build_sector_action_rows(original_view), build_sector_action_rows(compact_view))
        self.assertEqual(build_sector_member_rows(original_view), build_sector_member_rows(compact_view))
        self.assertEqual(hourly_member_rows(original_detail), hourly_member_rows(compact_detail))
        self.assertEqual(_sector_summary(original_detail, "반도체"), _sector_summary(compact_detail, "반도체"))
        self.assertEqual(_sector_member_groups(original_detail, "반도체"), _sector_member_groups(compact_detail, "반도체"))
        self.assertLess(len(json.dumps(compact)), len(json.dumps(original)) * .7)

    def test_database_compaction_leaves_schema_and_run_identity_intact(self):
        connection = sqlite3.connect(":memory:")
        connection.execute(
            "CREATE TABLE market_betting_runs (run_id TEXT PRIMARY KEY, derived_evidence_json TEXT)"
        )
        payload = json.dumps(evidence(), ensure_ascii=False)
        connection.execute("INSERT INTO market_betting_runs VALUES (?, ?)", ("run-1", payload))
        result = compact_market_betting_runs(connection)
        row = connection.execute("SELECT run_id, derived_evidence_json FROM market_betting_runs").fetchone()
        self.assertEqual(row[0], "run-1")
        self.assertTrue(json.loads(row[1])["web_display_compacted"])
        self.assertEqual(result["rows"], 1)
        self.assertLess(result["after_bytes"], result["before_bytes"])

    def test_web_database_build_compacts_runs_without_changing_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory, "source.db")
            target_path = Path(directory, "web.db")
            payload = json.dumps(evidence(), ensure_ascii=False)
            with closing(sqlite3.connect(source_path)) as connection:
                connection.execute(
                    "CREATE TABLE market_betting_runs ("
                    "run_id TEXT PRIMARY KEY, target_trade_date TEXT, "
                    "derived_evidence_json TEXT)"
                )
                connection.execute(
                    "INSERT INTO market_betting_runs VALUES (?, ?, ?)",
                    ("run-1", "2026-09-11", payload),
                )
                connection.commit()
            build_web_database(source_path, target_path)
            with closing(sqlite3.connect(source_path)) as source:
                source_payload = source.execute(
                    "SELECT derived_evidence_json FROM market_betting_runs"
                ).fetchone()[0]
            with closing(sqlite3.connect(target_path)) as target:
                target_payload = target.execute(
                    "SELECT derived_evidence_json FROM market_betting_runs"
                ).fetchone()[0]
            self.assertEqual(source_payload, payload)
            self.assertTrue(json.loads(target_payload)["web_display_compacted"])
            self.assertLess(len(target_payload), len(source_payload))


if __name__ == "__main__":
    unittest.main()
