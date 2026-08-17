import sqlite3
import tempfile
import unittest
from pathlib import Path

from web_database import (
    compress_web_database,
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


if __name__ == "__main__":
    unittest.main()
