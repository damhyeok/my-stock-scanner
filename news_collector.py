import sqlite3
import time
import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
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
        self.positive_weights = {
            "공급계약 체결": 4, "공급 계약 체결": 4, "대규모 수주": 4,
            "수주": 4, "낙찰": 4, "기술수출": 4, "기술 수출": 4,
            "라이선스 아웃": 4, "사상 최대": 3, "역대 최대": 3,
            "어닝서프라이즈": 3, "어닝 서프라이즈": 3, "흑자전환": 3,
            "흑자 전환": 3, "FDA 승인": 3, "품목허가": 3, "품목 허가": 3,
            "임상 성공": 3, "자사주 소각": 3, "배당 확대": 3,
            "목표주가 상향": 2, "전망 상향": 2, "가이던스 상향": 2,
            "증설": 2, "대규모 투자": 2, "신규 진출": 2,
            "전략적 제휴": 2, "업무협약": 2, "MOU": 2,
            "호조": 1, "성장": 1, "증가": 1, "반등": 1,
            "개선": 1, "회복": 1, "수혜": 1, "강세": 1,
        }
        self.negative_weights = {
            "계약 해지": -4, "계약해지": -4, "계약 취소": -4,
            "수주 취소": -4, "상장폐지": -4, "상장 폐지": -4,
            "횡령": -4, "배임": -4, "거래정지": -4, "거래 정지": -4,
            "적자전환": -3, "적자 전환": -3, "어닝쇼크": -3,
            "어닝 쇼크": -3, "리콜": -3, "제재": -3,
            "임상 실패": -3, "승인 거절": -3, "압수수색": -3,
            "전망 하향": -2, "목표주가 하향": -2, "감산": -2,
            "연기": -2, "지연": -2, "소송": -2, "유상증자": -2,
            "유상 증자": -2, "부진": -1, "감소": -1, "약세": -1,
            "하락": -1, "우려": -1, "손실": -1, "악화": -1,
        }
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
        positive_hits = [phrase for phrase in self.positive_weights if phrase in title]
        negative_hits = [phrase for phrase in self.negative_weights if phrase in title]
        strong_negative = any(self.negative_weights[phrase] <= -3 for phrase in negative_hits)
        positive_score = 0 if strong_negative else sum(self.positive_weights[phrase] for phrase in positive_hits)
        negative_score = sum(self.negative_weights[phrase] for phrase in negative_hits)
        score = max(-5, min(5, positive_score + negative_score))
        if score > 0:
            sentiment = "긍정"
        elif score < 0:
            sentiment = "부정"
        else:
            sentiment = "중립"
        keywords = ", ".join(dict.fromkeys(positive_hits + negative_hits))
        return sentiment, score, keywords

    @staticmethod
    def _normalize_title(title, source=""):
        text = str(title or "").strip()
        source_text = str(source or "").strip()
        if source_text and text.endswith(f" - {source_text}"):
            text = text[:-(len(source_text) + 3)]
        text = re.sub(r"\[[^\]]+\]|\([^)]*속보[^)]*\)", " ", text)
        text = re.sub(r"[^0-9A-Za-z가-힣]", "", text).lower()
        return text

    def _deduplicate_news(self, news_items, limit):
        unique_items = []
        seen_links = set()
        for news in news_items:
            link = str(news.get("link", "")).strip()
            normalized_title = self._normalize_title(
                news.get("title", ""), news.get("source", "")
            )
            if not normalized_title or (link and link in seen_links):
                continue

            sentiment, score, keywords = self._classify_news(news.get("title", ""))
            is_duplicate = False
            for saved in unique_items:
                if saved["sentiment"] != sentiment:
                    continue
                similarity = SequenceMatcher(
                    None, normalized_title, saved["normalized_title"]
                ).ratio()
                if similarity >= 0.88:
                    is_duplicate = True
                    break
            if is_duplicate:
                continue

            unique_items.append({
                **news,
                "normalized_title": normalized_title,
                "sentiment": sentiment,
                "sentiment_score": score,
                "keywords": keywords,
            })
            if link:
                seen_links.add(link)
            if len(unique_items) >= limit:
                break
        return unique_items

    def _fetch_google_news(self, name, max_items=5):
        query = quote_plus(f'"{name}" 주가 OR 실적 OR 수주 OR 투자 when:1d')
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
        target_news_date = datetime.strptime(target_date, "%Y%m%d").strftime("%Y-%m-%d")
        for _, stock in stocks.iterrows():
            name = str(stock.get("name", "")).strip()
            if not name:
                continue
            try:
                news_items = self._fetch_google_news(
                    name,
                    max_items=max(per_stock_limit * 3, 10),
                )
            except Exception as e:
                print(f"[News Warning] {name} 뉴스 수집 실패: {e}")
                continue

            today_candidates = [
                news for news in news_items
                if news.get("published_at", "").startswith(target_news_date)
            ]
            today_items = self._deduplicate_news(today_candidates, per_stock_limit)
            for news in today_items:
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
                        news["sentiment"],
                        news["sentiment_score"],
                        news["keywords"],
                        self.collected_at_kst,
                    )
                )
                saved_count += 1
            time.sleep(0.2)

        conn.commit()
        conn.close()
        print(f"[News] {target_date} {target_session} 뉴스 {saved_count}건 저장 완료")
        return saved_count
