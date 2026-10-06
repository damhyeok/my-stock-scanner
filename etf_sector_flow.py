"""Small, independent ETF observations; never mix these with stock rankings."""
from datetime import datetime, timedelta
import math
import sqlite3
import time

import pandas as pd
import requests

from market_calendar import KST, is_krx_closed
from analysis_schedule import FULL_ANALYSIS_SLOTS

# Fixed observation proxies, not a promise of the largest fund in every category.
ETF_UNIVERSE = (
    ("반도체", "091160", "KODEX 반도체", "반도체 업종 전체"),
    ("반도체 전공정", "475300", "SOL 반도체전공정", "전공정 관련 기업"),
    ("반도체 후공정", "475310", "SOL 반도체후공정", "기판 관련 기업도 포함"),
    ("전력", "487240", "KODEX AI전력핵심설비", "전력기기·전선 중심"),
    ("원전", "0091P0", "TIGER 코리아원자력", "원전 관련 건설주도 포함"),
    ("방산", "449450", "PLUS K방산", "조선 관련 종목과 일부 중복"),
    ("조선", "466920", "SOL 조선TOP3플러스", "주요 조선사와 관련 기업"),
    ("2차전지", "305720", "KODEX 2차전지산업", "배터리 산업 전반"),
    ("로봇", "445290", "KODEX 로봇액티브", "대형 IT 기업도 포함·액티브"),
    ("바이오·제약", "364970", "TIGER 바이오TOP10", "대형 바이오 기업 중심"),
    ("금융·은행", "091170", "KODEX 은행", "은행·금융지주 중심"),
    ("화장품", "228790", "TIGER 화장품", "화장품 관련 기업"),
    ("엔터", "475050", "ACE KPOP포커스", "주요 K팝 엔터사 중심"),
    ("게임", "300950", "KODEX 게임산업", "게임 관련 기업"),
    ("식품", "438900", "HANARO Fn K-푸드", "식품 관련 기업"),
    ("데이터센터", "0222F0", "마이티 AI데이터센터밸류체인", "반도체·전력·IT도 포함"),
    ("광통신·위성", "0219B0", "KoAct 광통신&위성네트워크액티브", "위성 산업도 포함·액티브"),
    ("에너지·화학", "139250", "TIGER 200 에너지화학", "순수 정유 ETF가 아님"),
)


def number(value):
    try:
        result = float(str(value).replace(",", ""))
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def init_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS etf_sector_snapshots (
            trade_date TEXT, session TEXT, ticker TEXT, sector TEXT, name TEXT,
            price REAL, change_rate REAL, trading_value REAL, kospi_rate REAL,
            collected_at_kst TEXT, PRIMARY KEY(trade_date, session, ticker));
        CREATE TABLE IF NOT EXISTS etf_sector_daily (
            trade_date TEXT, ticker TEXT, close REAL, trading_value REAL,
            PRIMARY KEY(trade_date, ticker));
        CREATE TABLE IF NOT EXISTS etf_sector_holdings (
            trade_date TEXT, session TEXT, etf_ticker TEXT, ticker TEXT, name TEXT,
            weight REAL, price REAL, change_rate REAL,
            PRIMARY KEY(trade_date, session, etf_ticker, ticker));
        CREATE TABLE IF NOT EXISTS etf_sector_runs (
            trade_date TEXT, session TEXT, expected_count INTEGER, success_count INTEGER,
            status TEXT, errors TEXT, collected_at_kst TEXT,
            PRIMARY KEY(trade_date, session));
    """)


class EtfSectorCollector:
    def __init__(self, crawler, universe=ETF_UNIVERSE):
        self.crawler = crawler
        self.universe = universe
        self.last_request = 0
        with sqlite3.connect(crawler.db_path) as conn:
            init_tables(conn)

    def request(self, path, tr_id, params):
        # Keep the extra traffic small and sequential. No new threads/timers.
        elapsed = time.monotonic() - self.last_request
        if elapsed < .12:
            time.sleep(.12 - elapsed)
        self.last_request = time.monotonic()
        response = requests.get(
            self.crawler.kis_base_url + path,
            headers={"authorization": "Bearer " + self.crawler._get_kis_access_token(),
                     "appkey": self.crawler.kis_app_key,
                     "appsecret": self.crawler.kis_app_secret,
                     "tr_id": tr_id, "custtype": "P"},
            params=params, timeout=8,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("rt_cd") != "0":
            # Do not log server responses or credentials.
            raise RuntimeError("KIS ETF 조회 실패: " + str(payload.get("msg_cd", "unknown")))
        return payload

    def history(self, ticker, day):
        start = datetime.strptime(day, "%Y%m%d") - timedelta(days=100)
        payload = self.request(
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice", "FHKST03010100",
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker,
             "FID_INPUT_DATE_1": start.strftime("%Y%m%d"), "FID_INPUT_DATE_2": day,
             "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"},
        )
        records = []
        latest = None
        for row in payload.get("output2") or []:
            date = str(row.get("stck_bsop_date", ""))
            close = number(row.get("stck_clpr"))
            if len(date) != 8 or not date.isdigit() or not close or date > day:
                continue
            if is_krx_closed(datetime.strptime(date, "%Y%m%d").date()):
                continue
            latest = max(latest or date, date)
            records.append((date, ticker, close, number(row.get("acml_tr_pbmn"))))
        return latest, records

    def seed_history(self, day):
        """Real daily history only. Never fabricate historical intraday snapshots."""
        counts = {}
        for _, ticker, _, _ in self.universe:
            _, records = self.history(ticker, day)
            with sqlite3.connect(self.crawler.db_path) as conn:
                conn.executemany("INSERT OR REPLACE INTO etf_sector_daily VALUES (?,?,?,?)", records)
            counts[ticker] = len(records)
        return counts

    def run(self):
        now = datetime.now(KST)
        if is_krx_closed(now.date()) or self.crawler.target_date != now.strftime("%Y%m%d"):
            return {"status": "market_closed", "success_count": 0}
        day, session = str(self.crawler.target_date), self.crawler._get_session_name()
        if session not in {s for _, _, s in FULL_ANALYSIS_SLOTS}:
            return {"status": "unsupported_session", "success_count": 0}
        self.crawler._get_kis_access_token()
        deadline = time.monotonic() + 180
        snapshots, holdings, errors = [], [], []
        stamp = now.strftime("%Y-%m-%d %H:%M:%S")
        try:
            output = self.request(
                "/uapi/domestic-stock/v1/quotations/inquire-index-price", "FHPUP02100000",
                {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": "0001"},
            ).get("output") or {}
            kospi = number(output.get("bstp_nmix_prdy_ctrt"))
        except Exception:
            kospi = None
            errors.append("코스피 조회 실패")
        for sector, ticker, name, _ in self.universe:
            if time.monotonic() > deadline:
                errors.append("ETF 수집 시간 제한: 나머지는 다음 분석에서 재시도")
                break
            try:
                # Refresh daily history once on the first observation each day.
                with sqlite3.connect(self.crawler.db_path) as conn:
                    seen = conn.execute("SELECT 1 FROM etf_sector_snapshots WHERE trade_date=? AND ticker=? LIMIT 1",
                                        (day, ticker)).fetchone()
                if not seen:
                    latest, daily = self.history(ticker, day)
                    if latest != day:
                        raise RuntimeError("당일 시세 미확인")
                    with sqlite3.connect(self.crawler.db_path) as conn:
                        conn.executemany("INSERT OR REPLACE INTO etf_sector_daily VALUES (?,?,?,?)",
                                         [r for r in daily if r[0] < day])
                quote = self.request(
                    "/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100",
                    {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker},
                ).get("output") or {}
                price, rate, value = (number(quote.get(k)) for k in
                                      ("stck_prpr", "prdy_ctrt", "acml_tr_pbmn"))
                if not price or rate is None or value is None:
                    raise RuntimeError("필수 시세 누락")
                snapshots.append((day, session, ticker, sector, name, price, rate, value, kospi,
                                  datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")))
                if session == "정규장(16:00)":
                    with sqlite3.connect(self.crawler.db_path) as conn:
                        conn.execute("INSERT OR REPLACE INTO etf_sector_daily VALUES (?,?,?,?)",
                                     (day, ticker, price, value))
            except Exception as error:
                errors.append(f"{name}: {type(error).__name__}")
                continue
            try:
                rows = self.request(
                    "/uapi/etfetn/v1/quotations/inquire-component-stock-price", "FHKST121600C0",
                    {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker,
                     "FID_COND_SCR_DIV_CODE": "11216"},
                ).get("output2") or []
                for r in rows:
                    code = str(r.get("stck_shrn_iscd", ""))
                    if code:
                        holdings.append((day, session, ticker, code, r.get("hts_kor_isnm", ""),
                                         number(r.get("etf_cnfg_issu_rlim")), number(r.get("stck_prpr")),
                                         number(r.get("prdy_ctrt"))))
            except Exception:
                errors.append(f"{name}: 구성종목 조회 실패")
        status = "success" if not errors else ("partial" if snapshots else "failed")
        with sqlite3.connect(self.crawler.db_path) as conn:
            # One transaction per session; a rerun must not leave old successful rows mixed in.
            conn.execute("DELETE FROM etf_sector_snapshots WHERE trade_date=? AND session=?", (day, session))
            conn.execute("DELETE FROM etf_sector_holdings WHERE trade_date=? AND session=?", (day, session))
            conn.executemany("INSERT INTO etf_sector_snapshots VALUES (?,?,?,?,?,?,?,?,?,?)", snapshots)
            conn.executemany("INSERT INTO etf_sector_holdings VALUES (?,?,?,?,?,?,?,?)", holdings)
            conn.execute("INSERT OR REPLACE INTO etf_sector_runs VALUES (?,?,?,?,?,?,?)",
                         (day, session, len(self.universe), len(snapshots), status, "; ".join(errors), stamp))
        return {"status": status, "success_count": len(snapshots), "errors": errors}


def build_ranking(snapshots, daily, day, session):
    if snapshots.empty:
        return pd.DataFrame()
    result = snapshots[(snapshots.trade_date == day) & (snapshots.session == session)].copy()
    result = result.sort_values(["change_rate", "ticker"], ascending=[False, True]).reset_index(drop=True)
    result["rank"] = range(1, len(result) + 1)
    result["excess_rate"] = result.change_rate - result.kospi_rate
    result["trading_eok"] = result.trading_value / 1e8
    for index, row in result.iterrows():
        # No future dates, other ETF products, or other collection slots in the baseline.
        previous = snapshots[(snapshots.ticker == row.ticker) & (snapshots.trade_date < day)
                             & (snapshots.session == session)].sort_values("trade_date").tail(20)
        previous = previous[pd.to_numeric(previous.trading_value, errors="coerce") > 0]
        # Avoid comparing severely delayed observations with on-time collections.
        current_time = pd.to_datetime(row.collected_at_kst, errors="coerce")
        times = pd.to_datetime(previous.collected_at_kst, errors="coerce")
        if pd.notna(current_time):
            difference = (times.dt.hour * 60 + times.dt.minute - current_time.hour * 60 - current_time.minute).abs()
            previous = previous[difference <= 5]
        count = len(previous)
        result.loc[index, "baseline_days"] = count
        result.loc[index, "trading_multiple"] = row.trading_value / previous.trading_value.mean() if count >= 5 else float("nan")
        history = daily[(daily.ticker == row.ticker) & (daily.trade_date < day)].sort_values("trade_date") if not daily.empty else pd.DataFrame()
        result.loc[index, "return_5d"] = (row.price / history.iloc[-5].close - 1) * 100 if len(history) >= 5 else float("nan")
    return result


def build_trend(daily, ranking, day, tickers):
    if daily.empty or ranking.empty:
        return pd.DataFrame()
    dates = sorted(daily.loc[daily.trade_date < day, "trade_date"].unique())[-9:] + [day]
    records = []
    for ticker in tickers:
        selected = ranking[ranking.ticker == ticker]
        if selected.empty:
            continue
        row = selected.iloc[0]
        points = daily[(daily.ticker == ticker) & daily.trade_date.isin(dates[:-1])].sort_values("trade_date")
        series = [(r.trade_date, r.close) for r in points.itertuples()] + [(day, row.price)]
        if series[0][0] != dates[0]:
            continue  # Never compare lines with different initial dates.
        base = series[0][1]
        for date, price in series:
            records.append({"date": date, "sector": row.sector, "return": (price / base - 1) * 100})
    return pd.DataFrame(records)


def build_daily_return_trend(daily, ranking, day):
    """Selected-slot top eight; past close-to-close, current reported daily change."""
    if ranking.empty:
        return pd.DataFrame()
    history = daily[daily.trade_date.astype(str) < str(day)].copy() if not daily.empty else pd.DataFrame()
    dates = sorted(history.trade_date.astype(str).unique()) if not history.empty else []
    shown_dates = dates[-9:] + [str(day)]
    previous = {date: dates[i-1] if i else None for i, date in enumerate(dates)}
    prices = {(str(r.trade_date), str(r.ticker)): number(r.close) for r in history.itertuples()}
    records = []
    top = ranking.sort_values(['change_rate', 'ticker'], ascending=[False, True]).head(8)
    for row in top.itertuples():
        for date in shown_dates:
            rate = None
            if date == str(day):
                rate = number(row.change_rate)
            else:
                price = prices.get((date, str(row.ticker)))
                base = prices.get((previous.get(date), str(row.ticker)))
                if price is not None and price > 0 and base is not None and base > 0:
                    rate = (price / base - 1) * 100
            records.append({'date': date, 'sector': row.sector, 'ticker': row.ticker,
                            'daily_return': rate})
    return pd.DataFrame(records)
