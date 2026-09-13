import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import pandas as pd

from dashboard_session_display import build_dashboard_sessions, read_dashboard_sessions
from news_display import build_stock_news_display, read_stock_news_display
from web_database import build_web_database


NEWS_COLUMNS = [
    "date", "session", "ticker", "name", "sector", "title", "link",
    "source", "published_at", "sentiment", "sentiment_score", "keywords",
    "collected_at_kst",
]


def news_row(ticker, index, *, session="장중(14:50)", score=1):
    return (
        "20260911", session, ticker, f"종목{ticker}", "반도체",
        f"기사{ticker}-{index}", f"https://example.com/{ticker}/{index}", "테스트",
        f"2026-09-11 14:{59-index:02d}:00", "긍정" if score > 0 else "부정",
        score, "실적, 반도체", "2026-09-11 15:00:00",
    )


def legacy_summary(frame):
    result = frame.groupby(["ticker", "name", "sector"]).agg(
        news_score=("sentiment_score", "sum"),
        news_count=("title", "count"),
        positive_count=("sentiment", lambda values: int((values == "긍정").sum())),
        negative_count=("sentiment", lambda values: int((values == "부정").sum())),
        neutral_count=("sentiment", lambda values: int((values == "중립").sum())),
        keywords=("keywords", lambda values: ", ".join(dict.fromkeys(
            keyword.strip()
            for value in values.dropna().astype(str)
            for keyword in value.split(",")
            if keyword.strip()
        ))),
    ).reset_index()
    return result.sort_values(
        ["news_score", "positive_count", "negative_count"],
        ascending=[False, False, True],
    ).reset_index(drop=True)


class NewsDisplayTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        definitions = ",".join(f'"{column}" TEXT' for column in NEWS_COLUMNS)
        self.connection.execute(f"CREATE TABLE stock_news ({definitions})")
        rows = [news_row("005930", index) for index in range(7)]
        rows += [news_row("000660", index, score=-1) for index in range(2)]
        rows += [news_row("035420", 0, session="장중(09:30)")]
        self.connection.executemany(
            f"INSERT INTO stock_news VALUES ({','.join('?' for _ in NEWS_COLUMNS)})", rows
        )

    def tearDown(self):
        self.connection.close()

    def test_materialized_summary_and_top_five_match_legacy_view(self):
        receipt = build_stock_news_display(self.connection)
        summary, articles = read_stock_news_display(
            self.connection, "20260911", "장중(14:50)"
        )
        raw = pd.read_sql_query(
            "SELECT * FROM stock_news WHERE date='20260911' AND session='장중(14:50)' "
            "ORDER BY published_at DESC", self.connection
        )
        expected = legacy_summary(raw)
        self.assertEqual(summary.to_dict("records"), expected.to_dict("records"))
        for ticker in ("005930", "000660"):
            self.assertEqual(
                articles[articles["ticker"] == ticker]["title"].tolist(),
                raw[raw["ticker"] == ticker].head(5)["title"].tolist(),
            )
        self.assertEqual(receipt, {"source_rows": 10, "summary_rows": 3, "article_rows": 8})

    def test_raw_fallback_reads_only_selected_session(self):
        summary, articles = read_stock_news_display(
            self.connection, "20260911", "장중(09:30)"
        )
        self.assertTrue(summary.empty)
        self.assertEqual(articles["ticker"].tolist(), ["035420"])

    def test_web_build_replaces_raw_news_but_source_keeps_it(self):
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory, "source.db")
            target_path = Path(directory, "web.db")
            with closing(sqlite3.connect(source_path)) as source:
                definitions = ",".join(f'"{column}" TEXT' for column in NEWS_COLUMNS)
                source.execute(f"CREATE TABLE stock_news ({definitions})")
                source.execute(
                    f"INSERT INTO stock_news VALUES ({','.join('?' for _ in NEWS_COLUMNS)})",
                    news_row("005930", 0),
                )
                source.execute(
                    "CREATE TABLE daily_stocks (date TEXT, session TEXT, ticker TEXT, "
                    "name TEXT, market_cap REAL, category TEXT)"
                )
                source.execute(
                    "INSERT INTO daily_stocks VALUES (?,?,?,?,?,?)",
                    ("20260911", "장중(14:50)", "005930", "삼성전자", 100, "VOLUME_TOP_60"),
                )
                source.commit()
            build_web_database(source_path, target_path)
            with closing(sqlite3.connect(source_path)) as source:
                self.assertEqual(source.execute("SELECT COUNT(*) FROM stock_news").fetchone()[0], 1)
            with closing(sqlite3.connect(target_path)) as target:
                tables = {
                    row[0] for row in target.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                self.assertNotIn("stock_news", tables)
                self.assertIn("web_stock_news_summary", tables)
                self.assertIn("web_stock_news_articles", tables)
                self.assertIn("web_dashboard_sessions", tables)


class DashboardSessionDisplayTests(unittest.TestCase):
    def test_catalog_matches_existing_date_and_time_order(self):
        connection = sqlite3.connect(":memory:")
        connection.execute("CREATE TABLE daily_stocks (date TEXT, session TEXT)")
        connection.executemany(
            "INSERT INTO daily_stocks VALUES (?, ?)",
            [
                ("20260910", "장중(09:30)"),
                ("20260911", "장중(09:30)"),
                ("20260911", "정규장(16:00)"),
                ("20260911", "정규장(16:00)"),
                ("20260911", "시간외(18:00)"),
            ],
        )
        expected = build_dashboard_sessions(connection)
        actual = read_dashboard_sessions(connection)
        self.assertEqual(actual.to_dict("records"), expected.to_dict("records"))
        self.assertEqual(actual["date"].tolist(), ["20260911", "20260911", "20260910"])
        self.assertEqual(actual["session"].tolist()[:2], ["정규장(16:00)", "장중(09:30)"])
        connection.close()


if __name__ == "__main__":
    unittest.main()
