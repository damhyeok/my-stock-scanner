import argparse
import asyncio
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

import requests
import websockets
from dotenv import load_dotenv


load_dotenv()


class ProgramTradeCollector:
    KST = timezone(timedelta(hours=9))
    WS_URL = "ws://ops.koreainvestment.com:21000/tryitout"
    TR_ID = "H0UPPGM0"
    INDEX_CODE = "0001"
    FIELD_INDEX = {
        "snapshot_time": 1,
        "arbitrage_net": 28,
        "non_arbitrage_net": 40,
        "program_net": 76,
    }
    SESSION_CONFIG = {
        "morning": {
            "targets": ["09:15", "09:30", "09:45"],
            "stop_time": "09:46",
        },
        "closing": {
            "targets": ["14:30", "15:00", "15:20", "15:30"],
            "stop_time": "15:31",
        },
    }

    def __init__(self, analysis_type, db_path="program_snapshots.db"):
        if analysis_type not in self.SESSION_CONFIG:
            raise ValueError(f"지원하지 않는 수집 구분입니다: {analysis_type}")
        self.analysis_type = analysis_type
        self.db_path = db_path
        self.app_key = os.environ.get("KIS_APP_KEY", "").strip()
        self.app_secret = os.environ.get("KIS_APP_SECRET", "").strip()
        self.trade_date = datetime.now(self.KST).strftime("%Y%m%d")
        self.targets = self.SESSION_CONFIG[analysis_type]["targets"]
        self.stop_at = self._today_at(self.SESSION_CONFIG[analysis_type]["stop_time"])
        self.saved = set()
        self.last_tick = None
        self._init_db()

    def _today_at(self, hhmm):
        hour, minute = map(int, hhmm.split(":"))
        return datetime.now(self.KST).replace(hour=hour, minute=minute, second=0, microsecond=0)

    def _init_db(self):
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS market_program_snapshots (
                    trade_date TEXT,
                    analysis_type TEXT,
                    snapshot_time TEXT,
                    program_net REAL,
                    arbitrage_net REAL,
                    non_arbitrage_net REAL,
                    source TEXT,
                    collected_at_kst TEXT,
                    PRIMARY KEY (trade_date, analysis_type, snapshot_time)
                )
                """
            )

    def _get_approval_key(self):
        if not self.app_key or not self.app_secret:
            raise ValueError("KIS_APP_KEY와 KIS_APP_SECRET이 설정되지 않았습니다.")
        response = requests.post(
            "https://openapi.koreainvestment.com:9443/oauth2/Approval",
            headers={"content-type": "application/json"},
            json={
                "grant_type": "client_credentials",
                "appkey": self.app_key,
                "secretkey": self.app_secret,
            },
            timeout=15,
        )
        response.raise_for_status()
        approval_key = response.json().get("approval_key")
        if not approval_key:
            raise RuntimeError(f"KIS WebSocket 접속키 발급 실패: {response.text}")
        return approval_key

    def _subscription_message(self, approval_key):
        return json.dumps(
            {
                "header": {
                    "approval_key": approval_key,
                    "custtype": "P",
                    "tr_type": "1",
                    "content-type": "utf-8",
                },
                "body": {
                    "input": {
                        "tr_id": self.TR_ID,
                        "tr_key": self.INDEX_CODE,
                    }
                },
            }
        )

    @staticmethod
    def _to_float(value):
        try:
            return float(str(value).replace(",", ""))
        except (TypeError, ValueError):
            return 0.0

    def _parse_record(self, values):
        if len(values) <= max(self.FIELD_INDEX.values()):
            return None
        raw_time = values[self.FIELD_INDEX["snapshot_time"]].zfill(6)
        if not raw_time.isdigit():
            return None
        tick_time = datetime.now(self.KST).replace(
            hour=int(raw_time[0:2]),
            minute=int(raw_time[2:4]),
            second=int(raw_time[4:6]),
            microsecond=0,
        )
        return {
            "tick_time": tick_time,
            "program_net": self._to_float(values[self.FIELD_INDEX["program_net"]]),
            "arbitrage_net": self._to_float(values[self.FIELD_INDEX["arbitrage_net"]]),
            "non_arbitrage_net": self._to_float(values[self.FIELD_INDEX["non_arbitrage_net"]]),
        }

    def _parse_message(self, message):
        parts = message.split("|", 3)
        if len(parts) != 4 or parts[1] != self.TR_ID:
            return []
        if parts[0] != "0":
            raise RuntimeError("암호화된 KIS 프로그램 매매 메시지는 지원하지 않습니다.")
        try:
            record_count = int(parts[2])
        except ValueError:
            record_count = 1
        values = parts[3].split("^")
        field_count = 88
        records = []
        for index in range(record_count):
            record = self._parse_record(values[index * field_count:(index + 1) * field_count])
            if record:
                records.append(record)
        return records

    def _save_snapshot(self, snapshot_time, tick):
        collected_at = datetime.now(self.KST).strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO market_program_snapshots (
                    trade_date, analysis_type, snapshot_time, program_net,
                    arbitrage_net, non_arbitrage_net, source, collected_at_kst
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.trade_date,
                    self.analysis_type,
                    snapshot_time,
                    tick["program_net"],
                    tick["arbitrage_net"],
                    tick["non_arbitrage_net"],
                    "KIS_WEBSOCKET",
                    collected_at,
                ),
            )
        self.saved.add(snapshot_time)
        print(
            f"[Program WS] {snapshot_time} 저장: program={tick['program_net']}, "
            f"arbitrage={tick['arbitrage_net']}, non_arbitrage={tick['non_arbitrage_net']}"
        )

    def _capture_crossed_targets(self, tick):
        for snapshot_time in self.targets:
            if snapshot_time in self.saved:
                continue
            target = self._today_at(snapshot_time)
            candidate = None
            if tick["tick_time"] == target:
                candidate = tick
            elif self.last_tick and self.last_tick["tick_time"] <= target < tick["tick_time"]:
                if target - self.last_tick["tick_time"] <= timedelta(minutes=2):
                    candidate = self.last_tick
            if candidate:
                self._save_snapshot(snapshot_time, candidate)
        self.last_tick = tick

    def _save_final_target_if_close(self):
        if not self.last_tick:
            return
        for snapshot_time in self.targets:
            if snapshot_time in self.saved:
                continue
            target = self._today_at(snapshot_time)
            distance = abs((target - self.last_tick["tick_time"]).total_seconds())
            if distance <= 120:
                self._save_snapshot(snapshot_time, self.last_tick)

    async def _collect_once(self, approval_key):
        async with websockets.connect(
            self.WS_URL,
            ping_interval=20,
            ping_timeout=20,
            open_timeout=15,
        ) as websocket:
            await websocket.send(self._subscription_message(approval_key))
            print(f"[Program WS] {self.analysis_type} 수집 연결 완료")
            while datetime.now(self.KST) <= self.stop_at:
                try:
                    message = await asyncio.wait_for(websocket.recv(), timeout=10)
                except asyncio.TimeoutError:
                    continue
                if message.startswith("{"):
                    payload = json.loads(message)
                    if payload.get("header", {}).get("tr_id") == "PINGPONG":
                        await websocket.send(message)
                    continue
                for tick in self._parse_message(message):
                    self._capture_crossed_targets(tick)

    async def collect(self):
        if datetime.now(self.KST).weekday() >= 5:
            print("[Program WS] 주말에는 수집하지 않습니다.")
            return False
        if datetime.now(self.KST) > self.stop_at:
            raise RuntimeError(f"{self.analysis_type} WebSocket 수집 종료시각이 지났습니다.")

        approval_key = self._get_approval_key()
        while datetime.now(self.KST) <= self.stop_at:
            try:
                await self._collect_once(approval_key)
                break
            except Exception as error:
                print(f"[Program WS Warning] 연결 오류, 5초 후 재연결: {error}")
                await asyncio.sleep(5)
        self._save_final_target_if_close()
        missing = [target for target in self.targets if target not in self.saved]
        if missing:
            raise RuntimeError(f"프로그램 수급 미수집 시점: {', '.join(missing)}")
        print(f"[Program WS] {self.analysis_type} 수집 완료")
        return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("analysis_type", choices=["morning", "closing"])
    parser.add_argument("--db-path", default="program_snapshots.db")
    args = parser.parse_args()
    collector = ProgramTradeCollector(args.analysis_type, db_path=args.db_path)
    asyncio.run(collector.collect())


if __name__ == "__main__":
    main()
