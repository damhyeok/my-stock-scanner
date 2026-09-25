import unittest
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

import pandas as pd

from rise_sector_history import build_rise_sector_history
from web_database import build_web_database


def rank_rows(date, groups, *, category="RISE_TOP_60"):
    rows = []
    for sector_index, (sector, count) in enumerate(groups):
        base = {"전력": 100000, "바이오": 200000, "반도체": 300000,
                "조선": 400000, "기타": 900000}.get(sector, 500000 + sector_index * 1000)
        for number in range(count):
            ticker = f"{base + number:06d}"
            rows.append({
                "date": date,
                "session": "정규장(16:00)",
                "category": category,
                "ticker": ticker,
                "name": f"{sector}{number}",
                "sector": sector,
                "fluctuation_rate": 10 - len(rows) / 100,
                "trading_value": 100_000_000,
            })
    return rows


def volume_rows(date, tickers):
    return [{
        "date": date,
        "session": "정규장(16:00)",
        "category": "VOLUME_TOP_60",
        "ticker": ticker,
        "trading_value": 100_000_000,
    } for ticker in tickers]


class RiseSectorHistoryTests(unittest.TestCase):
    def test_web_copy_keeps_only_small_real_trading_date_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory, "source.db"), Path(directory, "web.db")
            with closing(sqlite3.connect(source)) as conn, conn:
                conn.execute("CREATE TABLE intraday_index_bars (trade_date TEXT)")
                conn.execute("INSERT INTO intraday_index_bars VALUES ('20260923')")
                conn.execute("CREATE TABLE model_ohlcv_daily (date TEXT)")
                conn.execute("INSERT INTO model_ohlcv_daily VALUES ('20260922')")
            build_web_database(source, target)
            with closing(sqlite3.connect(target)) as conn:
                dates = conn.execute("SELECT date FROM web_trading_dates ORDER BY date").fetchall()
                self.assertEqual(dates, [("20260922",), ("20260923",)])
                tables = {row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )}
                self.assertNotIn("intraday_index_bars", tables)
                self.assertNotIn("model_ohlcv_daily", tables)

    def test_holidays_are_excluded_and_other_is_only_bottom_row(self):
        date = "20260923"
        rise = rank_rows(date, [("기타", 30), ("반도체", 15), ("전력", 10), ("바이오", 5)])
        volume = volume_rows(date, [row["ticker"] for row in rise])
        holiday = rank_rows("20260924", [("가짜 업종", 60)])
        frame = pd.DataFrame(rise + volume + holiday)
        result = build_rise_sector_history(frame, [date], "20260925")

        self.assertEqual(result["dates"], [date])
        self.assertNotIn("20260924", result["days"])
        for kind in ("rise", "overlap"):
            self.assertEqual(
                [group["sector"] for group in result["days"][date][kind]["leaders"]],
                ["반도체", "전력", "바이오"],
            )
        self.assertIsNone(result["days"][date]["rise"]["other"])
        self.assertEqual(result["days"][date]["overlap"]["other"]["count"], 30)

    def test_reentry_and_stock_continuity_use_trading_days(self):
        dates = ["20260917", "20260918", "20260921", "20260922"]
        groups = [
            [("전력", 25), ("바이오", 20), ("반도체", 15)],
            [("전력", 25), ("바이오", 20), ("반도체", 15)],
            [("조선", 30), ("바이오", 20), ("반도체", 10)],
            [("전력", 25), ("조선", 20), ("바이오", 15)],
        ]
        all_rows = []
        for date, sector_groups in zip(dates, groups):
            rise = rank_rows(date, sector_groups)
            all_rows += rise + volume_rows(date, [row["ticker"] for row in rise])
        result = build_rise_sector_history(pd.DataFrame(all_rows), dates, dates[-1])
        day2 = result["days"][dates[1]]["rise"]["leaders"]
        self.assertEqual(day2[0]["tracking"], "2거래일 연속")
        self.assertEqual(day2[0]["stocks"][0]["tracking"], "2거래일 연속")
        day4 = result["days"][dates[-1]]["rise"]["leaders"]
        self.assertEqual(day4[0]["sector"], "전력")
        self.assertEqual(day4[0]["tracking"], "2거래일 만에 재진입")
        self.assertEqual(day4[0]["stocks"][0]["tracking"], "2거래일 만에 재등장")

    def test_old_top30_and_partial_top60_do_not_create_false_leaders(self):
        dates = ["20260916", "20260917", "20260918"]
        old = rank_rows(dates[0], [("반도체", 30)], category="RISE_TOP_30")
        full = rank_rows(dates[1], [("전력", 60)])
        partial = rank_rows(dates[2], [("바이오", 59)])
        rows = old + full + partial
        rows += volume_rows(dates[1], [row["ticker"] for row in full])
        rows += volume_rows(dates[2], [row["ticker"] for row in partial] + ["999999"])
        result = build_rise_sector_history(pd.DataFrame(rows), dates, dates[-1])

        self.assertEqual(result["days"][dates[0]]["rise"]["reason"], "당시 TOP30만 수집")
        self.assertEqual(result["days"][dates[2]]["rise"]["reason"], "상승률 59/60 · 비교 제외")
        self.assertEqual(result["days"][dates[1]]["rise"]["leaders"][0]["sector"], "전력")

    def test_overlap_requires_complete_volume_rank(self):
        date = "20260923"
        rise = rank_rows(date, [("전력", 60)])
        rows = rise + volume_rows(date, [row["ticker"] for row in rise[:59]])
        result = build_rise_sector_history(pd.DataFrame(rows), [date], date)
        self.assertFalse(result["days"][date]["rise"]["reason"])
        self.assertEqual(result["days"][date]["overlap"]["reason"], "거래대금 59/60 · 비교 제외")


if __name__ == "__main__":
    unittest.main()
