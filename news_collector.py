import sqlite3
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET

import pandas as pd
import requests


class NewsCollector:
    def __init__(self, db_path="stock_data.db"):
        self.db_path = db_path
        self.kst = timezone(timedelta(hours=9))
        self.collected_at_kst = datetime.now(self.kst).strftime("%Y-%m-%d %H:%M:%S")
        self.positive_keywords = [
            "수주", "공급", "계약", "실적", "호조", "흑자", "상향", "증가", "성장",
            "강세", "급등", "반등", "기대", "승인", "신규", "확대", "투자", "개발",
            "수혜", "최대", "돌파", "개선", "회복", "선정"
        ]
        self.negative_keywords = [
            "하향", "부진", "적자", "감소", "급락", "약세", "하락", "우려", "리콜",
            "소송", "규제", "제재", "손실", "악화", "축소", "중단", "연기", "취소",
            "압수수색", "상장폐지", "횡령", "배임"
        ]
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stock_news (
                date TEXT,
                session TEXT,
                ticker TEXT,
                name TEXT,
                sector TEXT,
                title TEXT,
                link TEXT,
                source TEXT,
                published_at TEXT,
                sentiment TEXT,
                sentiment_score INTEGER,
                keywords TEXT,
                collected_at_kst TEXT,
                PRIMARY KEY (date, session, ticker, link)
            )
        """)
        conn.commit()
        conn.close()

    def _get_latest_stock_universe(self, limit=60):
        conn = sqlite3.connect(self.db_path)
        latest = pd.read_sql(
            """
            SELECT date, session
            FROM daily_stocks
            WHERE category = 'VOLUME_TOP_60'
              AND session NOT LIKE '%시간외%'
            GROUP BY date, session
            ORDER BY COALESCE(MAX(collected_at_kst), '') DESC, date DESC
            LIMIT 1
            """,
            conn
        )
        if latest.empty:
            conn.close()
            return None, None, pd.DataFrame()

        target_date = latest.loc[0, "date"]
        target_session = latest.loc[0, "session"]
        stocks = pd.read_sql(
            """
            SELECT DISTINCT ticker, name, sector, trading_value
            FROM daily_stocks
            WHERE date = ?
              AND session = ?
              AND category = 'VOLUME_TOP_60'
            ORDER BY trading_value DESC
            LIMIT ?
            """,
            conn,
            params=(target_date, target_session, limit)
        )
        conn.close()
        return target_date, target_session, stocks

    def _parse_published_at(self, value):
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=self.kst)
            return parsed.astimezone(self.kst).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return ""

    def _classify_news(self, title):
        positive_hits = [keyword for keyword in self.positive_keywords if keyword in title]
        negative_hits = [keyword for keyword in self.negative_keywords if keyword in title]
        score = len(positive_hits) - len(negative_hits)
        if score > 0:
            sentiment = "긍정"
        elif score < 0:
            sentiment = "부정"
        else:
            sentiment = "중립"
        keywords = ", ".join(dict.fromkeys(positive_hits + negative_hits))
        return sentiment, score, keywords

    def _fetch_google_news(self, name, max_items=5):
        query = quote_plus(f'"{name}" 주가 OR 실적 OR 수주 OR 투자')
        url = f"https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko"
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(url, headers=headers, timeout=10)
        res.raise_for_status()

        root = ET.fromstring(res.content)
        items = []
        for item in root.findall("./channel/item")[:max_items]:
            title = item.findtext("title", default="").strip()
            link = item.findtext("link", default="").strip()
            source = item.findtext("source", default="").strip()
            published_at = self._parse_published_at(item.findtext("pubDate", default=""))
            if not title or not link:
                continue
            items.append({
                "title": title,
                "link": link,
                "source": source,
                "published_at": published_at,
            })
        return items

    def run(self, per_stock_limit=3, stock_limit=60):
        target_date, target_session, stocks = self._get_latest_stock_universe(limit=stock_limit)
        if stocks.empty:
            print("[News] 뉴스 수집 대상 종목이 없습니다.")
            return 0

        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "DELETE FROM stock_news WHERE date = ? AND session = ?",
            (target_date, target_session)
        )

        saved_count = 0
        for _, stock in stocks.iterrows():
            name = str(stock.get("name", "")).strip()
            if not name:
                continue
            try:
                news_items = self._fetch_google_news(name, max_items=per_stock_limit)
            except Exception as e:
                print(f"[News Warning] {name} 뉴스 수집 실패: {e}")
                continue

            for news in news_items:
                sentiment, score, keywords = self._classify_news(news["title"])
                conn.execute(
                    """
                    INSERT OR REPLACE INTO stock_news
                    (date, session, ticker, name, sector, title, link, source, published_at,
                     sentiment, sentiment_score, keywords, collected_at_kst)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        target_date,
                        target_session,
                        str(stock.get("ticker", "")),
                        name,
                        str(stock.get("sector", "")),
                        news["title"],
                        news["link"],
                        news["source"],
                        news["published_at"],
                        sentiment,
                        score,
                        keywords,
                        self.collected_at_kst,
                    )
                )
                saved_count += 1
            time.sleep(0.2)

        conn.commit()
        conn.close()
        print(f"[News] {target_date} {target_session} 뉴스 {saved_count}건 저장 완료")
        return saved_count
