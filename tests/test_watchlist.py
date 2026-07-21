import sqlite3
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from watchlist import WatchlistManager


KST = timezone(timedelta(hours=9))


class FakeCollector:
    frames = {}

    def __init__(self, db_path):
        self.db_path = db_path

    def fetch_daily_ohlcv(self, ticker, lookback_days=45):
        return list(self.frames[ticker])

    def save_ohlcv(self, ticker, name, market_cap, universe_type, frame):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS model_ohlcv_daily (
                    date TEXT, ticker TEXT, name TEXT, close INTEGER,
                    change_rate REAL, universe_type TEXT,
                    PRIMARY KEY (date, ticker, universe_type)
                )"""
            )
            for row in frame:
                conn.execute(
                    "INSERT OR REPLACE INTO model_ohlcv_daily VALUES (?, ?, ?, ?, ?, ?)",
                    (row["date"], ticker, name, row["close"], row["change_rate"], universe_type),
                )


def read_item(db_path):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT ticker, added_date, entry_date, entry_price FROM watchlist_items"
        ).fetchone()


class WatchlistManagerTests(TestCase):
    @patch("watchlist.get_model_data_collector", side_effect=FakeCollector)
    def test_intraday_add_waits_for_close_then_keeps_fixed_entry(self, _collector):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "watchlist.db"
            FakeCollector.frames = {
                "005930": [
                    {"date": "20260720", "close": 70000, "change_rate": 1.0},
                    {"date": "20260721", "close": 71000, "change_rate": 1.43},
                ]
            }
            intraday = WatchlistManager(
                db_path, now=datetime(2026, 7, 21, 14, 0, tzinfo=KST)
            )
            intraday.add("005930", "삼성전자", 400_000_000_000_000)
            self.assertEqual(read_item(db_path), ("005930", "20260721", None, None))

            after_close = WatchlistManager(
                db_path, now=datetime(2026, 7, 21, 16, 0, tzinfo=KST)
            )
            after_close.refresh()
            self.assertEqual(
                read_item(db_path), ("005930", "20260721", "20260721", 71000)
            )

            FakeCollector.frames["005930"].append(
                {"date": "20260722", "close": 73000, "change_rate": 2.82}
            )
            next_day = WatchlistManager(
                db_path, now=datetime(2026, 7, 22, 16, 0, tzinfo=KST)
            )
            next_day.refresh()
            self.assertEqual(
                read_item(db_path), ("005930", "20260721", "20260721", 71000)
            )

    @patch("watchlist.get_model_data_collector", side_effect=FakeCollector)
    def test_non_trading_day_uses_latest_completed_close_and_remove(self, _collector):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "watchlist.db"
            FakeCollector.frames = {
                "000660": [
                    {"date": "20260717", "close": 200000, "change_rate": -0.5},
                ]
            }
            manager = WatchlistManager(
                db_path, now=datetime(2026, 7, 19, 16, 0, tzinfo=KST)
            )
            manager.add("000660", "SK하이닉스", 150_000_000_000_000)
            self.assertEqual(
                read_item(db_path), ("000660", "20260719", "20260717", 200000)
            )
            self.assertTrue(manager.remove("000660"))
            self.assertIsNone(read_item(db_path))
