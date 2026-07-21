import sqlite3
import tempfile
from pathlib import Path
from unittest import TestCase

from close_bet_staged.data_loader import load_selected_minute_bars


class MinuteBarLoaderTests(TestCase):
    def test_loads_current_intraday_stock_bars_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "bars.db"
            with sqlite3.connect(database) as connection:
                connection.execute(
                    """
                    CREATE TABLE intraday_stock_bars (
                        trade_date TEXT, ticker TEXT, bar_time TEXT,
                        open REAL, high REAL, low REAL, close REAL, volume REAL
                    )
                    """
                )
                connection.execute(
                    "INSERT INTO intraday_stock_bars VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    ("20260721", "5930", "14:30", 70000, 70500, 69900, 70400, 1234),
                )

            frame = load_selected_minute_bars(database)

            self.assertEqual(len(frame), 1)
            self.assertEqual(frame.iloc[0]["ticker"], "005930")
            self.assertEqual(
                frame.iloc[0]["timestamp"].strftime("%Y-%m-%d %H:%M"),
                "2026-07-21 14:30",
            )
            self.assertEqual(frame.iloc[0]["volume"], 1234)
