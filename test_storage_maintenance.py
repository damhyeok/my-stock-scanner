import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from storage_maintenance import (
    _compact_git_repository,
    prune_database,
    run_storage_maintenance,
)


class StorageMaintenanceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_prunes_only_dates_older_than_retention_window(self):
        db_path = self.root / "sample.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute("CREATE TABLE bars (trade_date TEXT, value INTEGER)")
            conn.executemany(
                "INSERT INTO bars VALUES (?, ?)",
                [(f"2026010{day}", day) for day in range(1, 6)],
            )

        result = prune_database(db_path, {"bars": ("trade_date", 2)})

        with sqlite3.connect(db_path) as conn:
            dates = [row[0] for row in conn.execute("SELECT trade_date FROM bars ORDER BY trade_date")]
        self.assertEqual(dates, ["20260104", "20260105"])
        self.assertEqual(result["deleted_rows"]["bars"], 3)
        self.assertEqual(result["integrity"], "ok")

    def test_writes_disk_and_database_report_when_databases_are_absent(self):
        report = run_storage_maintenance(self.root)

        self.assertIn(report["disk"]["status"], {"OK", "WARNING", "CRITICAL"})
        self.assertFalse(report["databases"]["stock_data"]["exists"])
        self.assertTrue((self.root / "reports" / "storage_maintenance_latest.json").is_file())

    def test_git_compaction_runs_only_above_threshold(self):
        below = _compact_git_repository(self.root, {"exists": True, "loose_bytes": 1})
        self.assertFalse(below["attempted"])

        with patch("storage_maintenance.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = ""
            run.return_value.stderr = ""
            above = _compact_git_repository(
                self.root,
                {"exists": True, "loose_bytes": 600 * 1024 * 1024},
            )
        self.assertTrue(above["attempted"])
        self.assertEqual(run.call_count, 3)
        self.assertEqual(above["return_code"], 0)


if __name__ == "__main__":
    unittest.main()
