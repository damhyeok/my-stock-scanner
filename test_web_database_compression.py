import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from analyzer import StockAnalyzer
from web_database import (
    build_web_database,
    compress_web_database,
    create_recovery_database,
    decompress_web_database,
    restore_working_database,
)


class WebDatabaseCompressionTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.web_db = self.root / "web_data.db"
        with sqlite3.connect(self.web_db) as conn:
            conn.execute("CREATE TABLE sample (value TEXT)")
            conn.execute("INSERT INTO sample VALUES ('ok')")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_compress_and_decompress_round_trip(self):
        compressed = self.root / "web_data.db.gz"
        restored = self.root / "restored.db"

        summary = compress_web_database(self.web_db, compressed)
        decompress_web_database(compressed, restored)

        self.assertGreater(summary["compressed_bytes"], 0)
        with sqlite3.connect(restored) as conn:
            self.assertEqual(conn.execute("SELECT value FROM sample").fetchone()[0], "ok")

    def test_restore_working_database_uses_compressed_snapshot(self):
        compressed = self.root / "web_data.db.gz"
        working = self.root / "stock_data.db"
        compress_web_database(self.web_db, compressed)
        self.web_db.unlink()

        restored = restore_working_database(self.web_db, working, compressed)

        self.assertTrue(restored)
        with sqlite3.connect(working) as conn:
            self.assertEqual(conn.execute("SELECT value FROM sample").fetchone()[0], "ok")

    def test_sector_summary_keeps_history_before_oldest_selectable_date(self):
        source = self.root / "stock.db"
        target = self.root / "bounded.db"
        with sqlite3.connect(source) as conn:
            conn.execute(
                "CREATE TABLE daily_stocks (date TEXT, session TEXT, category TEXT, "
                "ticker TEXT, name TEXT, sector TEXT, trading_value INTEGER, fluctuation_rate REAL)"
            )
            for number in range(45):
                conn.execute(
                    "INSERT INTO daily_stocks VALUES (?, '정규장(16:00)', 'VOLUME_TOP_60', "
                    "'1', '알파', '반도체', 100, 1)",
                    (f"2026{number:04d}",),
                )
        build_web_database(source, target)
        with sqlite3.connect(target) as conn:
            raw_dates = conn.execute(
                "SELECT COUNT(DISTINCT date) FROM daily_stocks"
            ).fetchone()[0]
            summary_dates = conn.execute(
                "SELECT COUNT(DISTINCT date) FROM web_sector_trend_daily"
            ).fetchone()[0]
        self.assertEqual(raw_dates, 30)
        self.assertEqual(summary_dates, 39)

    def test_precomputed_scores_equal_existing_analyzer_output_and_order(self):
        source = self.root / "scores-source.db"
        target = self.root / "scores-web.db"
        with sqlite3.connect(source) as conn:
            conn.execute(
                "CREATE TABLE daily_stocks (date TEXT, session TEXT, category TEXT, "
                "ticker TEXT, name TEXT, foreign_net REAL, inst_net REAL, "
                "trading_value REAL, volume REAL, fluctuation_rate REAL)"
            )
            for date, first_volume, first_rate in (
                ("20260910", 200, 1), ("20260911", 100, -1)
            ):
                conn.executemany(
                    "INSERT INTO daily_stocks VALUES (?, '정규장(16:00)', ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (date, "VOLUME_TOP_60", "1", "알파", 10, 5, 1000, first_volume, first_rate),
                        (date, "FOREIGN_TOP_30", "2", "베타", 30, 0, 500, 100, 2),
                    ],
                )
        expected = StockAnalyzer(source).run_analysis().reset_index(drop=True)
        build_web_database(source, target)
        with sqlite3.connect(target) as conn:
            actual = pd.read_sql_query(
                "SELECT * FROM web_stock_analysis_scores ORDER BY display_order", conn
            ).drop(columns="display_order")
        actual["is_pullback"] = actual["is_pullback"].astype(bool)
        pd.testing.assert_frame_equal(actual, expected, check_dtype=False)

    def test_model_sources_are_removed_only_after_display_rows_are_built(self):
        source = self.root / "model-source.db"
        target = self.root / "model-web.db"
        with sqlite3.connect(source) as conn:
            conn.execute("CREATE TABLE model_feature_daily(value TEXT)")
            conn.execute("CREATE TABLE model_ohlcv_daily(value TEXT)")
            conn.execute(
                "CREATE TABLE model_universe_snapshots "
                "(ticker TEXT, name TEXT, market_cap REAL)"
            )
            conn.execute(
                "INSERT INTO model_universe_snapshots VALUES ('1','알파',100)"
            )
        build_web_database(source, target)
        with sqlite3.connect(target) as conn:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            catalog = conn.execute(
                "SELECT ticker,name,market_cap FROM web_stock_catalog"
            ).fetchone()
        self.assertFalse({
            "model_feature_daily", "model_ohlcv_daily",
            "model_universe_snapshots",
        } & tables)
        self.assertEqual(catalog, ("000001", "알파", 100.0))

    def test_restore_working_database_uses_bootstrap_snapshot(self):
        bootstrap = self.root / "web_data.bootstrap.db.gz"
        working = self.root / "stock_data.db"
        compress_web_database(self.web_db, bootstrap)
        self.web_db.unlink()

        restored = restore_working_database(
            self.web_db,
            working,
            self.root / "missing-live.db.gz",
            bootstrap,
        )

        self.assertTrue(restored)
        with sqlite3.connect(working) as conn:
            self.assertEqual(conn.execute("SELECT value FROM sample").fetchone()[0], "ok")

    def test_full_recovery_snapshot_is_preferred_to_bounded_web_copy(self):
        recovery = self.root / "stock_data.recovery.db.gz"
        working = self.root / "stock_data.db"
        create_recovery_database(self.web_db, recovery)
        with closing(sqlite3.connect(self.web_db)) as conn, conn:
            conn.execute("UPDATE sample SET value='bounded'")
        compressed = self.root / "web_data.db.gz"
        compress_web_database(self.web_db, compressed)
        self.web_db.unlink()
        restored = restore_working_database(
            self.web_db, working, compressed,
            self.root / "missing-bootstrap.gz", recovery,
        )
        self.assertTrue(restored)
        with sqlite3.connect(working) as conn:
            self.assertEqual(conn.execute("SELECT value FROM sample").fetchone()[0], "ok")


if __name__ == "__main__":
    unittest.main()
