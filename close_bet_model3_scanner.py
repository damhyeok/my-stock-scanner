import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import pandas as pd

from close_bet_scanner import CloseBetScanner


class CloseBetModel3Scanner(CloseBetScanner):
    """MACD/RSI 동시 매수신호 기반 종가베팅 스캐너."""

    MARKET_CAP_MIN = 1_000_000_000_000

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS close_bet_model3_scans (
                trade_date TEXT, session TEXT, ticker TEXT, name TEXT, grade TEXT,
                market_cap INTEGER, current_price INTEGER, fluctuation_rate REAL,
                previous_return REAL, volume_ratio REAL, rsi REAL,
                ma20_change_5d REAL, scanned_at_kst TEXT,
                PRIMARY KEY (trade_date, session, ticker))""")
            conn.execute("""CREATE TABLE IF NOT EXISTS close_bet_model3_runs (
                trade_date TEXT, session TEXT, scanned_count INTEGER,
                selected_count INTEGER, failed_count INTEGER, completed_at_kst TEXT,
                PRIMARY KEY (trade_date, session))""")

    @staticmethod
    def _signal(frame):
        close = frame["stck_clpr"].astype(float)
        volume = frame["acml_vol"].astype(float)
        delta = close.diff()
        gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
        rsi = 100 - 100 / (1 + gain / loss.replace(0, 1e-12))
        rsi_signal = rsi.rolling(9, min_periods=9).mean()
        macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
        macd_signal = macd.ewm(span=9, adjust=False).mean()
        ma20 = close.rolling(20).mean()
        return {
            "rsi_buy": bool(rsi.iloc[-2] <= rsi_signal.iloc[-2] and rsi.iloc[-1] > rsi_signal.iloc[-1]),
            "macd_buy": bool(macd.iloc[-2] <= macd_signal.iloc[-2] and macd.iloc[-1] > macd_signal.iloc[-1]),
            "rsi": float(rsi.iloc[-1]),
            "today_return": float((close.iloc[-1] / close.iloc[-2] - 1) * 100),
            "previous_return": float((close.iloc[-2] / close.iloc[-3] - 1) * 100),
            "volume_ratio": float(volume.iloc[-1] / volume.iloc[-21:-1].mean()),
            "ma20_change_5d": float((ma20.iloc[-1] / ma20.iloc[-6] - 1) * 100),
        }

    def _analyze_model3(self, ticker, name, market_cap, trade_date):
        frame = self._fetch_daily_ohlcv(ticker, trade_date)
        if frame is None or len(frame) < 60:
            return None
        if trade_date == datetime.now(self.kst).strftime("%Y%m%d"):
            for column, value in self._fetch_current_quote(ticker).items():
                if pd.notna(value) and value > 0:
                    frame.loc[frame.index[-1], column] = value
        signal = self._signal(frame)
        if not (signal["rsi_buy"] and signal["macd_buy"]
                and signal["today_return"] <= 10
                and 0.7 <= signal["volume_ratio"] <= 1.5
                and signal["ma20_change_5d"] <= 1
                and signal["previous_return"] >= -3):
            return None
        return {
            "ticker": ticker, "name": name,
            "grade": "A" if signal["previous_return"] >= 0 else "B",
            "market_cap": int(market_cap),
            "current_price": int(frame["stck_clpr"].iloc[-1]),
            "fluctuation_rate": round(signal["today_return"], 2),
            "previous_return": round(signal["previous_return"], 2),
            "volume_ratio": round(signal["volume_ratio"], 2),
            "rsi": round(signal["rsi"], 1),
            "ma20_change_5d": round(signal["ma20_change_5d"], 2),
        }

    def run(self, trade_date=None, session=None):
        trade_date = trade_date or self.crawler.target_date
        session = session or self.crawler._get_session_name()
        universe = self._load_universe(trade_date, session)
        results, failures = [], 0
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(self._analyze_model3, r.ticker, r.name, r.market_cap, trade_date): r.ticker
                       for r in universe.itertuples(index=False)}
            for future in as_completed(futures):
                try:
                    result = future.result()
                    if result:
                        results.append(result)
                except Exception as error:
                    failures += 1
                    print(f"[Close Bet Model 3 Warning] {futures[future]}: {error}")
        completed_at = datetime.now(self.kst).strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM close_bet_model3_scans WHERE trade_date=? AND session=?", (trade_date, session))
            for row in results:
                conn.execute("INSERT INTO close_bet_model3_scans VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                             (trade_date, session, row["ticker"], row["name"], row["grade"], row["market_cap"],
                              row["current_price"], row["fluctuation_rate"], row["previous_return"],
                              row["volume_ratio"], row["rsi"], row["ma20_change_5d"], completed_at))
            conn.execute("INSERT OR REPLACE INTO close_bet_model3_runs VALUES (?, ?, ?, ?, ?, ?)",
                         (trade_date, session, len(universe), len(results), failures, completed_at))
        print(f"[Close Bet Model 3] selected={len(results)}, failed={failures}")
        return pd.DataFrame(results)
