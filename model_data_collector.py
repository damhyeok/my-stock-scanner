import argparse
import hashlib
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

from model_schema import init_model_tables


KST = timezone(timedelta(hours=9))
load_dotenv()


class ModelDataCollector:
    """Collect model-only market data without touching dashboard service tables."""

    PRODUCT_KEYWORDS = (
        "KODEX", "TIGER", "ACE", "SOL", "PLUS", "RISE", "HANARO",
        "KOSEF", "ARIRANG", "KBSTAR", "KINDEX", "TREX", "TIMEFOLIO",
        "FOCUS", "WOORI", "1Q", "ETF", "ETN", "SPAC",
    )

    def __init__(self, db_path="stock_data.db"):
        self.db_path = db_path
        self.kis_app_key = os.environ.get("KIS_APP_KEY", "")
        self.kis_app_secret = os.environ.get("KIS_APP_SECRET", "")
        self.kis_base_url = "https://openapi.koreainvestment.com:9443"
        self.access_token = None
        self.collected_at_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
        self.snapshot_date = datetime.now(KST).strftime("%Y%m%d")
        init_model_tables(db_path=db_path)

    def _token(self):
        if self.access_token:
            return self.access_token
        if not self.kis_app_key or not self.kis_app_secret:
            raise ValueError("KIS_APP_KEY and KIS_APP_SECRET must be set in .env")

        cache_path = Path(os.environ.get(
            "KIS_TOKEN_CACHE",
            Path(__file__).resolve().parent / ".kis_token_cache.json",
        ))
        app_key_hash = hashlib.sha256(self.kis_app_key.encode("utf-8")).hexdigest()
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if (
                cached.get("app_key_hash") == app_key_hash
                and float(cached.get("expires_at", 0)) > time.time() + 300
                and cached.get("access_token")
            ):
                self.access_token = cached["access_token"]
                return self.access_token
        except (FileNotFoundError, ValueError, json.JSONDecodeError):
            pass

        response = requests.post(
            f"{self.kis_base_url}/oauth2/tokenP",
            headers={"content-type": "application/json"},
            json={
                "grant_type": "client_credentials",
                "appkey": self.kis_app_key,
                "appsecret": self.kis_app_secret,
            },
            timeout=20,
        )
        response.raise_for_status()
        body = response.json()
        self.access_token = body.get("access_token")
        expires_in = int(body.get("expires_in", 23 * 60 * 60))
        cache_path.write_text(
            json.dumps({
                "app_key_hash": app_key_hash,
                "access_token": self.access_token,
                "expires_at": time.time() + expires_in,
            }),
            encoding="utf-8",
        )
        if os.name != "nt":
            cache_path.chmod(0o600)
        return self.access_token

    def _headers(self, tr_id):
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._token()}",
            "appkey": self.kis_app_key,
            "appsecret": self.kis_app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    def _get(self, path, tr_id, params):
        last_error = None
        for attempt in range(1, 4):
            try:
                response = requests.get(
                    f"{self.kis_base_url}{path}",
                    headers=self._headers(tr_id),
                    params=params,
                    timeout=20,
                )
                response.raise_for_status()
                payload = response.json()
                if payload.get("rt_cd") != "0":
                    raise RuntimeError(payload.get("msg1") or "KIS API request failed")
                return payload
            except (requests.RequestException, RuntimeError) as error:
                last_error = error
                if attempt == 3:
                    break
                time.sleep(0.8 * attempt)
        raise last_error

    @classmethod
    def _is_product(cls, name):
        name_upper = str(name or "").upper()
        return any(keyword in name_upper for keyword in cls.PRODUCT_KEYWORDS)

    def fetch_market_cap_top(self, limit=100):
        universe = self.fetch_market_cap_universe(limit=limit)
        return universe

    def fetch_market_cap_universe(self, min_market_cap=None, limit=None):
        rows = []
        pending_ranges = [(0, 10_000_000)]
        processed_ranges = 0

        while pending_ranges:
            if processed_ranges >= 256:
                raise RuntimeError("KIS market-cap range split exceeded safety limit")
            min_price, max_price = pending_ranges.pop()
            processed_ranges += 1
            payload = self._get(
                "/uapi/domestic-stock/v1/ranking/market-cap",
                "FHPST01740000",
                {
                    "FID_INPUT_PRICE_2": str(max_price),
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_COND_SCR_DIV_CODE": "20174",
                    "FID_DIV_CLS_CODE": "1",
                    "FID_INPUT_ISCD": "0000",
                    "FID_TRGT_CLS_CODE": "0",
                    "FID_TRGT_EXLS_CLS_CODE": "0000001101",
                    "FID_INPUT_PRICE_1": str(min_price),
                    "FID_VOL_CNT": "",
                },
            )
            output = payload.get("output") or []
            valid_caps = []
            for item in output:
                ticker = str(item.get("mksc_shrn_iscd", "")).zfill(6)
                name = str(item.get("hts_kor_isnm", "")).strip()
                if not ticker.isdigit() or self._is_product(name):
                    continue
                current_price = self._to_float(item.get("stck_prpr"))
                listed_shares = self._to_float(item.get("lstn_stcn"))
                if current_price is None or listed_shares is None:
                    continue
                market_cap = int(current_price * listed_shares)
                valid_caps.append(market_cap)
                rows.append(
                    {
                        "ticker": ticker,
                        "name": name,
                        "market_cap": market_cap,
                        "current_price": int(current_price),
                        "listed_shares": int(listed_shares),
                    }
                )

            bucket_may_be_truncated = len(output) >= 30 and valid_caps
            if bucket_may_be_truncated and min_price < max_price:
                midpoint = (min_price + max_price) // 2
                pending_ranges.append((min_price, midpoint))
                pending_ranges.append((midpoint + 1, max_price))
            time.sleep(0.06)

        if not rows:
            raise RuntimeError("KIS market-cap API returned no usable stocks")
        deduped = {}
        for row in rows:
            previous = deduped.get(row["ticker"])
            if previous is None or row["market_cap"] > previous["market_cap"]:
                deduped[row["ticker"]] = row
        universe = sorted(deduped.values(), key=lambda item: item["market_cap"], reverse=True)
        if min_market_cap is not None:
            universe = [row for row in universe if row["market_cap"] >= min_market_cap]
        if limit is not None:
            universe = universe[:limit]
        for idx, row in enumerate(universe, start=1):
            row["rank"] = idx
        return universe

    def save_universe(self, universe, universe_type):
        with sqlite3.connect(self.db_path) as conn:
            for row in universe:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO model_universe_snapshots (
                        snapshot_date, universe_type, rank, ticker, name, market_cap,
                        current_price, listed_shares, source, collected_at_kst
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.snapshot_date,
                        universe_type,
                        int(row["rank"]),
                        row["ticker"],
                        row["name"],
                        int(row["market_cap"]),
                        int(row["current_price"]),
                        int(row["listed_shares"]),
                        "KIS_MARKET_CAP_RANK",
                        self.collected_at_kst,
                    ),
                )

    def find_market_cap_stock(self, query, min_market_cap=500_000_000_000):
        query_text = str(query or "").strip()
        if not query_text:
            return None
        query_ticker = query_text.zfill(6) if query_text.isdigit() else None
        universe = self.fetch_market_cap_universe(min_market_cap=min_market_cap)
        for row in universe:
            if query_ticker and row["ticker"] == query_ticker:
                return row
            if row["name"] == query_text:
                return row
        for row in universe:
            if query_text.lower() in row["name"].lower():
                return row
        return None

    def load_saved_universe(self, universe_type):
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT rank, ticker, name, market_cap, current_price, listed_shares
                FROM model_universe_snapshots
                WHERE snapshot_date = ? AND universe_type = ?
                ORDER BY rank
                """,
                (self.snapshot_date, universe_type),
            ).fetchall()
        return [
            {
                "rank": row[0],
                "ticker": row[1],
                "name": row[2],
                "market_cap": row[3],
                "current_price": row[4],
                "listed_shares": row[5],
            }
            for row in rows
        ]

    def fetch_daily_ohlcv(self, ticker, end_date=None, lookback_days=370):
        end = datetime.strptime(end_date or self.snapshot_date, "%Y%m%d")
        start = end - timedelta(days=lookback_days)
        raw_rows = []
        cursor_end = end

        while cursor_end >= start:
            payload = self._get(
                "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                "FHKST03010100",
                {
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": ticker,
                    "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
                    "FID_INPUT_DATE_2": cursor_end.strftime("%Y%m%d"),
                    "FID_PERIOD_DIV_CODE": "D",
                    "FID_ORG_ADJ_PRC": "0",
                },
            )
            chunk = payload.get("output2") or []
            if not chunk:
                break
            raw_rows.extend(chunk)
            oldest_text = min(str(item.get("stck_bsop_date", "")) for item in chunk)
            if not oldest_text:
                break
            oldest = datetime.strptime(oldest_text, "%Y%m%d")
            next_end = oldest - timedelta(days=1)
            if next_end >= cursor_end:
                break
            cursor_end = next_end
            time.sleep(0.06)

        parsed_rows = []
        previous_close = None
        for item in sorted(raw_rows, key=lambda value: str(value.get("stck_bsop_date", ""))):
            close = self._to_float(item.get("stck_clpr"))
            volume = self._to_float(item.get("acml_vol"))
            if close is None or volume is None:
                continue
            trading_value = self._to_float(item.get("acml_tr_pbmn"))
            if trading_value is None:
                trading_value = close * volume
            change_rate = self._to_float(item.get("prdy_ctrt"))
            if change_rate is None and previous_close:
                change_rate = (close / previous_close - 1) * 100
            parsed_rows.append({
                "date": str(item.get("stck_bsop_date", "")),
                "open": self._to_float(item.get("stck_oprc")),
                "high": self._to_float(item.get("stck_hgpr")),
                "low": self._to_float(item.get("stck_lwpr")),
                "close": close,
                "volume": volume,
                "trading_value": trading_value,
                "change_rate": change_rate,
            })
            previous_close = close
        deduped = {row["date"]: row for row in parsed_rows if row["date"]}
        return [deduped[date] for date in sorted(deduped)]

    def save_ohlcv(self, ticker, name, market_cap, universe_type, frame):
        if not frame:
            return 0
        with sqlite3.connect(self.db_path) as conn:
            for row in frame:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO model_ohlcv_daily (
                        date, ticker, name, open, high, low, close, volume,
                        trading_value, change_rate, market_cap, universe_type,
                        universe_snapshot_date, source, collected_at_kst
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(row["date"]),
                        ticker,
                        name,
                        self._int_or_none(row["open"]),
                        self._int_or_none(row["high"]),
                        self._int_or_none(row["low"]),
                        self._int_or_none(row["close"]),
                        self._int_or_none(row["volume"]),
                        self._int_or_none(row["trading_value"]),
                        self._float_or_none(row["change_rate"]),
                        int(market_cap),
                        universe_type,
                        self.snapshot_date,
                        "KIS_DAILY_ITEM_CHARTPRICE",
                        self.collected_at_kst,
                    ),
                )
        return len(frame)

    @staticmethod
    def _to_float(value):
        try:
            if value is None or value == "":
                return None
            return float(str(value).replace(",", ""))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _int_or_none(value):
        return None if value is None else int(value)

    @staticmethod
    def _float_or_none(value):
        return None if value is None else float(value)

    def collect_market_cap_top_ohlcv(self, limit=100, lookback_days=370, reuse_universe=True):
        universe_type = f"market_cap_top_{limit}"
        universe = self.load_saved_universe(universe_type) if reuse_universe else []
        if len(universe) < limit:
            universe = self.fetch_market_cap_top(limit=limit)
            self.save_universe(universe, universe_type)
        return self._collect_ohlcv_for_universe(universe, universe_type, lookback_days)

    def collect_market_cap_threshold_ohlcv(
        self,
        min_market_cap=1_000_000_000_000,
        universe_type=None,
        lookback_days=370,
    ):
        universe_type = universe_type or f"market_cap_{int(min_market_cap / 100_000_000)}eok_plus"
        universe = self.fetch_market_cap_universe(min_market_cap=min_market_cap)
        self.save_universe(universe, universe_type)
        return self._collect_ohlcv_for_universe(universe, universe_type, lookback_days)

    def collect_single_stock_ohlcv(
        self,
        query,
        min_market_cap=500_000_000_000,
        universe_type="custom_5000eok_plus",
        lookback_days=370,
    ):
        stock = self.find_market_cap_stock(query, min_market_cap=min_market_cap)
        if stock is None:
            raise RuntimeError(f"No stock over market cap threshold matched: {query}")
        stock["rank"] = 1
        self.save_universe([stock], universe_type)
        summary = self._collect_ohlcv_for_universe([stock], universe_type, lookback_days)
        summary["stock"] = stock
        return summary

    def _collect_ohlcv_for_universe(self, universe, universe_type, lookback_days):
        saved_rows = 0
        failures = []
        for stock in universe:
            try:
                frame = self.fetch_daily_ohlcv(stock["ticker"], lookback_days=lookback_days)
                saved_rows += self.save_ohlcv(
                    stock["ticker"],
                    stock["name"],
                    stock["market_cap"],
                    universe_type,
                    frame,
                )
                print(
                    f"[ModelData] {stock['rank']:03d} {stock['ticker']} "
                    f"{stock['name']}: {len(frame)} rows"
                )
            except Exception as error:
                failures.append((stock["ticker"], stock["name"], str(error)))
                print(f"[ModelData Warning] {stock['ticker']} {stock['name']}: {error}")
            time.sleep(0.08)

        return {
            "universe_type": universe_type,
            "snapshot_date": self.snapshot_date,
            "universe_count": len(universe),
            "ohlcv_rows": saved_rows,
            "failures": failures,
        }


def main():
    parser = argparse.ArgumentParser(description="Collect model-only OHLCV data from KIS.")
    parser.add_argument("--db-path", default="stock_data.db")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--lookback-days", type=int, default=370)
    parser.add_argument("--refresh-universe", action="store_true")
    parser.add_argument("--mode", choices=["top", "threshold", "single"], default="top")
    parser.add_argument("--min-market-cap", type=int, default=1_000_000_000_000)
    parser.add_argument("--query")
    parser.add_argument("--universe-type")
    args = parser.parse_args()

    collector = ModelDataCollector(db_path=args.db_path)
    if args.mode == "threshold":
        summary = collector.collect_market_cap_threshold_ohlcv(
            min_market_cap=args.min_market_cap,
            universe_type=args.universe_type,
            lookback_days=args.lookback_days,
        )
    elif args.mode == "single":
        summary = collector.collect_single_stock_ohlcv(
            args.query,
            min_market_cap=args.min_market_cap,
            universe_type=args.universe_type or "custom_5000eok_plus",
            lookback_days=args.lookback_days,
        )
    else:
        summary = collector.collect_market_cap_top_ohlcv(
            limit=args.limit,
            lookback_days=args.lookback_days,
            reuse_universe=not args.refresh_universe,
        )
    print(
        "[ModelData] done: "
        f"universe={summary['universe_count']}, "
        f"ohlcv_rows={summary['ohlcv_rows']}, "
        f"failures={len(summary['failures'])}"
    )


if __name__ == "__main__":
    main()
