import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests


class CloseBetScanner:
    """Scan liquid large-cap stocks for the requested close-bet chart setup."""

    MARKET_CAP_MIN = 500_000_000_000

    def __init__(self, crawler, db_path="stock_data.db"):
        self.crawler = crawler
        self.db_path = db_path
        self.kst = timezone(timedelta(hours=9))
        self._request_lock = threading.Lock()
        self._last_request_at = 0.0
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS close_bet_scans (
                    trade_date TEXT,
                    session TEXT,
                    ticker TEXT,
                    name TEXT,
                    grade TEXT,
                    market_cap INTEGER,
                    current_price INTEGER,
                    fluctuation_rate REAL,
                    volume_ratio REAL,
                    rsi REAL,
                    williams_r REAL,
                    scanned_at_kst TEXT,
                    PRIMARY KEY (trade_date, session, ticker)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS close_bet_scan_runs (
                    trade_date TEXT,
                    session TEXT,
                    scanned_count INTEGER,
                    selected_count INTEGER,
                    failed_count INTEGER,
                    completed_at_kst TEXT,
                    PRIMARY KEY (trade_date, session)
                )
                """
            )

    def _throttle(self):
        with self._request_lock:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < 0.07:
                time.sleep(0.07 - elapsed)
            self._last_request_at = time.monotonic()

    def _request(self, url, tr_id, params):
        self._throttle()
        token = self.crawler._get_kis_access_token()
        response = requests.get(
            url,
            headers={
                "content-type": "application/json; charset=utf-8",
                "authorization": f"Bearer {token}",
                "appkey": self.crawler.kis_app_key,
                "appsecret": self.crawler.kis_app_secret,
                "tr_id": tr_id,
                "custtype": "P",
            },
            params=params,
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("rt_cd") != "0":
            raise RuntimeError(payload.get("msg1") or "KIS API request failed")
        return payload

    def _load_universe(self, trade_date, session):
        token = self.crawler._get_kis_access_token()
        url = f"{self.crawler.kis_base_url}/uapi/domestic-stock/v1/ranking/market-cap"
        rows = []
        pending_ranges = [(0, 10_000_000)]
        processed_ranges = 0

        while pending_ranges:
            if processed_ranges >= 256:
                raise RuntimeError("KIS market-cap price-range split exceeded the safety limit")
            minimum_price, maximum_price = pending_ranges.pop()
            processed_ranges += 1
            self._throttle()
            headers = {
                "content-type": "application/json; charset=utf-8",
                "authorization": f"Bearer {token}",
                "appkey": self.crawler.kis_app_key,
                "appsecret": self.crawler.kis_app_secret,
                "tr_id": "FHPST01740000",
                "custtype": "P",
            }
            params = {
                "FID_INPUT_PRICE_2": str(maximum_price),
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_COND_SCR_DIV_CODE": "20174",
                "FID_DIV_CLS_CODE": "1",
                "FID_INPUT_ISCD": "0000",
                "FID_TRGT_CLS_CODE": "0",
                "FID_TRGT_EXLS_CLS_CODE": "0",
                "FID_INPUT_PRICE_1": str(minimum_price),
                "FID_VOL_CNT": "",
            }
            response = requests.get(url, headers=headers, params=params, timeout=15)
            response.raise_for_status()
            payload = response.json()
            if payload.get("rt_cd") != "0":
                raise RuntimeError(payload.get("msg1") or "KIS market-cap ranking failed")

            output = payload.get("output") or []
            if not output:
                print(
                    f"[Close Bet Universe] price={minimum_price:,}~{maximum_price:,}, "
                    "rows=0"
                )
                continue

            valid_market_caps = []
            for item in output:
                ticker = str(item.get("mksc_shrn_iscd", "")).zfill(6)
                name = str(item.get("hts_kor_isnm", "")).strip()
                current_price = pd.to_numeric(item.get("stck_prpr"), errors="coerce")
                listed_shares = pd.to_numeric(item.get("lstn_stcn"), errors="coerce")
                if not ticker.isdigit() or pd.isna(current_price) or pd.isna(listed_shares):
                    continue
                market_cap = int(current_price * listed_shares)
                valid_market_caps.append(market_cap)
                if market_cap >= self.MARKET_CAP_MIN:
                    rows.append({
                        "ticker": ticker,
                        "name": name,
                        "market_cap": market_cap,
                    })

            print(
                f"[Close Bet Universe] price={minimum_price:,}~{maximum_price:,}, "
                f"rows={len(output)}, qualified={len(rows)}"
            )
            bucket_may_be_truncated = (
                len(output) >= 30
                and valid_market_caps
                and min(valid_market_caps) >= self.MARKET_CAP_MIN
            )
            if bucket_may_be_truncated:
                if minimum_price >= maximum_price:
                    raise RuntimeError(
                        f"KIS market-cap bucket remained truncated at price {minimum_price}"
                    )
                midpoint = (minimum_price + maximum_price) // 2
                pending_ranges.append((minimum_price, midpoint))
                pending_ranges.append((midpoint + 1, maximum_price))

        frame = pd.DataFrame(rows).drop_duplicates("ticker") if rows else pd.DataFrame()
        if frame.empty:
            raise RuntimeError("KIS market-cap ranking returned no stocks above 500 billion won")
        frame = frame[["ticker", "name", "market_cap"]].sort_values(
            "market_cap", ascending=False
        )
        frame["ticker"] = frame["ticker"].astype(str).str.zfill(6)
        print(
            f"[Close Bet Universe] KIS large-cap stocks={len(frame)}, "
            f"price_ranges={processed_ranges}"
        )
        return frame

    def _fetch_daily_ohlcv(self, ticker, trade_date):
        end_date = datetime.strptime(trade_date, "%Y%m%d")
        start_date = end_date - timedelta(days=450)
        payload = self._request(
            f"{self.crawler.kis_base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            "FHKST03010100",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": ticker,
                "FID_INPUT_DATE_1": start_date.strftime("%Y%m%d"),
                "FID_INPUT_DATE_2": trade_date,
                "FID_PERIOD_DIV_CODE": "D",
                "FID_ORG_ADJ_PRC": "0",
            },
        )
        frame = pd.DataFrame(payload.get("output2") or [])
        required = ["stck_bsop_date", "stck_clpr", "stck_hgpr", "stck_lwpr", "stck_oprc", "acml_vol"]
        if frame.empty or any(column not in frame.columns for column in required):
            return None
        for column in required[1:]:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        return frame.sort_values("stck_bsop_date").drop_duplicates("stck_bsop_date").reset_index(drop=True)

    def _fetch_current_quote(self, ticker):
        payload = self._request(
            f"{self.crawler.kis_base_url}/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100",
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker},
        )
        output = payload.get("output") or {}
        return {
            "stck_clpr": pd.to_numeric(output.get("stck_prpr"), errors="coerce"),
            "stck_hgpr": pd.to_numeric(output.get("stck_hgpr"), errors="coerce"),
            "stck_lwpr": pd.to_numeric(output.get("stck_lwpr"), errors="coerce"),
            "stck_oprc": pd.to_numeric(output.get("stck_oprc"), errors="coerce"),
            "acml_vol": pd.to_numeric(output.get("acml_vol"), errors="coerce"),
        }

    @staticmethod
    def _calculate_indicators(frame):
        close = frame["stck_clpr"]
        high = frame["stck_hgpr"]
        low = frame["stck_lwpr"]
        volume = frame["acml_vol"]
        current, previous = frame.iloc[-1], frame.iloc[-2]

        diff = close.diff()
        average_gain = diff.where(diff > 0, 0).rolling(14).mean()
        average_loss = -diff.where(diff < 0, 0).rolling(14).mean()
        rsi = (100 - (100 / (1 + average_gain / (average_loss + 1e-9)))).iloc[-1]
        williams_r = (
            (high.rolling(14).max() - close)
            / (high.rolling(14).max() - low.rolling(14).min() + 1e-9)
            * -100
        ).iloc[-1]
        macd_line = close.ewm(span=12).mean() - close.ewm(span=26).mean()
        span_a = (
            high.rolling(9).max() + low.rolling(9).min()
            + high.rolling(26).max() + low.rolling(26).min()
        ).div(4).shift(26)
        span_b = (high.rolling(52).max() + low.rolling(52).min()).div(2).shift(26)
        cloud_top = max(span_a.iloc[-1], span_b.iloc[-1])

        direction = close.diff().apply(lambda value: 1 if value > 0 else (-1 if value < 0 else 0))
        obv = (volume * direction).fillna(0).cumsum()
        quiet_5 = close.iloc[-6:-1]
        return {
            "current": current,
            "close": close,
            "rsi": rsi,
            "williams_r": williams_r,
            "macd": macd_line.iloc[-1],
            "signal": macd_line.ewm(span=9).mean().iloc[-1],
            "cloud_top": cloud_top,
            "obv": obv,
            "obv_ma12": obv.rolling(12).mean().iloc[-1],
            "obv_ma20": obv.rolling(20).mean().iloc[-1],
            "volume_ratio": current["acml_vol"] / (volume.iloc[-6:-1].mean() + 1e-9) * 100,
            "volatility_5d": (quiet_5.max() - quiet_5.min()) / (quiet_5.min() + 1e-9) * 100,
            "is_breakout": current["stck_clpr"] > quiet_5.max(),
            "price_change": (current["stck_clpr"] / previous["stck_clpr"] - 1) * 100,
        }

    def _analyze_stock(self, ticker, name, market_cap, trade_date):
        frame = self._fetch_daily_ohlcv(ticker, trade_date)
        if frame is None or len(frame) < 80:
            return None

        if trade_date == datetime.now(self.kst).strftime("%Y%m%d"):
            quote = self._fetch_current_quote(ticker)
            for column, value in quote.items():
                if pd.notna(value) and value > 0:
                    frame.loc[frame.index[-1], column] = value

        indicators = self._calculate_indicators(frame)
        current_price = indicators["current"]["stck_clpr"]
        basic = (
            current_price > indicators["cloud_top"]
            and current_price > indicators["close"].iloc[-26]
            and indicators["rsi"] > 55
            and indicators["williams_r"] > -20
            and indicators["macd"] > indicators["signal"]
            and indicators["obv"].iloc[-1] > indicators["obv_ma12"]
            and indicators["obv"].iloc[-1] > indicators["obv_ma20"]
        )
        if not basic:
            return None

        breakout = (
            (indicators["volatility_5d"] < 7.5 and indicators["is_breakout"] and indicators["volume_ratio"] >= 140)
            or (indicators["obv"].iloc[-1] == indicators["obv"].iloc[-20:].max() and indicators["volume_ratio"] >= 140)
        )
        if breakout:
            grade = "S급(과열주의)" if indicators["price_change"] >= 10 or indicators["rsi"] >= 75 else "S급(최적타점)"
        elif indicators["volatility_5d"] < 7.5 and abs(indicators["price_change"]) < 2:
            grade = "A급(매복)"
        else:
            return None

        return {
            "ticker": ticker,
            "name": name,
            "grade": grade,
            "market_cap": int(market_cap),
            "current_price": int(current_price),
            "fluctuation_rate": round(float(indicators["price_change"]), 2),
            "volume_ratio": round(float(indicators["volume_ratio"]), 1),
            "rsi": round(float(indicators["rsi"]), 1),
            "williams_r": round(float(indicators["williams_r"]), 1),
        }

    def run(self, trade_date=None, session=None):
        trade_date = trade_date or self.crawler.target_date
        session = session or self.crawler._get_session_name()
        universe = self._load_universe(trade_date, session)
        if universe.empty:
            print(f"[Close Bet] {trade_date} {session}: no stocks above the market-cap threshold")
            return pd.DataFrame()

        print(f"[Close Bet] scanning {len(universe)} stocks")
        results = []
        failures = 0
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(
                    self._analyze_stock,
                    row.ticker,
                    row.name,
                    row.market_cap,
                    trade_date,
                ): row.ticker
                for row in universe.itertuples(index=False)
            }
            for future in as_completed(futures):
                try:
                    result = future.result()
                    if result:
                        results.append(result)
                except Exception as error:
                    failures += 1
                    print(f"[Close Bet Warning] {futures[future]}: {error}")

        result_frame = pd.DataFrame(results)
        scanned_at = datetime.now(self.kst).strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM close_bet_scans WHERE trade_date = ? AND session = ?",
                (trade_date, session),
            )
            for result in results:
                conn.execute(
                    """
                    INSERT INTO close_bet_scans (
                        trade_date, session, ticker, name, grade, market_cap,
                        current_price, fluctuation_rate, volume_ratio, rsi,
                        williams_r, scanned_at_kst
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        trade_date, session, result["ticker"], result["name"], result["grade"],
                        result["market_cap"], result["current_price"], result["fluctuation_rate"],
                        result["volume_ratio"], result["rsi"], result["williams_r"], scanned_at,
                    ),
                )
            conn.execute(
                """
                INSERT OR REPLACE INTO close_bet_scan_runs (
                    trade_date, session, scanned_count, selected_count,
                    failed_count, completed_at_kst
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (trade_date, session, len(universe), len(results), failures, scanned_at),
            )
        print(f"[Close Bet] selected={len(results)}, failed={failures}")
        return result_frame
