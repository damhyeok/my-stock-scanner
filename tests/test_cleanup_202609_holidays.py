import sqlite3
from contextlib import closing
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "cleanup_202609_holidays.py"


class HolidayCleanupTests(unittest.TestCase):
    def test_market_rows_deleted_but_news_and_watchlist_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "stock_data.db"
            with closing(sqlite3.connect(db)) as conn:
                conn.executescript("""
                    CREATE TABLE daily_stocks (date TEXT, name TEXT);
                    CREATE TABLE stock_news (date TEXT, title TEXT);
                    CREATE TABLE watchlist_items (added_date TEXT, ticker TEXT);
                    CREATE TABLE market_betting_runs (run_id TEXT PRIMARY KEY, target_trade_date TEXT);
                    CREATE TABLE market_betting_judgments (
                        run_id TEXT REFERENCES market_betting_runs(run_id) ON DELETE CASCADE
                    );
                """)
                conn.executemany("INSERT INTO daily_stocks VALUES (?, ?)", [
                    ("20260923", "valid"), ("20260924", "false"), ("20260925", "false")
                ])
                conn.execute("INSERT INTO stock_news VALUES ('20260924', 'holiday news')")
                conn.execute("INSERT INTO watchlist_items VALUES ('20260925', '000001')")
                conn.execute("INSERT INTO market_betting_runs VALUES ('r1', '2026-09-24')")
                conn.execute("INSERT INTO market_betting_judgments VALUES ('r1')")
                conn.commit()
            subprocess.run([sys.executable, str(SCRIPT), str(db), "--apply"], check=True,
                           capture_output=True, text=True)
            with closing(sqlite3.connect(db)) as conn:
                self.assertEqual(conn.execute("SELECT date FROM daily_stocks").fetchall(), [("20260923",)])
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM stock_news").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM watchlist_items").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM market_betting_runs").fetchone()[0], 0)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM market_betting_judgments").fetchone()[0], 0)
            self.assertEqual(len(list(Path(directory).glob("*_pre_holiday_cleanup_*.db"))), 1)


if __name__ == "__main__":
    unittest.main()
