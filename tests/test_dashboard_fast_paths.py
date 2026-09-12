import sqlite3
from contextlib import closing
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from watchlist import WatchlistManager, KST, read_watchlist_performance
from sector_trend_window import (
    build_sector_trend_summary,
    recent_sector_summary_window,
    recent_sector_window,
)


class DashboardFastPathsTests(unittest.TestCase):
    def test_fast_add_remove_never_calls_provider(self):
        with tempfile.TemporaryDirectory() as d, patch("watchlist.get_model_data_collector") as provider:
            path = Path(d) / "test.db"
            manager = WatchlistManager(path, datetime(2026, 9, 11, 16, tzinfo=KST))
            manager.add("005930", "삼성전자", refresh=False)
            rows = read_watchlist_performance(path)
            self.assertEqual(rows[0]["ticker"], "005930")
            self.assertIsNone(rows[0]["entry_price"])
            self.assertTrue(manager.add("005930", "삼성전자", refresh=False)["already_exists"])
            manager.remove("005930")
            self.assertEqual(read_watchlist_performance(path), [])
            provider.assert_not_called()

    def test_fast_add_uses_stored_close_and_keeps_fixed_entry(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "test.db"
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.execute("CREATE TABLE model_ohlcv_daily(ticker TEXT,date TEXT,close INTEGER,change_rate REAL)")
                conn.execute("INSERT INTO model_ohlcv_daily VALUES ('005930','20260911',100,2)")
            manager = WatchlistManager(path, datetime(2026, 9, 11, 16, tzinfo=KST))
            manager.add("005930", "삼성전자", refresh=False)
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.execute("INSERT INTO model_ohlcv_daily VALUES ('005930','20260914',110,10)")
            row = read_watchlist_performance(path)[0]
            self.assertEqual(row["entry_price"], 100)
            self.assertEqual(row["current_price"], 110)
            self.assertAlmostEqual(row["total_return"], 10)

    def test_rolling_ten_dates_cross_monday_and_exclude_future(self):
        dates = pd.bdate_range("2026-08-20", "2026-09-15").strftime("%Y%m%d").tolist()
        frame = pd.DataFrame([dict(date=d, session="정규장(16:00)", category="VOLUME_TOP_60", ticker="005930") for d in dates])
        result, selected = recent_sector_window(pd.concat([frame, frame]), "20260914")
        self.assertEqual(selected, [d for d in dates if d <= "20260914"][-10:])
        self.assertEqual(len(result), 10)
        self.assertLess(selected[0], "20260914")

    def test_short_history_is_not_fabricated(self):
        frame = pd.DataFrame([dict(date="20260911", session="정규장(16:00)", category="VOLUME_TOP_60", ticker="005930")])
        self.assertEqual(recent_sector_window(frame, "20260911")[1], ["20260911"])
        self.assertEqual(recent_sector_window(frame, "20260910")[1], [])

    def test_precomputed_sector_summary_matches_existing_chart_calculation(self):
        rows = []
        for date in ("20260910", "20260911", "20260914"):
            rows.extend([
                dict(date=date, session="정규장(16:00)", category="VOLUME_TOP_60", ticker="1", name="알파", sector="반도체", trading_value=300, fluctuation_rate=2.345),
                dict(date=date, session="정규장(16:00)", category="VOLUME_TOP_60", ticker="2", name="베타", sector="반도체", trading_value=100, fluctuation_rate=-1),
                dict(date=date, session="정규장(16:00)", category="VOLUME_TOP_60", ticker="3", name="감마", sector="바이오", trading_value=400, fluctuation_rate=1),
            ])
        raw = pd.DataFrame(rows)
        window, old_dates = recent_sector_window(raw, "20260914")
        old_all = (
            window[window["sector"] != "기타"]
            .groupby(["date", "sector"])
            .agg(trading_value=("trading_value", "sum"), stock_count=("ticker", "nunique"), included_stocks=("name", lambda values: ", ".join(dict.fromkeys(values.astype(str)))))
            .reset_index()
        )
        old_all["trading_rank"] = old_all.groupby("date")["trading_value"].rank(method="min", ascending=False).astype(int)
        summary, new_dates = recent_sector_summary_window(build_sector_trend_summary(raw), "20260914")
        actual = summary[summary["trend_kind"] == "ALL"].drop(columns="trend_kind").sort_values(["date", "sector"]).reset_index(drop=True)
        expected = old_all.sort_values(["date", "sector"]).reset_index(drop=True)[actual.columns]
        pd.testing.assert_frame_equal(actual, expected, check_dtype=False)
        self.assertEqual(new_dates, old_dates)
        rising = summary[summary["trend_kind"] == "RISING"].set_index(["date", "sector"])
        self.assertEqual(rising.loc[("20260914", "반도체"), "included_stocks"], "알파 (+2.35%)")
