"""Collect per-stock KIS program net-buy snapshots for the scanner universe."""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime

import pandas as pd
import requests


class ProgramNetBuyScanner:
    API_PATH = "/uapi/domestic-stock/v1/quotations/program-trade-by-stock"
    TR_ID = "FHPPG04650101"

    def __init__(self, crawler):
        self.crawler = crawler
        self.db_path = crawler.db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS stock_program_net_snapshots (
                    trade_date TEXT NOT NULL,
                    session TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    name TEXT,
                    current_price INTEGER,
                    fluctuation_rate REAL,
                    market_cap INTEGER,
                    trading_value INTEGER,
                    program_net_buy INTEGER,
                    program_net_ratio REAL,
                    sector TEXT,
                    snapshot_time TEXT,
                    collected_at_kst TEXT,
                    PRIMARY KEY (trade_date, session, ticker)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS stock_program_net_runs (
                    trade_date TEXT NOT NULL,
                    session TEXT NOT NULL,
                    status TEXT NOT NULL,
                    universe_count INTEGER NOT NULL,
                    queried_count INTEGER NOT NULL,
                    positive_count INTEGER NOT NULL,
                    failure_count INTEGER NOT NULL,
                    error_message TEXT,
                    completed_at_kst TEXT,
                    PRIMARY KEY (trade_date, session)
                )
                """
            )

    @staticmethod
    def _number(value, default=0):
        if value is None:
            return default
        normalized = str(value).replace(",", "").strip()
        number = pd.to_numeric(normalized, errors="coerce")
        return default if pd.isna(number) else number

    @staticmethod
    def _latest_row(rows):
        valid_rows = [row for row in rows if isinstance(row, dict)]
        if not valid_rows:
            return None
        return max(valid_rows, key=lambda row: str(row.get("bsop_hour", "")).zfill(6))

    @staticmethod
    def _format_snapshot_time(value):
        digits = "".join(character for character in str(value or "") if character.isdigit())
        if len(digits) < 4:
            return str(value or "")
        digits = digits.zfill(6)[-6:]
        return f"{digits[:2]}:{digits[2:4]}:{digits[4:6]}"

    def _request_ticker(self, ticker, token):
        last_error = None
        for attempt in range(3):
            try:
                response = requests.get(
                    f"{self.crawler.kis_base_url}{self.API_PATH}",
                    headers={
                        "content-type": "application/json; charset=utf-8",
                        "authorization": f"Bearer {token}",
                        "appkey": self.crawler.kis_app_key,
                        "appsecret": self.crawler.kis_app_secret,
                        "tr_id": self.TR_ID,
                        "custtype": "P",
                    },
                    params={
                        "FID_COND_MRKT_DIV_CODE": "J",
                        "FID_INPUT_ISCD": ticker,
                    },
                    timeout=10,
                )
                payload = response.json() if response.content else {}
                if response.status_code == 200 and payload.get("rt_cd") == "0":
                    return self._latest_row(payload.get("output", []))
                last_error = RuntimeError(
                    payload.get("msg1") or response.text or f"HTTP {response.status_code}"
                )
            except Exception as error:
                last_error = error
            if attempt < 2:
                time.sleep(0.4 * (attempt + 1))
        raise RuntimeError(str(last_error or "unknown KIS API error"))

    def run(self, universe_df, session=None):
        session = session or self.crawler._get_session_name()
        trade_date = str(self.crawler.target_date)
        completed_at = datetime.now(self.crawler.kst).strftime("%Y-%m-%d %H:%M:%S")
        universe = (
            universe_df.copy()
            .drop_duplicates(subset=["ticker"])
            .sort_values("trading_value", ascending=False)
        )
        records = []
        failures = []
        queried_count = 0

        try:
            token = self.crawler._get_kis_access_token()
        except Exception as error:
            token = None
            failures.append(f"authentication: {error}")

        for row in universe.to_dict("records") if token else []:
            ticker = str(row.get("ticker", "")).zfill(6)
            if not ticker.strip("0"):
                continue
            try:
                program_row = self._request_ticker(ticker, token)
                queried_count += 1
                if not program_row:
                    continue
                program_net_buy = int(self._number(program_row.get("whol_smtn_ntby_tr_pbmn")))
                if program_net_buy <= 0:
                    continue
                trading_value = int(self._number(row.get("trading_value")))
                records.append(
                    {
                        "trade_date": trade_date,
                        "session": session,
                        "ticker": ticker,
                        "name": str(row.get("name", "")),
                        "current_price": int(
                            self._number(program_row.get("stck_prpr"), self._number(row.get("close")))
                        ),
                        "fluctuation_rate": float(
                            self._number(
                                program_row.get("prdy_ctrt"),
                                self._number(row.get("fluctuation_rate")),
                            )
                        ),
                        "market_cap": int(self._number(row.get("market_cap"))),
                        "trading_value": trading_value,
                        "program_net_buy": program_net_buy,
                        "program_net_ratio": (
                            round(program_net_buy / trading_value * 100, 2)
                            if trading_value > 0
                            else None
                        ),
                        "sector": str(row.get("sector", "")),
                        "snapshot_time": self._format_snapshot_time(program_row.get("bsop_hour")),
                        "collected_at_kst": completed_at,
                    }
                )
            except Exception as error:
                failures.append(f"{ticker}: {error}")
            time.sleep(0.06)

        if queried_count == 0:
            status = "failure"
        elif failures:
            status = "partial"
        else:
            status = "success"

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM stock_program_net_snapshots WHERE trade_date=? AND session=?",
                (trade_date, session),
            )
            conn.executemany(
                """
                INSERT INTO stock_program_net_snapshots (
                    trade_date, session, ticker, name, current_price,
                    fluctuation_rate, market_cap, trading_value,
                    program_net_buy, program_net_ratio, sector,
                    snapshot_time, collected_at_kst
                ) VALUES (
                    :trade_date, :session, :ticker, :name, :current_price,
                    :fluctuation_rate, :market_cap, :trading_value,
                    :program_net_buy, :program_net_ratio, :sector,
                    :snapshot_time, :collected_at_kst
                )
                """,
                sorted(records, key=lambda item: item["program_net_buy"], reverse=True),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO stock_program_net_runs (
                    trade_date, session, status, universe_count, queried_count,
                    positive_count, failure_count, error_message, completed_at_kst
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade_date,
                    session,
                    status,
                    len(universe),
                    queried_count,
                    len(records),
                    len(failures),
                    " | ".join(failures[:5]) or None,
                    completed_at,
                ),
            )

        print(
            "[Program Net Buy] "
            f"session={session}, universe={len(universe)}, queried={queried_count}, "
            f"positive={len(records)}, failures={len(failures)}, status={status}"
        )
        return pd.DataFrame(records)
