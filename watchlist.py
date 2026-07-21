import sqlite3
from datetime import datetime, time as datetime_time, timedelta, timezone

KST = timezone(timedelta(hours=9))
WATCHLIST_UNIVERSE = "watchlist"


def get_model_data_collector(db_path):
    from model_data_collector import ModelDataCollector

    return ModelDataCollector(db_path=db_path)


def init_watchlist_table(db_path="stock_data.db"):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS watchlist_items (
                ticker TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                market_cap INTEGER,
                added_date TEXT NOT NULL,
                added_at_kst TEXT NOT NULL,
                entry_date TEXT,
                entry_price INTEGER,
                updated_at_kst TEXT NOT NULL
            )
            """
        )


class WatchlistManager:
    def __init__(self, db_path="stock_data.db", now=None):
        self.db_path = str(db_path)
        self.now = now or datetime.now(KST)
        if self.now.tzinfo is None:
            self.now = self.now.replace(tzinfo=KST)
        init_watchlist_table(self.db_path)

    def add(self, ticker, name, market_cap=None):
        ticker = str(ticker or "").strip().zfill(6)
        name = str(name or "").strip()
        if len(ticker) != 6 or not ticker.isdigit() or not name:
            raise ValueError("올바른 종목코드와 종목명이 필요합니다.")
        timestamp = self.now.strftime("%Y-%m-%d %H:%M:%S")
        added_date = self.now.strftime("%Y%m%d")
        with sqlite3.connect(self.db_path) as conn:
            existing = conn.execute(
                "SELECT ticker FROM watchlist_items WHERE ticker = ?", (ticker,)
            ).fetchone()
            if existing:
                return {"ticker": ticker, "name": name, "already_exists": True}
            conn.execute(
                """
                INSERT INTO watchlist_items (
                    ticker, name, market_cap, added_date, added_at_kst,
                    entry_date, entry_price, updated_at_kst
                ) VALUES (?, ?, ?, ?, ?, NULL, NULL, ?)
                """,
                (ticker, name, market_cap, added_date, timestamp, timestamp),
            )
        self.refresh(tickers=[ticker])
        return {"ticker": ticker, "name": name, "already_exists": False}

    def remove(self, ticker):
        ticker = str(ticker or "").strip().zfill(6)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM watchlist_items WHERE ticker = ?", (ticker,))
        return cursor.rowcount > 0

    def refresh(self, tickers=None):
        params = []
        where = ""
        if tickers:
            normalized = [str(value).zfill(6) for value in tickers]
            where = f" WHERE ticker IN ({','.join('?' for _ in normalized)})"
            params.extend(normalized)
        with sqlite3.connect(self.db_path) as conn:
            items = conn.execute(
                "SELECT ticker, name, market_cap, added_date, entry_price "
                f"FROM watchlist_items{where} ORDER BY added_at_kst",
                params,
            ).fetchall()
        if not items:
            return {"updated": 0, "failures": []}

        collector = get_model_data_collector(self.db_path)
        failures = []
        updated = 0
        for ticker, name, market_cap, added_date, entry_price in items:
            try:
                frame = collector.fetch_daily_ohlcv(ticker, lookback_days=45)
                collector.save_ohlcv(
                    ticker,
                    name,
                    int(market_cap or 0),
                    WATCHLIST_UNIVERSE,
                    frame,
                )
                self._finalize_entry_price(ticker, added_date, entry_price, frame)
                updated += 1
            except Exception as error:
                failures.append((ticker, name, str(error)))
        return {"updated": updated, "failures": failures}

    def _finalize_entry_price(self, ticker, added_date, entry_price, frame):
        timestamp = self.now.strftime("%Y-%m-%d %H:%M:%S")
        if entry_price is not None:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "UPDATE watchlist_items SET updated_at_kst = ? WHERE ticker = ?",
                    (timestamp, ticker),
                )
            return

        completed = self.now.date().strftime("%Y%m%d") > added_date or self.now.time() >= datetime_time(15, 40)
        if not completed or not frame:
            return
        exact = [row for row in frame if str(row.get("date", "")) == added_date]
        if exact:
            entry = exact[-1]
        else:
            added_day = datetime.strptime(added_date, "%Y%m%d").date()
            is_non_trading_day_confirmed = (
                added_day.weekday() >= 5 or self.now.date() > added_day
            )
            if not is_non_trading_day_confirmed:
                return
            eligible = [row for row in frame if str(row.get("date", "")) < added_date]
            if not eligible:
                return
            entry = max(eligible, key=lambda row: str(row["date"]))
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE watchlist_items
                SET entry_date = ?, entry_price = ?, updated_at_kst = ?
                WHERE ticker = ? AND entry_price IS NULL
                """,
                (str(entry["date"]), int(entry["close"]), timestamp, ticker),
            )


def refresh_watchlist(db_path="stock_data.db"):
    return WatchlistManager(db_path=db_path).refresh()
