import sqlite3
import unittest

from bottom_candidate_display import (
    build_bottom_candidate_display,
    read_bottom_candidate_display,
)


class BottomCandidateDisplayTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("""CREATE TABLE model_rule_scan_signals (
            signal_date TEXT, model_id TEXT, ticker TEXT, name TEXT,
            current_price REAL, change_rate REAL, market_cap REAL,
            trend_score REAL, rsi_14 REAL, volume_ratio REAL, entry_price REAL,
            stop_price REAL, first_target_price REAL, target_room_pct REAL,
            signal_reason TEXT, universe_type TEXT)""")
        self.conn.execute("""CREATE TABLE model_bottom_signals (
            signal_date TEXT, ticker TEXT, name TEXT, current_price REAL,
            bottom_score REAL, grade TEXT, chart_score REAL, supply_score REAL,
            sector_market_score REAL, risk_penalty REAL, reasons TEXT,
            risk_reasons TEXT, universe_type TEXT)""")
        self.conn.execute("""CREATE TABLE model_ohlcv_daily (
            date TEXT, ticker TEXT, change_rate REAL, market_cap REAL,
            universe_type TEXT)""")

    def tearDown(self):
        self.conn.close()

    def test_rule_rows_take_precedence_and_preserve_order(self):
        values = [
            ("20260911", "model_1", "1", "알파", 100, 1, 20000, 2, 55, 1.2, 100, 97, 110, 10, "a", "market_cap_10000eok_plus"),
            ("20260911", "model_1", "2", "베타", 100, 1, 20000, 4, 55, 1.2, 100, 97, 110, 10, "b", "market_cap_10000eok_plus"),
        ]
        self.conn.executemany(
            "INSERT INTO model_rule_scan_signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            values,
        )
        build_bottom_candidate_display(self.conn)
        result = read_bottom_candidate_display(self.conn, "20260912", "20260911")
        self.assertEqual(result["ticker"].tolist(), ["2", "1"])
        self.assertEqual(result["decision_risk_summary"].tolist(), ["b", "a"])
        self.assertNotIn("bottom_score", result.columns)

    def test_legacy_rows_include_materialized_ohlcv_and_date_fallback(self):
        self.conn.execute(
            "INSERT INTO model_bottom_signals VALUES "
            "('20260910','3','감마',90,50,'관찰',3,2,1,0,'[]','[]','market_cap_10000eok_plus')"
        )
        self.conn.execute(
            "INSERT INTO model_ohlcv_daily VALUES "
            "('20260910','3',-1.25,15000,'market_cap_10000eok_plus')"
        )
        build_bottom_candidate_display(self.conn)
        result = read_bottom_candidate_display(self.conn, "20260911")
        self.assertEqual(result.iloc[0]["today_change_rate"], -1.25)
        self.assertEqual(result.iloc[0]["market_cap"], 15000)
        self.assertNotIn("scanner_model", result.columns)

    def test_equal_scores_use_stable_ticker_order(self):
        self.conn.executemany(
            "INSERT INTO model_bottom_signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                ("20260911", "9", "나", 90, 50, "관찰", 3, 2, 1, 0, "[]", "[]", "market_cap_10000eok_plus"),
                ("20260911", "1", "가", 90, 50, "관찰", 3, 2, 1, 0, "[]", "[]", "market_cap_10000eok_plus"),
            ],
        )
        self.conn.executemany(
            "INSERT INTO model_ohlcv_daily VALUES (?,?,?,?,?)",
            [
                ("20260911", "9", 1, 15000, "market_cap_10000eok_plus"),
                ("20260911", "1", 1, 15000, "market_cap_10000eok_plus"),
            ],
        )
        build_bottom_candidate_display(self.conn)
        result = read_bottom_candidate_display(self.conn, "20260911")
        self.assertEqual(result["ticker"].tolist(), ["1", "9"])


if __name__ == "__main__":
    unittest.main()
