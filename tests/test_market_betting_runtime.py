import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from market_betting_engine.adapters import adapt_probe_payload
from market_betting_engine.collector import ProbeCollectionResult
from market_betting_engine.contracts import VerificationStatus
from market_betting_engine.runtime import run_market_betting_analysis
from market_betting_engine.session import KST, SessionContext
from market_betting_engine.storage import load_decision_run


NOW = datetime(2026, 7, 31, 15, 25, tzinfo=KST)
CONTEXT = SessionContext(NOW.date(), NOW, True, "TEST")


def bar_payload(kind, start):
    rows = []
    for index in range(10):
        price = start + index
        row = {
            "stck_bsop_date": "20260731",
            "stck_cntg_hour": f"15{index:02d}00",
            "cntg_vol": str(100 if index < 5 else 300),
        }
        if kind == "index":
            row.update(
                bstp_nmix_oprc=str(price - 1), bstp_nmix_hgpr=str(price + 1),
                bstp_nmix_lwpr=str(price - 2), bstp_nmix_prpr=str(price),
                acml_tr_pbmn=str(price * 100),
            )
        elif kind == "futures":
            row.update(
                futs_oprc=str(price - 1), futs_hgpr=str(price + 1),
                futs_lwpr=str(price - 2), futs_prpr=str(price),
            )
        else:
            row.update(
                stck_oprc=str(price - 1), stck_hgpr=str(price + 1),
                stck_lwpr=str(price - 2), stck_prpr=str(price),
                acml_tr_pbmn=str(price * 100),
            )
        rows.append(row)
    return {"output2": rows}


class OracleRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "stock_data.db"
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE daily_stocks (
                    date TEXT, session TEXT, ticker TEXT, name TEXT, sector TEXT,
                    trading_value REAL, fluctuation_rate REAL,
                    collected_at_kst TEXT, category TEXT
                )
                """
            )
            conn.execute(
                "INSERT INTO daily_stocks VALUES (?,?,?,?,?,?,?,?,?)",
                ("20260731", "정규장 (15:20)", "005930", "삼성전자", "반도체", 1000, 3.0, "2026-07-31 15:20:00", "VOLUME_TOP_60"),
            )

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def fake_collect(probe_id, *, context, instrument, ticker="005930", **kwargs):
        if probe_id == "kis_index_minute_kospi":
            payload = bar_payload("index", 200)
        elif probe_id == "kis_futures_minute_active":
            payload = bar_payload("futures", 300)
        elif probe_id == "kis_program_summary_kospi":
            payload = {"output": [
                {"bsop_hour": "151900", "whol_smtn_ntby_tr_pbmn": "10", "arbt_smtn_ntby_tr_pbmn": "0", "nabt_smtn_ntby_tr_pbmn": "10"},
                {"bsop_hour": "152000", "whol_smtn_ntby_tr_pbmn": "20", "arbt_smtn_ntby_tr_pbmn": "0", "nabt_smtn_ntby_tr_pbmn": "20"},
            ]}
        else:
            payload = bar_payload("stock", 100)
        adapted = adapt_probe_payload(
            probe_id,
            payload,
            context=CONTEXT,
            instrument=instrument,
            verification_status=VerificationStatus.VERIFIED,
        )
        probe = SimpleNamespace(
            probe_id=probe_id,
            execution_status="SUCCESS",
            verification_status="VERIFIED",
            output_row_count=10,
            source_trade_dates=["20260731"] if probe_id == "kis_index_minute_kospi" else [],
        )
        return ProbeCollectionResult(probe, adapted)

    def test_oracle_runtime_saves_cycle_and_blocks_incomplete_sector_universe(self):
        with patch(
            "market_betting_engine.runtime.collect_probe_observations",
            side_effect=self.fake_collect,
        ):
            receipt = run_market_betting_analysis(self.db_path, evaluated_at=NOW)

        detail = load_decision_run(self.db_path, receipt.run_id)
        judgments = {
            (row["scope_type"], row["scope_id"]): row for row in detail["judgments"]
        }
        self.assertEqual(judgments[("MARKET", "KOSPI")]["decision"], "ALLOW")
        sector = judgments[("SECTOR", "반도체")]
        self.assertEqual(sector["decision"], "NOT_EVALUABLE")
        self.assertTrue(
            any(item["code"] == "SECTOR_UNIVERSE_INCOMPLETE" for item in sector["blockers"])
        )
        self.assertEqual(
            judgments[("OVERNIGHT", "HOLD_EXISTING")]["decision"], "NOT_EVALUABLE"
        )


if __name__ == "__main__":
    unittest.main()
