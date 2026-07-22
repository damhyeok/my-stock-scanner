"""Incremental intraday relative-strength scanner for the regular session."""

from __future__ import annotations

import math
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests


KST = timezone(timedelta(hours=9))
REGULAR_OPEN = "09:00"
REGULAR_CLOSE = "15:30"
INDEX_CODES = {"KOSPI": "0001", "KOSDAQ": "1001"}
MIN_MATCHED_BAR_RATIO = 0.95


def session_cutoff(session: str) -> str:
    """Return the regular-session cutoff represented by an analysis session."""
    match = re.search(r"\((\d{1,2}):(\d{2})\)", str(session))
    if not match:
        return REGULAR_CLOSE
    hour, minute = map(int, match.groups())
    value = max(9 * 60, min(15 * 60 + 30, hour * 60 + minute))
    return f"{value // 60:02d}:{value % 60:02d}"


def _number(value, default=None):
    parsed = pd.to_numeric(value, errors="coerce")
    return default if pd.isna(parsed) else float(parsed)


def _parse_intraday_time(value):
    """Normalize KIS HHMMSS values that may include separators or milliseconds."""
    digits = re.sub(r"\D", "", str(value or "").strip())
    if len(digits) < 4:
        return None
    normalized = (digits[:6] if len(digits) >= 6 else f"{digits[:4]}00")
    try:
        return datetime.strptime(normalized, "%H%M%S")
    except ValueError:
        return None


def classify_relative_strength(index_return, stock_return, excess_return, persistence):
    """Classify a stock using only information available at the cutoff."""
    if index_return >= 0:
        if excess_return > 0 and persistence >= 50:
            return "상승장 주도"
        return "상승장 동행"
    if stock_return > 0:
        return "하락장 역행"
    if excess_return > 0:
        return "하락장 방어"
    return "시장 대비 약세"


def _fixed_interval_return(joined, start_time, end_time):
    """Return the stock close-to-close move for an exact fixed minute interval."""
    start_rows = joined[joined["bar_time"].eq(start_time)]
    end_rows = joined[joined["bar_time"].eq(end_time)]
    if start_rows.empty or end_rows.empty:
        return None
    start_close = _number(start_rows.iloc[-1].get("close_stock"))
    end_close = _number(end_rows.iloc[-1].get("close_stock"))
    if not start_close or end_close is None:
        return None
    return float((end_close / start_close - 1) * 100)


def expected_regular_bars(cutoff_time):
    start = _parse_intraday_time(REGULAR_OPEN)
    cutoff = _parse_intraday_time(cutoff_time)
    if start is None or cutoff is None or cutoff < start:
        return 0
    return int((cutoff - start).total_seconds() // 60) + 1


def calculate_relative_strength(stock_bars, index_bars, trading_value=0):
    """Calculate point-in-time relative-strength features from aligned minute bars."""
    if stock_bars.empty or index_bars.empty:
        return None
    stock = stock_bars.copy().sort_values("bar_time")
    index = index_bars.copy().sort_values("bar_time")
    joined = stock.merge(
        index[["bar_time", "close", "change_rate"]],
        on="bar_time",
        how="inner",
        suffixes=("_stock", "_index"),
    )
    if joined.empty:
        return None

    for column in ("close_stock", "close_index", "change_rate_stock", "change_rate_index"):
        joined[column] = pd.to_numeric(joined[column], errors="coerce")
    joined = joined.dropna(subset=["close_stock", "close_index"])
    if joined.empty:
        return None

    stock_open = _number(stock.iloc[0].get("open"), _number(joined.iloc[0]["close_stock"], 0))
    index_open = _number(index.iloc[0].get("open"), _number(joined.iloc[0]["close_index"], 0))
    stock_open_returns = (joined["close_stock"] / stock_open - 1) * 100 if stock_open else 0
    index_open_returns = (joined["close_index"] / index_open - 1) * 100 if index_open else 0

    if joined["change_rate_stock"].notna().any():
        stock_returns = joined["change_rate_stock"].ffill().fillna(stock_open_returns)
    else:
        stock_returns = stock_open_returns
    if joined["change_rate_index"].notna().any():
        index_returns = joined["change_rate_index"].ffill().fillna(index_open_returns)
    else:
        index_returns = index_open_returns

    excess = stock_returns - index_returns
    end_stock_return = float(stock_returns.iloc[-1])
    end_index_return = float(index_returns.iloc[-1])
    end_excess = float(excess.iloc[-1])
    open_relative_return = float(stock_open_returns.iloc[-1] - index_open_returns.iloc[-1])
    persistence = float((excess > 0).mean() * 100)

    recent_start = max(0, len(excess) - 31)
    recent_change = float(excess.iloc[-1] - excess.iloc[recent_start])
    pre_close_30m_return = _fixed_interval_return(joined, "14:50", "15:20")
    closing_auction_return = _fixed_interval_return(joined, "15:20", "15:30")
    last_close = float(joined.iloc[-1]["close_stock"])
    high_close = float(joined["close_stock"].max())
    drawdown = max(0.0, (high_close - last_close) / high_close * 100) if high_close else 0.0
    liquidity_bonus = min(10.0, max(0.0, math.log10(max(float(trading_value), 1)) - 8) * 2.5)
    score = (
        end_excess * 20
        + persistence * 0.30
        + recent_change * 10
        - drawdown * 5
        + liquidity_bonus
    )
    classification = classify_relative_strength(
        end_index_return, end_stock_return, end_excess, persistence
    )
    return {
        "stock_return": end_stock_return,
        "index_return": end_index_return,
        "excess_return": end_excess,
        "open_relative_return": open_relative_return,
        "strength_persistence": persistence,
        "recent_30m_change": recent_change,
        "pre_close_30m_return": pre_close_30m_return,
        "closing_auction_return": closing_auction_return,
        "drawdown_from_high": drawdown,
        "score": score,
        "classification": classification,
        "matched_bars": int(len(joined)),
    }


class IntradayRelativeStrengthScanner:
    def __init__(self, crawler, db_path=None, request_interval=0.07):
        self.crawler = crawler
        self.db_path = db_path or crawler.db_path
        self.request_interval = request_interval
        self.token = crawler._get_kis_access_token()
        self.stock_url = (
            f"{crawler.kis_base_url}/uapi/domestic-stock/v1/quotations/"
            "inquire-time-itemchartprice"
        )
        self.index_url = (
            f"{crawler.kis_base_url}/uapi/domestic-stock/v1/quotations/"
            "inquire-time-indexchartprice"
        )
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS intraday_stock_bars (
                    trade_date TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    bar_time TEXT NOT NULL,
                    name TEXT,
                    market TEXT,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    change_rate REAL,
                    collected_at_kst TEXT,
                    PRIMARY KEY (trade_date, ticker, bar_time)
                );
                CREATE TABLE IF NOT EXISTS intraday_index_bars (
                    trade_date TEXT NOT NULL,
                    index_name TEXT NOT NULL,
                    index_code TEXT NOT NULL,
                    bar_time TEXT NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    change_rate REAL,
                    collected_at_kst TEXT,
                    PRIMARY KEY (trade_date, index_name, bar_time)
                );
                CREATE TABLE IF NOT EXISTS intraday_relative_strength_snapshots (
                    trade_date TEXT NOT NULL,
                    session TEXT NOT NULL,
                    cutoff_time TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    name TEXT,
                    market TEXT,
                    stock_return REAL,
                    index_return REAL,
                    excess_return REAL,
                    open_relative_return REAL,
                    strength_persistence REAL,
                    recent_30m_change REAL,
                    pre_close_30m_return REAL,
                    closing_auction_return REAL,
                    drawdown_from_high REAL,
                    trading_value REAL,
                    matched_bars INTEGER,
                    classification TEXT,
                    score REAL,
                    rank INTEGER,
                    collected_at_kst TEXT,
                    PRIMARY KEY (trade_date, session, ticker)
                );
                CREATE TABLE IF NOT EXISTS intraday_relative_strength_runs (
                    trade_date TEXT NOT NULL,
                    session TEXT NOT NULL,
                    cutoff_time TEXT NOT NULL,
                    universe_count INTEGER,
                    selected_count INTEGER,
                    failure_count INTEGER,
                    reconciled INTEGER,
                    completed_at_kst TEXT,
                    PRIMARY KEY (trade_date, session)
                );
                """
            )
            existing_snapshot_columns = {
                row[1] for row in conn.execute(
                    "PRAGMA table_info(intraday_relative_strength_snapshots)"
                ).fetchall()
            }
            for column in ("pre_close_30m_return", "closing_auction_return"):
                if column not in existing_snapshot_columns:
                    conn.execute(
                        f"ALTER TABLE intraday_relative_strength_snapshots ADD COLUMN {column} REAL"
                    )

    def _headers(self, tr_id):
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.token}",
            "appkey": self.crawler.kis_app_key,
            "appsecret": self.crawler.kis_app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    def _request(self, url, tr_id, params, tr_cont=""):
        last_error = None
        for attempt in range(3):
            try:
                headers = self._headers(tr_id)
                headers["tr_cont"] = tr_cont
                response = requests.get(url, headers=headers, params=params, timeout=12)
                body = response.json() if response.status_code == 200 else {}
                if response.status_code == 200 and body.get("rt_cd") == "0":
                    body = dict(body)
                    body["_response_tr_cont"] = response.headers.get("tr_cont", "")
                    time.sleep(self.request_interval)
                    return body
                last_error = RuntimeError(body.get("msg1") or response.text)
            except (requests.RequestException, ValueError) as error:
                last_error = error
            time.sleep(0.5 * (attempt + 1))
        raise RuntimeError(str(last_error) if last_error else "KIS 분봉 조회 실패")

    @staticmethod
    def _market_name(output1):
        name = str((output1 or {}).get("rprs_mrkt_kor_name", "")).upper()
        return "KOSDAQ" if "KOSDAQ" in name or "코스닥" in name else "KOSPI"

    def _last_stock_time(self, trade_date, ticker):
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute(
                "SELECT MAX(bar_time) FROM intraday_stock_bars WHERE trade_date=? AND ticker=?",
                (trade_date, ticker),
            ).fetchone()[0]

    def _last_index_time(self, trade_date, index_name):
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute(
                "SELECT MAX(bar_time) FROM intraday_index_bars WHERE trade_date=? AND index_name=?",
                (trade_date, index_name),
            ).fetchone()[0]

    @staticmethod
    def _next_minute(value):
        parsed = _parse_intraday_time(value)
        if parsed is None:
            return REGULAR_OPEN
        parsed += timedelta(minutes=1)
        return parsed.strftime("%H:%M")

    def _fetch_stock_delta(self, trade_date, ticker, name, start_time, cutoff_time):
        start = _parse_intraday_time(start_time)
        cursor = _parse_intraday_time(cutoff_time)
        if start is None or cursor is None:
            raise ValueError(f"잘못된 종목 분봉 시간 범위: {start_time}~{cutoff_time}")
        rows = {}
        market = "KOSPI"
        while cursor >= start:
            body = self._request(
                self.stock_url,
                "FHKST03010200",
                {
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": ticker,
                    "FID_INPUT_HOUR_1": cursor.strftime("%H%M%S"),
                    "FID_PW_DATA_INCU_YN": "Y",
                    "FID_ETC_CLS_CODE": "",
                },
            )
            market = self._market_name(body.get("output1", {}))
            output = body.get("output2", []) or []
            oldest = None
            for row in output:
                raw_time = str(row.get("stck_cntg_hour", "")).zfill(6)[:6]
                if not raw_time.isdigit():
                    continue
                bar_dt = _parse_intraday_time(raw_time)
                if bar_dt is None:
                    continue
                oldest = bar_dt if oldest is None or bar_dt < oldest else oldest
                if start <= bar_dt <= cursor:
                    rows[bar_dt.strftime("%H:%M")] = row
            if not output or oldest is None or oldest <= start:
                break
            cursor = oldest - timedelta(minutes=1)
        collected_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
        values = []
        for bar_time, row in rows.items():
            values.append(
                (
                    trade_date, ticker, bar_time, name, market,
                    _number(row.get("stck_oprc"), 0), _number(row.get("stck_hgpr"), 0),
                    _number(row.get("stck_lwpr"), 0), _number(row.get("stck_prpr"), 0),
                    _number(row.get("cntg_vol"), 0), _number(row.get("prdy_ctrt")),
                    collected_at,
                )
            )
        if values:
            with sqlite3.connect(self.db_path) as conn:
                conn.executemany(
                    """INSERT OR REPLACE INTO intraday_stock_bars
                    (trade_date,ticker,bar_time,name,market,open,high,low,close,volume,change_rate,collected_at_kst)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    values,
                )
        return market, len(values)

    @staticmethod
    def _index_price(row, *keys):
        for key in keys:
            value = _number(row.get(key))
            if value is not None:
                return value
        return 0.0

    def _fetch_index_delta(self, trade_date, index_name, start_time, cutoff_time):
        start = _parse_intraday_time(start_time)
        cutoff = _parse_intraday_time(cutoff_time)
        if start is None or cutoff is None:
            raise ValueError(f"잘못된 지수 분봉 시간 범위: {start_time}~{cutoff_time}")
        rows = {}
        previous_close = None
        seen_pages = set()
        request_tr_cont = ""
        for _ in range(20):
            body = self._request(
                self.index_url,
                "FHKUP03500200",
                {
                    "FID_COND_MRKT_DIV_CODE": "U",
                    "FID_ETC_CLS_CODE": "0",
                    "FID_INPUT_ISCD": INDEX_CODES[index_name],
                    # KIS 지수 API에서 이 값은 조회 종료시각이 아니라 분봉 간격(초)이다.
                    # API가 최근 약 98개만 반환하므로 장중 수집 타이머로 창을 겹쳐 저장한다.
                    "FID_INPUT_HOUR_1": "60",
                    "FID_PW_DATA_INCU_YN": "Y",
                },
                tr_cont=request_tr_cont,
            )
            summary = body.get("output1", {}) or {}
            previous_close = _number(summary.get("prdy_nmix"), previous_close)
            if previous_close is None:
                current = _number(summary.get("bstp_nmix_prpr"))
                current_rate = _number(summary.get("bstp_nmix_prdy_ctrt"))
                if current and current_rate is not None and current_rate > -100:
                    previous_close = current / (1 + current_rate / 100)
            output = body.get("output2", []) or []
            page_key = tuple(
                str(row.get("stck_cntg_hour", "")).zfill(6) for row in output
            )
            if not output or page_key in seen_pages:
                break
            seen_pages.add(page_key)
            for row in output:
                row_date = str(row.get("stck_bsop_date", "")).strip()
                if row_date and row_date != trade_date:
                    continue
                raw_time = str(row.get("stck_cntg_hour", ""))
                bar_dt = _parse_intraday_time(raw_time)
                if bar_dt is None:
                    continue
                if start <= bar_dt <= cutoff:
                    rows[bar_dt.strftime("%H:%M")] = row
            response_tr_cont = str(body.get("_response_tr_cont", "")).upper()
            if response_tr_cont not in {"M", "F"}:
                break
            request_tr_cont = "N"
        collected_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
        values = []
        for bar_time, row in rows.items():
            close = self._index_price(row, "bstp_nmix_prpr", "bstp_nmix", "stck_prpr")
            change_rate = (
                (close / previous_close - 1) * 100
                if close and previous_close
                else _number(row.get("bstp_nmix_prdy_ctrt"), _number(row.get("prdy_ctrt")))
            )
            values.append(
                (
                    trade_date, index_name, INDEX_CODES[index_name], bar_time,
                    self._index_price(row, "bstp_nmix_oprc", "stck_oprc"),
                    self._index_price(row, "bstp_nmix_hgpr", "stck_hgpr"),
                    self._index_price(row, "bstp_nmix_lwpr", "stck_lwpr"),
                    close, change_rate,
                    collected_at,
                )
            )
        if values:
            with sqlite3.connect(self.db_path) as conn:
                conn.executemany(
                    """INSERT OR REPLACE INTO intraday_index_bars
                    (trade_date,index_name,index_code,bar_time,open,high,low,close,change_rate,collected_at_kst)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    values,
                )
        return len(values)

    def collect_index_bars(self, trade_date=None, cutoff_time=None):
        """Persist the latest overlapping KOSPI/KOSDAQ minute-bar windows."""
        trade_date = trade_date or self.crawler.target_date
        cutoff_time = cutoff_time or datetime.now(KST).strftime("%H:%M")
        cutoff_time = min(max(cutoff_time, REGULAR_OPEN), REGULAR_CLOSE)
        collected = {}
        for index_name in INDEX_CODES:
            last_time = self._last_index_time(trade_date, index_name)
            start_time = REGULAR_OPEN if not last_time else self._next_minute(last_time)
            if start_time > cutoff_time:
                collected[index_name] = 0
                continue
            collected[index_name] = self._fetch_index_delta(
                trade_date, index_name, start_time, cutoff_time
            )
        return collected

    def _load_universe(self, trade_date, session):
        with sqlite3.connect(self.db_path) as conn:
            return pd.read_sql_query(
                """SELECT ticker, MAX(name) AS name, MAX(trading_value) AS trading_value
                FROM daily_stocks
                WHERE date=? AND session=? AND category='VOLUME_TOP_60'
                GROUP BY ticker ORDER BY trading_value DESC""",
                conn,
                params=(trade_date, session),
            )

    def _load_bars(self, table, trade_date, key_column, key, cutoff_time):
        with sqlite3.connect(self.db_path) as conn:
            return pd.read_sql_query(
                f"""SELECT * FROM {table}
                WHERE trade_date=? AND {key_column}=? AND bar_time BETWEEN ? AND ?
                ORDER BY bar_time""",
                conn,
                params=(trade_date, key, REGULAR_OPEN, cutoff_time),
            )

    def run(self, trade_date=None, session=None):
        trade_date = trade_date or self.crawler.target_date
        session = session or self.crawler._get_session_name()
        cutoff_time = session_cutoff(session)
        universe = self._load_universe(trade_date, session)
        if universe.empty:
            print(f"[Intraday RS] {trade_date} {session}: 거래대금 Top 60 데이터가 없습니다.")
            return pd.DataFrame()

        reconcile = cutoff_time == REGULAR_CLOSE and "16:00" in str(session)
        failures = 0
        markets = {}
        for index_name in INDEX_CODES:
            last_time = None if reconcile else self._last_index_time(trade_date, index_name)
            start_time = REGULAR_OPEN if not last_time else self._next_minute(last_time)
            if start_time <= cutoff_time:
                try:
                    self._fetch_index_delta(trade_date, index_name, start_time, cutoff_time)
                except Exception as error:
                    failures += 1
                    print(f"[Intraday RS Warning] {index_name} 분봉 수집 실패: {error}")

        for _, item in universe.iterrows():
            ticker = str(item["ticker"]).zfill(6)
            last_time = None if reconcile else self._last_stock_time(trade_date, ticker)
            start_time = REGULAR_OPEN if not last_time else self._next_minute(last_time)
            try:
                if start_time <= cutoff_time:
                    market, _ = self._fetch_stock_delta(
                        trade_date, ticker, item["name"], start_time, cutoff_time
                    )
                else:
                    bars = self._load_bars(
                        "intraday_stock_bars", trade_date, "ticker", ticker, cutoff_time
                    )
                    market = str(bars.iloc[-1]["market"]) if not bars.empty else "KOSPI"
                markets[ticker] = market
            except Exception as error:
                failures += 1
                print(f"[Intraday RS Warning] {ticker} 분봉 수집 실패: {error}")

        index_frames = {
            name: self._load_bars(
                "intraday_index_bars", trade_date, "index_name", name, cutoff_time
            )
            for name in INDEX_CODES
        }
        results = []
        coverage_failures = 0
        minimum_matched_bars = math.ceil(
            expected_regular_bars(cutoff_time) * MIN_MATCHED_BAR_RATIO
        )
        for _, item in universe.iterrows():
            ticker = str(item["ticker"]).zfill(6)
            stock_bars = self._load_bars(
                "intraday_stock_bars", trade_date, "ticker", ticker, cutoff_time
            )
            market = markets.get(ticker)
            if not market and not stock_bars.empty:
                market = str(stock_bars.iloc[-1].get("market") or "KOSPI")
            market = market if market in INDEX_CODES else "KOSPI"
            metrics = calculate_relative_strength(
                stock_bars, index_frames.get(market, pd.DataFrame()), item["trading_value"]
            )
            if metrics and metrics["matched_bars"] < minimum_matched_bars:
                coverage_failures += 1
                continue
            if metrics:
                results.append(
                    {
                        "ticker": ticker,
                        "name": item["name"],
                        "market": market,
                        "trading_value": float(item["trading_value"] or 0),
                        **metrics,
                    }
                )
        failures += coverage_failures
        if coverage_failures:
            print(
                f"[Intraday RS Warning] 비교 분봉 부족: {coverage_failures}개 종목 "
                f"(최소 {minimum_matched_bars}/{expected_regular_bars(cutoff_time)}개 필요)"
            )
        result = pd.DataFrame(results)
        if not result.empty:
            result = result.sort_values(
                ["score", "excess_return", "trading_value"], ascending=False
            ).reset_index(drop=True)
            result["rank"] = result.index + 1

        completed_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM intraday_relative_strength_snapshots WHERE trade_date=? AND session=?",
                (trade_date, session),
            )
            for _, row in result.iterrows():
                conn.execute(
                    """INSERT INTO intraday_relative_strength_snapshots
                    (trade_date,session,cutoff_time,ticker,name,market,stock_return,index_return,
                     excess_return,open_relative_return,strength_persistence,recent_30m_change,
                     pre_close_30m_return,closing_auction_return,drawdown_from_high,
                     trading_value,matched_bars,classification,score,rank,collected_at_kst)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        trade_date, session, cutoff_time, row["ticker"], row["name"], row["market"],
                        row["stock_return"], row["index_return"], row["excess_return"],
                        row["open_relative_return"], row["strength_persistence"],
                        row["recent_30m_change"], row["pre_close_30m_return"],
                        row["closing_auction_return"], row["drawdown_from_high"], row["trading_value"],
                        row["matched_bars"], row["classification"], row["score"], row["rank"],
                        completed_at,
                    ),
                )
            selected_count = int(
                result["classification"].isin(
                    ["상승장 주도", "하락장 역행", "하락장 방어"]
                ).sum()
            ) if not result.empty else 0
            conn.execute(
                """INSERT OR REPLACE INTO intraday_relative_strength_runs
                (trade_date,session,cutoff_time,universe_count,selected_count,failure_count,reconciled,completed_at_kst)
                VALUES (?,?,?,?,?,?,?,?)""",
                (
                    trade_date, session, cutoff_time, len(universe), selected_count,
                    failures, int(reconcile), completed_at,
                ),
            )
        print(
            f"[Intraday RS] {trade_date} {session}: {len(result)}/{len(universe)}개 분석, "
            f"강한 후보 {selected_count}개, 실패 {failures}개, reconcile={reconcile}"
        )
        return result
