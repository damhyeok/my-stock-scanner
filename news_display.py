"""Materialized stock-news rows used by the Streamlit dashboard."""

from __future__ import annotations

import sqlite3
from typing import Tuple

import pandas as pd


SUMMARY_COLUMNS = [
    "date", "session", "display_order", "ticker", "name", "sector",
    "news_score", "news_count", "positive_count", "negative_count",
    "neutral_count", "keywords",
]
ARTICLE_COLUMNS = [
    "date", "session", "ticker", "article_order", "title", "link",
    "source", "published_at", "sentiment",
]


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def summarize_stock_news(frame: pd.DataFrame) -> pd.DataFrame:
    """Match the existing per-session dashboard summary exactly."""

    if frame.empty:
        return pd.DataFrame(columns=SUMMARY_COLUMNS)
    summaries = []
    for (date, session), rows in frame.groupby(["date", "session"], sort=False):
        summary = rows.groupby(["ticker", "name", "sector"]).agg(
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
        summary = summary.sort_values(
            ["news_score", "positive_count", "negative_count"],
            ascending=[False, False, True],
        )
        summary.insert(0, "session", session)
        summary.insert(0, "date", date)
        summary.insert(2, "display_order", range(len(summary)))
        summaries.append(summary[SUMMARY_COLUMNS])
    return pd.concat(summaries, ignore_index=True)


def build_stock_news_display(connection: sqlite3.Connection) -> dict[str, int]:
    """Build summary and top-five article tables before raw web rows are removed."""

    if not _table_exists(connection, "stock_news"):
        pd.DataFrame(columns=SUMMARY_COLUMNS).to_sql(
            "web_stock_news_summary", connection, if_exists="replace", index=False
        )
        pd.DataFrame(columns=ARTICLE_COLUMNS).to_sql(
            "web_stock_news_articles", connection, if_exists="replace", index=False
        )
        return {"source_rows": 0, "summary_rows": 0, "article_rows": 0}

    frame = pd.read_sql_query(
        "SELECT * FROM stock_news ORDER BY date DESC, published_at DESC", connection
    )
    summary = summarize_stock_news(frame)
    articles = frame.copy()
    articles["article_order"] = articles.groupby(
        ["date", "session", "ticker"], sort=False
    ).cumcount()
    articles = articles[articles["article_order"] < 5][ARTICLE_COLUMNS]
    summary.to_sql(
        "web_stock_news_summary", connection, if_exists="replace", index=False
    )
    articles.to_sql(
        "web_stock_news_articles", connection, if_exists="replace", index=False
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_web_news_summary_session "
        "ON web_stock_news_summary(date, session, display_order)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_web_news_articles_session "
        "ON web_stock_news_articles(date, session, ticker, article_order)"
    )
    return {
        "source_rows": len(frame),
        "summary_rows": len(summary),
        "article_rows": len(articles),
    }


def read_stock_news_display(
    connection: sqlite3.Connection, date: str, session: str
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Read one selected session; fall back to an older raw snapshot."""

    if (
        _table_exists(connection, "web_stock_news_summary")
        and _table_exists(connection, "web_stock_news_articles")
    ):
        summary = pd.read_sql_query(
            "SELECT * FROM web_stock_news_summary WHERE date=? AND session=? "
            "ORDER BY display_order",
            connection,
            params=(str(date), str(session)),
        ).drop(columns=["date", "session", "display_order"], errors="ignore")
        articles = pd.read_sql_query(
            "SELECT * FROM web_stock_news_articles WHERE date=? AND session=? "
            "ORDER BY ticker, article_order",
            connection,
            params=(str(date), str(session)),
        ).drop(columns=["article_order"], errors="ignore")
        return summary, articles

    if not _table_exists(connection, "stock_news"):
        return pd.DataFrame(), pd.DataFrame()
    # Preserve the historical tie order in an older bundled snapshot. New web
    # snapshots use the indexed compact tables and never take this full-read path.
    all_articles = pd.read_sql_query(
        "SELECT * FROM stock_news ORDER BY date DESC, published_at DESC", connection
    )
    articles = all_articles[
        (all_articles["date"].astype(str) == str(date))
        & (all_articles["session"].astype(str) == str(session))
    ].copy()
    return pd.DataFrame(), articles
