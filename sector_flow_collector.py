import argparse
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

from crawler import StockCrawler


KST = timezone(timedelta(hours=9))
WINDOWS = {
    "1100": ("09:00", "11:00"),
    "1300": ("11:00", "13:00"),
    "1500": ("13:00", "15:00"),
    "1530": ("15:00", "15:30"),
}


class SectorFlowCollector:
    def __init__(self, window_key=None, db_path="stock_data.db"):
        self.now = datetime.now(KST)
        self.trade_date = self.now.strftime("%Y%m%d")
        self.window_key = window_key or self._resolve_window_key()
        self.window_start, self.window_end = WINDOWS[self.window_key]
        self.db_path = db_path
        self.crawler = StockCrawler(db_path=db_path)
        self.token = self.crawler._get_kis_access_token()
        self.url = (
            f"{self.crawler.kis_base_url}/uapi/domestic-stock/v1/quotations/"
            "inquire-time-itemchartprice"
        )
        self._init_db()

    def _resolve_window_key(self):
        hhmm = self.now.strftime("%H%M")
        if hhmm <= "1109":
            return "1100"
        if hhmm <= "1309":
            return "1300"
        if hhmm <= "1509":
            return "1500"
        return "1530"

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sector_flow_windows (
                    trade_date TEXT,
                    window_key TEXT,
                    window_start TEXT,
                    window_end TEXT,
                    sector TEXT,
                    signed_flow REAL,
                    gross_turnover REAL,
                    signed_flow_per_minute REAL,
                    gross_turnover_per_minute REAL,
                    sector_return REAL,
                    advancing_count INTEGER,
                    stock_count INTEGER,
                    normalized_flow REAL,
                    relative_signed_flow REAL,
                    collected_at_kst TEXT,
                    PRIMARY KEY (trade_date, window_key, sector)
                )
                """
            )
            existing_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(sector_flow_windows)").fetchall()
            }
            for column in ("signed_flow_per_minute", "gross_turnover_per_minute"):
                if column not in existing_columns:
                    conn.execute(f"ALTER TABLE sector_flow_windows ADD COLUMN {column} REAL DEFAULT 0")

    def _load_universe(self):
        with sqlite3.connect(self.db_path) as conn:
            latest_date = conn.execute(
                "SELECT MAX(date) FROM daily_stocks WHERE date <= ? AND category = 'VOLUME_TOP_60'",
                (self.trade_date,),
            ).fetchone()[0]
            if not latest_date:
                return pd.DataFrame()
            session = conn.execute(
                """
                SELECT session
                FROM daily_stocks
                WHERE date = ? AND category = 'VOLUME_TOP_60'
                  AND session NOT LIKE '%시간외%'
                GROUP BY session
                ORDER BY MAX(collected_at_kst) DESC
                LIMIT 1
                """,
                (latest_date,),
            ).fetchone()
            if not session:
                return pd.DataFrame()
            return pd.read_sql(
                """
                SELECT ticker, MAX(name) AS name, MAX(sector) AS sector
                FROM daily_stocks
                WHERE date = ? AND session = ? AND category = 'VOLUME_TOP_60'
                GROUP BY ticker
                """,
                conn,
                params=(latest_date, session[0]),
            )

    @staticmethod
    def _to_time(value):
        text = str(value).zfill(6)
        return datetime.strptime(text[:6], "%H%M%S").time()

    def _fetch_bars(self, ticker):
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.token}",
            "appkey": self.crawler.kis_app_key,
            "appsecret": self.crawler.kis_app_secret,
            "tr_id": "FHKST03010200",
            "custtype": "P",
        }
        start_dt = datetime.combine(self.now.date(), datetime.strptime(self.window_start, "%H:%M").time(), KST)
        end_dt = datetime.combine(self.now.date(), datetime.strptime(self.window_end, "%H:%M").time(), KST)
        cursor = end_dt
        rows = []
        seen_times = set()
        while cursor >= start_dt:
            params = {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": ticker,
                "FID_INPUT_HOUR_1": cursor.strftime("%H%M%S"),
                "FID_PW_DATA_INCU_YN": "Y",
                "FID_ETC_CLS_CODE": "",
            }
            response = requests.get(self.url, headers=headers, params=params, timeout=10)
            body = response.json() if response.status_code == 200 else {}
            if response.status_code != 200 or body.get("rt_cd") != "0":
                print(f"[Sector Flow] {ticker} 분봉 조회 실패: {response.text}")
                break
            output = body.get("output2", [])
            if not output:
                break
            oldest = None
            for row in output:
                raw_time = str(row.get("stck_cntg_hour", "")).zfill(6)
                if not raw_time or raw_time in seen_times:
                    continue
                seen_times.add(raw_time)
                bar_dt = datetime.combine(self.now.date(), self._to_time(raw_time), KST)
                oldest = bar_dt if oldest is None or bar_dt < oldest else oldest
                if start_dt <= bar_dt < end_dt:
                    rows.append(row)
            if oldest is None or oldest <= start_dt:
                break
            cursor = oldest - timedelta(minutes=1)
            time.sleep(0.06)
        return rows

    def _summarize_stock(self, ticker, name, sector):
        rows = self._fetch_bars(ticker)
        if not rows:
            return None
        frame = pd.DataFrame(rows)
        for column in ("stck_oprc", "stck_prpr", "cntg_vol"):
            frame[column] = pd.to_numeric(frame.get(column, 0), errors="coerce").fillna(0)
        frame = frame.sort_values("stck_cntg_hour")
        frame["turnover"] = frame["stck_prpr"] * frame["cntg_vol"]
        direction = (frame["stck_prpr"] > frame["stck_oprc"]).astype(int) - (
            frame["stck_prpr"] < frame["stck_oprc"]
        ).astype(int)
        signed_flow = float((frame["turnover"] * direction).sum())
        gross_turnover = float(frame["turnover"].sum())
        first_open = float(frame.iloc[0]["stck_oprc"])
        last_close = float(frame.iloc[-1]["stck_prpr"])
        window_return = ((last_close / first_open) - 1) * 100 if first_open else 0
        return {
            "ticker": ticker,
            "name": name,
            "sector": sector or "기타",
            "signed_flow": signed_flow,
            "gross_turnover": gross_turnover,
            "window_return": window_return,
        }

    def _save(self, sector_frame):
        collected_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
        start = datetime.strptime(self.window_start, "%H:%M")
        end = datetime.strptime(self.window_end, "%H:%M")
        window_minutes = max(1, int((end - start).total_seconds() / 60))
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM sector_flow_windows WHERE trade_date = ? AND window_key = ?",
                (self.trade_date, self.window_key),
            )
            for _, row in sector_frame.iterrows():
                conn.execute(
                    """
                    INSERT INTO sector_flow_windows (
                        trade_date, window_key, window_start, window_end, sector,
                        signed_flow, gross_turnover, signed_flow_per_minute,
                        gross_turnover_per_minute, sector_return, advancing_count,
                        stock_count, normalized_flow, relative_signed_flow, collected_at_kst
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?)
                    """,
                    (
                        self.trade_date, self.window_key, self.window_start, self.window_end,
                        row["sector"], row["signed_flow"], row["gross_turnover"],
                        row["signed_flow"] / window_minutes,
                        row["gross_turnover"] / window_minutes,
                        row["sector_return"], row["advancing_count"], row["stock_count"],
                        collected_at,
                    ),
                )
            rows = conn.execute(
                "SELECT sector, window_key, signed_flow_per_minute FROM sector_flow_windows WHERE trade_date = ?",
                (self.trade_date,),
            ).fetchall()
            normalized = pd.DataFrame(rows, columns=["sector", "window_key", "signed_flow_per_minute"])
            for sector, group in normalized.groupby("sector"):
                minimum = group["signed_flow_per_minute"].min()
                maximum = group["signed_flow_per_minute"].max()
                absolute_max = group["signed_flow_per_minute"].abs().max()
                for _, item in group.iterrows():
                    value = item["signed_flow_per_minute"]
                    minmax = (value - minimum) / (maximum - minimum) if maximum != minimum else 0.5
                    relative = value / absolute_max if absolute_max else 0
                    conn.execute(
                        """
                        UPDATE sector_flow_windows
                        SET normalized_flow = ?, relative_signed_flow = ?
                        WHERE trade_date = ? AND window_key = ? AND sector = ?
                        """,
                        (minmax, relative, self.trade_date, item["window_key"], sector),
                    )

    def run(self):
        universe = self._load_universe()
        if universe.empty:
            raise RuntimeError("섹터 흐름 수집 대상 TOP60 데이터가 없습니다.")
        summaries = []
        for _, stock in universe.iterrows():
            summary = self._summarize_stock(stock["ticker"], stock["name"], stock["sector"])
            if summary:
                summaries.append(summary)
            time.sleep(0.06)
        if not summaries:
            raise RuntimeError("수집된 2시간 분봉 데이터가 없습니다.")
        stock_frame = pd.DataFrame(summaries)
        stock_frame["weighted_return"] = stock_frame["window_return"] * stock_frame["gross_turnover"]
        sector_frame = (
            stock_frame[stock_frame["sector"] != "기타"]
            .groupby("sector")
            .agg(
                signed_flow=("signed_flow", "sum"),
                gross_turnover=("gross_turnover", "sum"),
                weighted_return=("weighted_return", "sum"),
                advancing_count=("window_return", lambda values: int((values > 0).sum())),
                stock_count=("ticker", "nunique"),
            )
            .reset_index()
        )
        sector_frame["sector_return"] = (
            sector_frame["weighted_return"] / sector_frame["gross_turnover"]
        ).fillna(0)
        self._save(sector_frame)
        print(
            f"[Sector Flow] {self.trade_date} {self.window_start}~{self.window_end} "
            f"{len(sector_frame)}개 섹터 저장 완료"
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("window_key", nargs="?", choices=list(WINDOWS), default=None)
    parser.add_argument("--db-path", default="stock_data.db")
    args = parser.parse_args()
    SectorFlowCollector(args.window_key, args.db_path).run()


if __name__ == "__main__":
    main()
