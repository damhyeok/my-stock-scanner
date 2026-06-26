import os
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from dotenv import load_dotenv


load_dotenv()


class MarketStrengthAnalyzer:
    SNAPSHOT_TIMES = ["14:30", "15:00", "15:20", "15:30"]

    def __init__(self, db_path="stock_data.db"):
        self.db_path = db_path
        self.kst = ZoneInfo("Asia/Seoul")
        self.kis_app_key = os.environ.get("KIS_APP_KEY", "")
        self.kis_app_secret = os.environ.get("KIS_APP_SECRET", "")
        self.kis_base_url = "https://openapi.koreainvestment.com:9443"
        self.access_token = None
        self.collected_at_kst = datetime.now(self.kst).strftime("%Y-%m-%d %H:%M:%S")
        self.target_date = self._resolve_target_date()
        self._init_db()

    def _resolve_target_date(self):
        now = datetime.now(self.kst)
        b_days = pd.bdate_range(end=now, periods=1)
        return b_days[0].strftime("%Y%m%d")

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS market_strength_snapshots (
                trade_date TEXT,
                snapshot_time TEXT,
                foreign_futures_net REAL,
                basis REAL,
                program_net REAL,
                arbitrage_net REAL,
                non_arbitrage_net REAL,
                kospi200_futures_price REAL,
                futures_day_high REAL,
                futures_day_low REAL,
                futures_vwap REAL,
                market_strength_score INTEGER,
                foreign_futures_score REAL,
                basis_score INTEGER,
                program_score INTEGER,
                futures_trend_score INTEGER,
                interpretation_text TEXT,
                collected_at_kst TEXT,
                PRIMARY KEY (trade_date, snapshot_time)
            )
            """
        )
        conn.commit()
        conn.close()

    def _get_kis_access_token(self):
        if self.access_token:
            return self.access_token
        if not self.kis_app_key or not self.kis_app_secret:
            raise ValueError("KIS API 키가 .env 파일에 설정되지 않았습니다.")

        res = requests.post(
            f"{self.kis_base_url}/oauth2/tokenP",
            headers={"content-type": "application/json"},
            json={
                "grant_type": "client_credentials",
                "appkey": self.kis_app_key,
                "appsecret": self.kis_app_secret,
            },
            timeout=15,
        )
        if res.status_code != 200:
            raise Exception(f"KIS 토큰 발급 실패: {res.text}")
        self.access_token = res.json().get("access_token")
        return self.access_token

    def _kis_get(self, path, tr_id, params):
        token = self._get_kis_access_token()
        res = requests.get(
            f"{self.kis_base_url}{path}",
            headers={
                "content-type": "application/json; charset=utf-8",
                "authorization": f"Bearer {token}",
                "appkey": self.kis_app_key,
                "appsecret": self.kis_app_secret,
                "tr_id": tr_id,
                "custtype": "P",
            },
            params=params,
            timeout=15,
        )
        if res.status_code != 200:
            raise Exception(f"KIS API 호출 실패({tr_id}): {res.text}")
        data = res.json()
        if data.get("rt_cd") not in (None, "0"):
            raise Exception(f"KIS API 응답 오류({tr_id}): {res.text}")
        return data

    @staticmethod
    def _to_float(value, default=0.0):
        try:
            if value in (None, ""):
                return default
            return float(str(value).replace(",", ""))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _time_to_hhmmss(snapshot_time):
        return snapshot_time.replace(":", "") + "00"

    @staticmethod
    def _nearest_row(rows, time_key, time_field):
        target = int(time_key)
        candidates = []
        for row in rows:
            raw_time = str(row.get(time_field, "")).zfill(6)
            if not raw_time.isdigit():
                continue
            row_time = int(raw_time)
            if row_time <= target:
                candidates.append((row_time, row))
        if candidates:
            return max(candidates, key=lambda item: item[0])[1]
        valid_rows = [
            (int(str(row.get(time_field, "")).zfill(6)), row)
            for row in rows
            if str(row.get(time_field, "")).zfill(6).isdigit()
        ]
        if not valid_rows:
            return {}
        return min(valid_rows, key=lambda item: abs(item[0] - target))[1]

    def _fetch_program_snapshots(self):
        data = self._kis_get(
            "/uapi/domestic-stock/v1/quotations/comp-program-trade-today",
            "FHPPG04600101",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_MRKT_CLS_CODE": "K",
                "FID_SCTN_CLS_CODE": "",
                "FID_INPUT_ISCD": "",
                "FID_COND_MRKT_DIV_CODE1": "",
                "FID_INPUT_HOUR_1": "",
            },
        )
        rows = data.get("output", []) or []
        snapshots = {}
        for snapshot_time in self.SNAPSHOT_TIMES:
            row = self._nearest_row(rows, self._time_to_hhmmss(snapshot_time), "bsop_hour")
            snapshots[snapshot_time] = {
                "program_net": self._to_float(row.get("whol_smtn_ntby_tr_pbmn")),
                "arbitrage_net": self._to_float(row.get("arbt_smtn_ntby_tr_pbmn")),
                "non_arbitrage_net": self._to_float(row.get("nabt_smtn_ntby_tr_pbmn")),
            }
        return snapshots

    def _fetch_active_futures_code(self):
        data = self._kis_get(
            "/uapi/domestic-futureoption/v1/quotations/display-board-futures",
            "FHPIF05030200",
            {
                "FID_COND_MRKT_DIV_CODE": "F",
                "FID_COND_SCR_DIV_CODE": "20503",
                "FID_COND_MRKT_CLS_CODE": "MKI",
            },
        )
        rows = data.get("output", []) or []
        if not rows:
            raise Exception("KOSPI200 선물 전광판 데이터가 없습니다.")
        return max(rows, key=lambda row: self._to_float(row.get("acml_vol"))).get("futs_shrn_iscd")

    def _fetch_futures_snapshots(self):
        futures_code = self._fetch_active_futures_code()
        data = self._kis_get(
            "/uapi/domestic-futureoption/v1/quotations/inquire-time-fuopchartprice",
            "FHKIF03020200",
            {
                "FID_COND_MRKT_DIV_CODE": "F",
                "FID_INPUT_ISCD": futures_code,
                "FID_HOUR_CLS_CODE": "60",
                "FID_PW_DATA_INCU_YN": "Y",
                "FID_FAKE_TICK_INCU_YN": "N",
                "FID_INPUT_DATE_1": self.target_date,
                "FID_INPUT_HOUR_1": "154000",
            },
        )
        current_info = data.get("output1", {}) or {}
        rows = data.get("output2", []) or []
        index_snapshots = self._fetch_index_snapshots()
        day_high = self._to_float(current_info.get("futs_hgpr"))
        day_low = self._to_float(current_info.get("futs_lwpr"))
        basis_now = self._to_float(current_info.get("basis"))

        snapshots = {}
        for snapshot_time in self.SNAPSHOT_TIMES:
            row = self._nearest_row(rows, self._time_to_hhmmss(snapshot_time), "stck_cntg_hour")
            price = self._to_float(row.get("futs_prpr"))
            amount = self._to_float(row.get("acml_tr_pbmn"))
            volume = self._to_float(row.get("cntg_vol"))
            index_price = index_snapshots.get(snapshot_time)
            calculated_basis = price - index_price if index_price else None
            snapshots[snapshot_time] = {
                "basis": calculated_basis if calculated_basis is not None and abs(calculated_basis) < 100 else basis_now,
                "kospi200_futures_price": price,
                "futures_day_high": day_high,
                "futures_day_low": day_low,
                "futures_vwap": amount / volume if volume else price,
            }
        return snapshots

    def _extract_index_price(self, row):
        for key in [
            "bstp_nmix_prpr",
            "bstp_nmix",
            "stck_prpr",
            "prpr",
            "stck_clpr",
            "clpr",
        ]:
            value = self._to_float(row.get(key))
            if value:
                return value
        for key, value in row.items():
            key_text = str(key).lower()
            if any(token in key_text for token in ["prpr", "nmix", "clpr"]):
                parsed = self._to_float(value)
                if parsed:
                    return parsed
        return 0.0

    def _fetch_index_snapshots(self):
        # KIS 업종 분봉조회는 환경별 코드 표기가 다를 수 있어 KOSPI200 후보를 순차 시도합니다.
        for index_code in ["2001", "0002", "0001"]:
            try:
                data = self._kis_get(
                    "/uapi/domestic-stock/v1/quotations/inquire-time-indexchartprice",
                    "FHKUP03500200",
                    {
                        "FID_COND_MRKT_DIV_CODE": "U",
                        "FID_ETC_CLS_CODE": "0",
                        "FID_INPUT_ISCD": index_code,
                        "FID_INPUT_HOUR_1": "153000",
                        "FID_PW_DATA_INCU_YN": "Y",
                    },
                )
                rows = data.get("output2", []) or []
                snapshots = {}
                for snapshot_time in self.SNAPSHOT_TIMES:
                    row = self._nearest_row(rows, self._time_to_hhmmss(snapshot_time), "stck_cntg_hour")
                    price = self._extract_index_price(row)
                    if price:
                        snapshots[snapshot_time] = price
                if len(snapshots) == len(self.SNAPSHOT_TIMES):
                    return snapshots
            except Exception as e:
                print(f"[Market Strength Warning] KOSPI200 지수 분봉 조회 실패(code={index_code}): {e}")
        return {}

    def _score_basis(self, snapshots):
        values = [snapshots[t]["basis"] for t in self.SNAPSHOT_TIMES]
        delta = values[-1] - values[0]
        late_delta = values[-1] - values[-2]
        score = 18
        if values[-1] > 0:
            score += 7
        if delta > 0:
            score += 6
        elif delta < 0:
            score -= 6
        if late_delta > 0:
            score += 4
        elif late_delta < 0:
            score -= 4
        if values[0] >= 0 and values[-1] < 0:
            score -= 10
        return max(0, min(35, int(round(score))))

    def _score_program(self, snapshots):
        program = [snapshots[t]["program_net"] for t in self.SNAPSHOT_TIMES]
        non_arbitrage = [snapshots[t]["non_arbitrage_net"] for t in self.SNAPSHOT_TIMES]
        score = 16
        if program[-1] > 0:
            score += 7
        if program[-1] > program[0]:
            score += 6
        elif program[-1] < program[0]:
            score -= 5
        if non_arbitrage[-1] > non_arbitrage[0]:
            score += 4
        if program[-1] < 0:
            score -= 8
        if program[-1] > program[-2]:
            score += 2
        return max(0, min(35, int(round(score))))

    def _score_futures_trend(self, snapshots):
        prices = [snapshots[t]["kospi200_futures_price"] for t in self.SNAPSHOT_TIMES]
        last = prices[-1]
        day_high = snapshots[self.SNAPSHOT_TIMES[-1]]["futures_day_high"]
        day_low = snapshots[self.SNAPSHOT_TIMES[-1]]["futures_day_low"]
        vwap = snapshots[self.SNAPSHOT_TIMES[-1]]["futures_vwap"]
        score = 12
        day_range = day_high - day_low
        if day_range > 0 and last >= day_low + day_range * 0.75:
            score += 7
        if last > vwap:
            score += 6
        if last > prices[-2]:
            score += 5
        elif last < prices[-2]:
            score -= 5
        if last < prices[0]:
            score -= 4
        return max(0, min(30, int(round(score))))

    def _build_interpretation(self, score, basis_score, program_score, futures_score, snapshots):
        basis_delta = snapshots[self.SNAPSHOT_TIMES[-1]]["basis"] - snapshots[self.SNAPSHOT_TIMES[0]]["basis"]
        program_delta = snapshots[self.SNAPSHOT_TIMES[-1]]["program_net"] - snapshots[self.SNAPSHOT_TIMES[0]]["program_net"]
        futures_delta = (
            snapshots[self.SNAPSHOT_TIMES[-1]]["kospi200_futures_price"]
            - snapshots[self.SNAPSHOT_TIMES[-2]]["kospi200_futures_price"]
        )

        parts = []
        parts.append("베이시스가 장 막판 확대되었습니다" if basis_delta > 0 else "베이시스가 장 막판 축소되었습니다")
        parts.append("프로그램 순매수가 증가했습니다" if program_delta > 0 else "프로그램 매수 강도는 약화되었습니다")
        parts.append("15:20 이후 선물이 상승했습니다" if futures_delta > 0 else "15:20 이후 선물이 밀렸습니다")

        if score >= 85:
            tail = "종가베팅 시장 환경은 매우 우호적입니다."
        elif score >= 70:
            tail = "종가베팅 시장 환경은 우호적입니다."
        elif score >= 55:
            tail = "종가베팅은 보통 수준의 환경에서 선별 접근이 필요합니다."
        else:
            tail = "장 막판 수급이 약해 종가베팅은 신중하게 접근하는 것이 좋습니다."

        return f"{', '.join(parts)}. {tail}"

    def _combine_snapshots(self, program_snapshots, futures_snapshots):
        snapshots = {}
        for snapshot_time in self.SNAPSHOT_TIMES:
            snapshots[snapshot_time] = {
                "foreign_futures_net": None,
                **program_snapshots.get(snapshot_time, {}),
                **futures_snapshots.get(snapshot_time, {}),
            }
        return snapshots

    def _save_snapshots(self, snapshots, scores, interpretation_text):
        conn = sqlite3.connect(self.db_path)
        conn.execute("DELETE FROM market_strength_snapshots WHERE trade_date = ?", (self.target_date,))
        for snapshot_time in self.SNAPSHOT_TIMES:
            row = snapshots[snapshot_time]
            conn.execute(
                """
                INSERT OR REPLACE INTO market_strength_snapshots (
                    trade_date, snapshot_time, foreign_futures_net, basis, program_net,
                    arbitrage_net, non_arbitrage_net, kospi200_futures_price,
                    futures_day_high, futures_day_low, futures_vwap,
                    market_strength_score, foreign_futures_score, basis_score,
                    program_score, futures_trend_score, interpretation_text, collected_at_kst
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.target_date,
                    snapshot_time,
                    row.get("foreign_futures_net"),
                    row.get("basis"),
                    row.get("program_net"),
                    row.get("arbitrage_net"),
                    row.get("non_arbitrage_net"),
                    row.get("kospi200_futures_price"),
                    row.get("futures_day_high"),
                    row.get("futures_day_low"),
                    row.get("futures_vwap"),
                    scores["market_strength_score"],
                    None,
                    scores["basis_score"],
                    scores["program_score"],
                    scores["futures_trend_score"],
                    interpretation_text,
                    self.collected_at_kst,
                ),
            )
        conn.commit()
        conn.close()

    def run(self):
        print(f"[Market Strength] {self.target_date} 시장강도 분석 시작")
        now = datetime.now(self.kst)
        if now.hour < 15 or (now.hour == 15 and now.minute < 35):
            print("[Market Strength] 15:35 이전에는 시장강도 분석을 건너뜁니다.")
            return None

        program_snapshots = self._fetch_program_snapshots()
        futures_snapshots = self._fetch_futures_snapshots()
        snapshots = self._combine_snapshots(program_snapshots, futures_snapshots)

        basis_score = self._score_basis(snapshots)
        program_score = self._score_program(snapshots)
        futures_score = self._score_futures_trend(snapshots)
        total_score = basis_score + program_score + futures_score
        scores = {
            "market_strength_score": total_score,
            "basis_score": basis_score,
            "program_score": program_score,
            "futures_trend_score": futures_score,
        }
        interpretation_text = self._build_interpretation(
            total_score, basis_score, program_score, futures_score, snapshots
        )
        self._save_snapshots(snapshots, scores, interpretation_text)
        print(f"[Market Strength] 시장강도 {total_score}점 저장 완료")
        return snapshots, scores, interpretation_text
