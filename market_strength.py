import os
import sqlite3
import math
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
from dotenv import load_dotenv


load_dotenv()


class MarketStrengthAnalyzer:
    MORNING_SNAPSHOT_TIMES = ["09:15", "09:30", "09:45"]
    AFTERNOON_SNAPSHOT_TIMES = ["13:30", "13:45", "14:00"]
    CLOSING_SNAPSHOT_TIMES = ["14:30", "15:00", "15:20", "15:30"]
    BASIS_MAX_ABS = 20.0
    BASIS_MAX_TIME_GAP_SECONDS = 60

    def __init__(self, db_path="stock_data.db", analysis_type=None, snapshot_times=None, requested_at_kst=None):
        self.db_path = db_path
        self.kst = timezone(timedelta(hours=9))
        self.kis_app_key = os.environ.get("KIS_APP_KEY", "")
        self.kis_app_secret = os.environ.get("KIS_APP_SECRET", "")
        self.kis_base_url = "https://openapi.koreainvestment.com:9443"
        self.access_token = None
        self.collected_at_kst = datetime.now(self.kst).strftime("%Y-%m-%d %H:%M:%S")
        self.target_date = self._resolve_target_date()
        self.requested_at_kst = self._parse_requested_at(requested_at_kst)
        self.analysis_type = analysis_type or self._resolve_analysis_type()
        self.snapshot_times = snapshot_times or self._resolve_snapshot_times()
        self._init_db()

    @classmethod
    def from_environment(cls, db_path="stock_data.db"):
        mode = os.environ.get("MARKET_STRENGTH_MODE", "").strip() or None
        requested_at = os.environ.get("MARKET_STRENGTH_REQUESTED_AT_KST", "").strip() or None
        return cls(db_path=db_path, analysis_type=mode, requested_at_kst=requested_at)

    def _resolve_target_date(self):
        now = datetime.now(self.kst)
        b_days = pd.bdate_range(end=now, periods=1)
        return b_days[0].strftime("%Y%m%d")

    def _parse_requested_at(self, value):
        if not value:
            return datetime.now(self.kst)
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=self.kst)
            return parsed.astimezone(self.kst)
        except ValueError:
            return datetime.now(self.kst)

    def _resolve_analysis_type(self):
        now = self.requested_at_kst
        if now.hour < 12:
            return "morning"
        if now.hour >= 15:
            return "closing"
        return "morning"

    def _resolve_snapshot_times(self):
        if self.analysis_type == "manual":
            base = self.requested_at_kst.replace(second=0, microsecond=0)
            times = sorted({(base - timedelta(minutes=offset)).strftime("%H:%M") for offset in [30, 15, 0]})
            return times
        if self.analysis_type == "morning":
            return self.MORNING_SNAPSHOT_TIMES
        if self.analysis_type == "afternoon":
            return self.AFTERNOON_SNAPSHOT_TIMES
        return self.CLOSING_SNAPSHOT_TIMES

    def _analysis_label(self):
        labels = {
            "morning": "오전 흐름",
            "afternoon": "오후 흐름",
            "closing": "종가 흐름",
            "manual": "수동 흐름",
        }
        if self.analysis_type == "manual":
            return f"{labels['manual']} ({self.requested_at_kst.strftime('%H:%M')} 기준)"
        return labels.get(self.analysis_type, self.analysis_type)

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        existing_columns = [
            row[1]
            for row in conn.execute("PRAGMA table_info(market_strength_snapshots)").fetchall()
        ]
        if existing_columns and "analysis_type" not in existing_columns:
            conn.execute("ALTER TABLE market_strength_snapshots RENAME TO market_strength_snapshots_legacy")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS market_strength_snapshots (
                trade_date TEXT,
                analysis_type TEXT DEFAULT 'closing',
                analysis_label TEXT,
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
                PRIMARY KEY (trade_date, analysis_type, snapshot_time)
            )
            """
        )
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
        legacy_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'market_strength_snapshots_legacy'"
        ).fetchone()
        if legacy_exists:
            conn.execute(
                """
                INSERT OR IGNORE INTO market_strength_snapshots (
                    trade_date, analysis_type, analysis_label, snapshot_time, foreign_futures_net,
                    basis, program_net, arbitrage_net, non_arbitrage_net,
                    kospi200_futures_price, futures_day_high, futures_day_low,
                    futures_vwap, market_strength_score, foreign_futures_score,
                    basis_score, program_score, futures_trend_score,
                    interpretation_text, collected_at_kst
                )
                SELECT
                    trade_date, 'closing', '종가 흐름', snapshot_time, foreign_futures_net,
                    basis, program_net, arbitrage_net, non_arbitrage_net,
                    kospi200_futures_price, futures_day_high, futures_day_low,
                    futures_vwap, market_strength_score, foreign_futures_score,
                    basis_score, program_score, futures_trend_score,
                    interpretation_text, collected_at_kst
                FROM market_strength_snapshots_legacy
                """
            )
            conn.execute("DROP TABLE market_strength_snapshots_legacy")
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

    def _kis_get(self, path, tr_id, params, tr_cont=""):
        token = self._get_kis_access_token()
        res = requests.get(
            f"{self.kis_base_url}{path}",
            headers={
                "content-type": "application/json; charset=utf-8",
                "authorization": f"Bearer {token}",
                "appkey": self.kis_app_key,
                "appsecret": self.kis_app_secret,
                "tr_id": tr_id,
                "tr_cont": tr_cont,
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
        data = dict(data)
        data["_response_tr_cont"] = res.headers.get("tr_cont", "")
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
    def _hhmmss_to_seconds(value):
        text = str(value).zfill(6)
        if not text.isdigit() or len(text) != 6:
            return None
        hour, minute, second = int(text[:2]), int(text[2:4]), int(text[4:6])
        if hour > 23 or minute > 59 or second > 59:
            return None
        return hour * 3600 + minute * 60 + second

    @staticmethod
    def _seconds_to_hhmmss(value):
        value = max(0, int(value))
        hour, remainder = divmod(value, 3600)
        minute, second = divmod(remainder, 60)
        return f"{hour:02d}{minute:02d}{second:02d}"

    @classmethod
    def _nearest_row(
        cls, rows, time_key, time_field, max_gap_seconds=None, allow_future=True
    ):
        target = cls._hhmmss_to_seconds(time_key)
        if target is None:
            return {}
        candidates = []
        for row in rows:
            row_time = cls._hhmmss_to_seconds(row.get(time_field, ""))
            if row_time is None:
                continue
            if row_time <= target:
                candidates.append((row_time, row))
        if candidates:
            row_time, row = max(candidates, key=lambda item: item[0])
            if max_gap_seconds is None or target - row_time <= max_gap_seconds:
                return row
            return {}
        if not allow_future:
            return {}
        valid_rows = [
            (cls._hhmmss_to_seconds(row.get(time_field, "")), row)
            for row in rows
            if cls._hhmmss_to_seconds(row.get(time_field, "")) is not None
        ]
        if not valid_rows:
            return {}
        row_time, row = min(valid_rows, key=lambda item: abs(item[0] - target))
        if max_gap_seconds is not None and abs(row_time - target) > max_gap_seconds:
            return {}
        return row

    def _fetch_backward_pages(self, path, tr_id, base_params, time_field):
        """Collect KIS intraday pages until the earliest requested snapshot is covered."""
        earliest = self._hhmmss_to_seconds(self._time_to_hhmmss(self.snapshot_times[0]))
        cursor = self._hhmmss_to_seconds(self._time_to_hhmmss(self.snapshot_times[-1]))
        collected = {}
        seen_pages = set()
        first_data = None
        for _ in range(20):
            params = dict(base_params)
            params["FID_INPUT_HOUR_1"] = self._seconds_to_hhmmss(cursor)
            data = self._kis_get(path, tr_id, params)
            if first_data is None:
                first_data = data
            rows = data.get("output2", []) or []
            page_times = tuple(str(row.get(time_field, "")).zfill(6) for row in rows)
            if not rows or page_times in seen_pages:
                break
            seen_pages.add(page_times)
            oldest = None
            for row in rows:
                raw_time = str(row.get(time_field, "")).zfill(6)
                row_seconds = self._hhmmss_to_seconds(raw_time)
                if row_seconds is None:
                    continue
                collected[raw_time] = row
                oldest = row_seconds if oldest is None else min(oldest, row_seconds)
            if oldest is None or oldest <= earliest:
                break
            next_cursor = oldest - 60
            if next_cursor >= cursor:
                break
            cursor = next_cursor
        return list(collected.values()), (first_data or {})

    def _intraday_vwap(self, rows, time_key):
        target = int(time_key)
        weighted_sum = 0.0
        total_volume = 0.0
        for row in rows:
            raw_time = str(row.get("stck_cntg_hour", "")).zfill(6)
            if not raw_time.isdigit() or int(raw_time) > target:
                continue
            price = self._to_float(row.get("futs_prpr"))
            volume = self._to_float(row.get("cntg_vol"))
            if price > 0 and volume > 0:
                weighted_sum += price * volume
                total_volume += volume
        return weighted_sum / total_volume if total_volume else 0.0

    def _fetch_program_snapshots(self):
        program_db_path = os.environ.get("PROGRAM_SNAPSHOT_DB", "").strip() or self.db_path
        rows = []
        if os.path.exists(program_db_path):
            try:
                conn = sqlite3.connect(program_db_path)
                rows = conn.execute(
                    """
                    SELECT snapshot_time, program_net, arbitrage_net, non_arbitrage_net
                    FROM market_program_snapshots
                    WHERE trade_date = ? AND analysis_type = ?
                    """,
                    (self.target_date, self.analysis_type),
                ).fetchall()
                conn.close()
            except sqlite3.OperationalError:
                rows = []
        collected = {
            row[0]: {
                "program_net": self._to_float(row[1]),
                "arbitrage_net": self._to_float(row[2]),
                "non_arbitrage_net": self._to_float(row[3]),
            }
            for row in rows
        }
        if all(snapshot_time in collected for snapshot_time in self.snapshot_times):
            print(f"[Market Strength] WebSocket 프로그램 수급 {len(self.snapshot_times)}개 시점을 사용합니다.")
            return {snapshot_time: collected[snapshot_time] for snapshot_time in self.snapshot_times}

        print("[Market Strength Warning] WebSocket 프로그램 수급이 완전하지 않아 KIS REST 값을 사용합니다.")
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
        for snapshot_time in self.snapshot_times:
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
        rows, data = self._fetch_backward_pages(
            "/uapi/domestic-futureoption/v1/quotations/inquire-time-fuopchartprice",
            "FHKIF03020200",
            {
                "FID_COND_MRKT_DIV_CODE": "F",
                "FID_INPUT_ISCD": futures_code,
                "FID_HOUR_CLS_CODE": "60",
                "FID_PW_DATA_INCU_YN": "Y",
                "FID_FAKE_TICK_INCU_YN": "N",
                "FID_INPUT_DATE_1": self.target_date,
            },
            "stck_cntg_hour",
        )
        current_info = data.get("output1", {}) or {}
        index_snapshots = self._fetch_index_snapshots()
        day_high = self._to_float(current_info.get("futs_hgpr"))
        day_low = self._to_float(current_info.get("futs_lwpr"))

        snapshots = {}
        for snapshot_time in self.snapshot_times:
            row = self._nearest_row(
                rows,
                self._time_to_hhmmss(snapshot_time),
                "stck_cntg_hour",
                max_gap_seconds=self.BASIS_MAX_TIME_GAP_SECONDS,
                allow_future=False,
            )
            price = self._to_float(row.get("futs_prpr"))
            index_price = index_snapshots.get(snapshot_time)
            calculated_basis = price - index_price if index_price else None
            vwap = self._intraday_vwap(rows, self._time_to_hhmmss(snapshot_time))
            valid_basis = (
                calculated_basis
                if calculated_basis is not None and abs(calculated_basis) <= self.BASIS_MAX_ABS
                else None
            )
            snapshots[snapshot_time] = {
                "basis": valid_basis,
                "kospi200_futures_price": price,
                "futures_day_high": day_high,
                "futures_day_low": day_low,
                "futures_vwap": vwap or price,
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
        # 베이시스는 반드시 KOSPI200(2001)과 KOSPI200 선물의 동일 시각 값으로 계산합니다.
        try:
            rows = self._fetch_index_minute_rows()
        except Exception as e:
            print(f"[Market Strength Warning] KOSPI200 지수 분봉 조회 실패(code=2001): {e}")
            return {}
        snapshots = {}
        for snapshot_time in self.snapshot_times:
            row = self._nearest_row(
                rows,
                self._time_to_hhmmss(snapshot_time),
                "stck_cntg_hour",
                max_gap_seconds=self.BASIS_MAX_TIME_GAP_SECONDS,
                allow_future=False,
            )
            price = self._extract_index_price(row)
            if price:
                snapshots[snapshot_time] = price
        return snapshots

    def _fetch_index_minute_rows(self):
        """Collect KOSPI200 one-minute bars using the index API continuation header."""
        earliest = self._hhmmss_to_seconds(self._time_to_hhmmss(self.snapshot_times[0]))
        params = {
            "FID_COND_MRKT_DIV_CODE": "U",
            "FID_ETC_CLS_CODE": "0",
            "FID_INPUT_ISCD": "2001",
            # This field is the bar interval, not an HHMMSS query cursor.
            "FID_INPUT_HOUR_1": "60",
            "FID_PW_DATA_INCU_YN": "Y",
        }
        collected = {}
        seen_pages = set()
        request_tr_cont = ""
        for _ in range(20):
            data = self._kis_get(
                "/uapi/domestic-stock/v1/quotations/inquire-time-indexchartprice",
                "FHKUP03500200",
                params,
                tr_cont=request_tr_cont,
            )
            rows = data.get("output2", []) or []
            page_times = tuple(str(row.get("stck_cntg_hour", "")).zfill(6) for row in rows)
            if not rows or page_times in seen_pages:
                break
            seen_pages.add(page_times)

            oldest = None
            for row in rows:
                row_date = str(row.get("stck_bsop_date", "")).strip()
                if row_date and row_date != self.target_date:
                    continue
                raw_time = str(row.get("stck_cntg_hour", "")).zfill(6)
                row_seconds = self._hhmmss_to_seconds(raw_time)
                if row_seconds is None:
                    continue
                collected[raw_time] = row
                oldest = row_seconds if oldest is None else min(oldest, row_seconds)
            if oldest is not None and oldest <= earliest:
                break

            response_tr_cont = str(data.get("_response_tr_cont", "")).upper()
            if response_tr_cont not in {"M", "F"}:
                break
            request_tr_cont = "N"
        return list(collected.values())

    def _score_basis(self, snapshots):
        values = [snapshots[t]["basis"] for t in self.snapshot_times]
        if not self._basis_is_valid(snapshots):
            return 0
        delta = values[-1] - values[0]
        last = values[-1]

        if last >= 3:
            score = 15
        elif last >= 1:
            score = 12
        elif last >= 0:
            score = 8
        elif last >= -1:
            score = 4
        else:
            score = 0

        if delta >= 1:
            score += 10
        elif delta >= 0.5:
            score += 7
        elif delta >= 0.1:
            score += 4

        positive_steps = sum(curr > prev for prev, curr in zip(values, values[1:]))
        score += round(5 * positive_steps / max(1, len(values) - 1))

        futures = [snapshots[t]["kospi200_futures_price"] for t in self.snapshot_times]
        if delta > 0 and futures[-1] > futures[0]:
            score += 5

        if self._has_basis_outlier(values):
            score -= 10
        return max(0, min(35, int(round(score))))

    def _score_program(self, snapshots):
        program = [snapshots[t]["program_net"] for t in self.snapshot_times]
        non_arbitrage = [snapshots[t]["non_arbitrage_net"] for t in self.snapshot_times]
        score = 15 if program[-1] > 0 else 0
        score += self._improvement_points(program[0], program[-1], 10)
        score += self._improvement_points(non_arbitrage[0], non_arbitrage[-1], 5)
        score += self._improvement_points(program[-2], program[-1], 5)
        score = max(0, min(35, int(round(score))))

        # 순매도 상태에서 방향만 소폭 개선된 경우 높은 등급을 막습니다.
        if program[-1] < 0:
            score = min(score, 15)
        return score

    def _score_futures_trend(self, snapshots):
        prices = [snapshots[t]["kospi200_futures_price"] for t in self.snapshot_times]
        last = prices[-1]
        day_high = snapshots[self.snapshot_times[-1]]["futures_day_high"]
        day_low = snapshots[self.snapshot_times[-1]]["futures_day_low"]
        vwap = snapshots[self.snapshot_times[-1]]["futures_vwap"]
        score = self._return_points(prices[0], last, 10)
        if len(prices) >= 4:
            score += self._return_points(prices[1], last, 10)
        else:
            score += self._return_points(prices[0], last, 10)
        score += self._return_points(prices[-2], last, 3)

        day_range = day_high - day_low
        if day_range > 0:
            range_position = (last - day_low) / day_range
            if range_position >= 0.75:
                score += 4
            elif range_position >= 0.5:
                score += 2
        if self._is_valid_reference(vwap, last) and last > vwap:
            score += 3
        return max(0, min(30, int(round(score))))

    @staticmethod
    def _improvement_points(start, end, maximum):
        scale = max(abs(start), 1.0)
        improvement = (end - start) / scale
        if improvement >= 0.10:
            return maximum
        if improvement >= 0.05:
            return round(maximum * 0.7)
        if improvement >= 0.01:
            return round(maximum * 0.3)
        return 0

    @staticmethod
    def _return_points(start, end, maximum):
        if start <= 0:
            return 0
        change_pct = (end / start - 1) * 100
        if change_pct >= 0.5:
            return maximum
        if change_pct >= 0.2:
            return round(maximum * 0.7)
        if change_pct > 0:
            return round(maximum * 0.5)
        if change_pct > -0.2:
            return round(maximum * 0.25)
        return 0

    @staticmethod
    def _is_valid_reference(reference, price):
        return price > 0 and price * 0.8 <= reference <= price * 1.2

    @staticmethod
    def _has_basis_outlier(values):
        values = [value for value in values if value is not None and math.isfinite(float(value))]
        if len(values) < 2:
            return False
        return any(
            abs(curr - prev) > 10 and abs(curr - prev) > 5 * max(abs(prev), 1)
            for prev, curr in zip(values, values[1:])
        )

    def _basis_is_valid(self, snapshots):
        basis = [snapshots[t].get("basis") for t in self.snapshot_times]
        if any(value is None or not math.isfinite(float(value)) for value in basis):
            return False
        if any(abs(float(value)) > self.BASIS_MAX_ABS for value in basis):
            return False
        futures = [snapshots[t]["kospi200_futures_price"] for t in self.snapshot_times]
        futures_move = (max(futures) - min(futures)) / max(abs(futures[0]), 1)
        basis_is_flat = max(basis) - min(basis) < 1e-9
        return not (basis_is_flat and futures_move >= 0.0005)

    def _data_quality_issues(self, snapshots):
        last = snapshots[self.snapshot_times[-1]]
        issues = []
        basis = [snapshots[t]["basis"] for t in self.snapshot_times]
        basis_complete = all(
            value is not None and math.isfinite(float(value)) for value in basis
        )
        basis_out_of_range = basis_complete and any(
            abs(float(value)) > self.BASIS_MAX_ABS for value in basis
        )
        if basis_complete and (basis_out_of_range or self._has_basis_outlier(basis)):
            issues.append("베이시스 급변값")
        elif not self._basis_is_valid(snapshots):
            issues.append("베이시스 시점값 누락/복제")
        price = last["kospi200_futures_price"]
        if not self._is_valid_reference(last["futures_vwap"], price):
            issues.append("선물 VWAP 오류")
        if not (last["futures_day_low"] <= price <= last["futures_day_high"]):
            issues.append("선물 고저가 오류")
        return issues

    def explain_scores(self, snapshots):
        basis = [snapshots[t]["basis"] for t in self.snapshot_times]
        program = [snapshots[t]["program_net"] for t in self.snapshot_times]
        non_arbitrage = [snapshots[t]["non_arbitrage_net"] for t in self.snapshot_times]
        futures = [snapshots[t]["kospi200_futures_price"] for t in self.snapshot_times]
        last = snapshots[self.snapshot_times[-1]]

        basis_complete = all(
            value is not None and math.isfinite(float(value)) for value in basis
        )
        basis_out_of_range = basis_complete and any(
            abs(float(value)) > self.BASIS_MAX_ABS for value in basis
        )
        if basis_complete and (basis_out_of_range or self._has_basis_outlier(basis)):
            basis_parts = ["급변값은 신뢰도가 낮아 감점", "베이시스 점수에서 제외"]
        elif not self._basis_is_valid(snapshots):
            basis_parts = ["시점별 값이 누락되거나 복제되어 점수에서 제외", "프로그램·선물 점수로 재환산"]
        else:
            basis_parts = ["양(+)의 베이시스로 선물 우위는 인정"] if basis[-1] > 0 else ["음(-)의 베이시스로 선물 우위 가점 없음"]
            basis_parts.append("장 후반 확대 흐름도 가점" if basis[-1] - basis[0] >= 0.1 else "장 후반 확대 흐름이 약해 추세 가점 없음")
            if self._has_basis_outlier(basis):
                basis_parts.append("중간 급변값은 신뢰도가 낮아 감점")

        program_parts = []
        if program[-1] < 0:
            program_parts.append("종가까지 순매도라 절대 수급 가점 없음")
        else:
            program_parts.append("종가 순매수로 절대 수급 가점")
        total_improvement = (program[-1] - program[0]) / max(abs(program[0]), 1)
        if total_improvement < 0.01:
            program_parts.append("전체 개선폭이 1% 미만이라 추세 가점 없음")
        else:
            program_parts.append("전체 수급 개선폭은 의미 있어 가점")
        non_arbitrage_improvement = (non_arbitrage[-1] - non_arbitrage[0]) / max(abs(non_arbitrage[0]), 1)
        if non_arbitrage_improvement >= 0.01:
            program_parts.append("비차익 순매도 완화만 일부 인정")
        elif non_arbitrage[-1] > 0:
            program_parts.append("비차익 순매수를 추가 반영")
        else:
            program_parts.append("비차익 수급도 가점 없음")

        full_change = (futures[-1] / futures[0] - 1) * 100 if futures[0] > 0 else 0
        middle_base = futures[1] if len(futures) >= 4 else futures[0]
        middle_change = (futures[-1] / middle_base - 1) * 100 if middle_base > 0 else 0
        late_change = (futures[-1] / futures[-2] - 1) * 100 if futures[-2] > 0 else 0
        futures_parts = []
        futures_parts.append("전체 구간 상승은 가점" if full_change > 0 else "전체 구간 하락으로 가점 없음")
        futures_parts.append("중간 이후 상승도 확인" if middle_change > 0 else "중간 이후 하락이어서 추세 가점 없음")
        if late_change > 0:
            futures_parts.append("마지막 반등은 보조점수만 인정")
        else:
            futures_parts.append("마지막 구간도 약해 보조점수 없음")
        day_range = last["futures_day_high"] - last["futures_day_low"]
        range_position = (futures[-1] - last["futures_day_low"]) / day_range if day_range > 0 else 0
        if range_position < 0.5:
            futures_parts.append("장중 범위 하단이라 추가 가점 없음")
        if not self._is_valid_reference(last["futures_vwap"], futures[-1]):
            futures_parts.append("VWAP 오류값은 점수에서 제외")

        return {
            "basis": ", ".join(basis_parts),
            "program": ", ".join(program_parts),
            "futures": ", ".join(futures_parts),
        }

    def _build_interpretation(self, score, basis_score, program_score, futures_score, snapshots):
        basis_valid = self._basis_is_valid(snapshots)
        basis_delta = (
            snapshots[self.snapshot_times[-1]]["basis"] - snapshots[self.snapshot_times[0]]["basis"]
            if basis_valid else 0
        )
        program_delta = snapshots[self.snapshot_times[-1]]["program_net"] - snapshots[self.snapshot_times[0]]["program_net"]
        futures_start = snapshots[self.snapshot_times[0]]["kospi200_futures_price"]
        futures_last = snapshots[self.snapshot_times[-1]]["kospi200_futures_price"]

        parts = []
        if basis_valid:
            parts.append("베이시스가 장 막판 확대되었습니다" if basis_delta > 0 else "베이시스가 장 막판 축소되었습니다")
        else:
            parts.append("시점별 베이시스는 데이터 오류로 점수에서 제외했습니다")
        program_last = snapshots[self.snapshot_times[-1]]["program_net"]
        if program_last < 0:
            parts.append("프로그램 순매도가 이어지고 있습니다")
        elif program_delta > 0:
            parts.append("프로그램 순매수가 증가했습니다")
        else:
            parts.append("프로그램 매수 강도는 약화되었습니다")
        parts.append("전체 구간에서 선물이 상승했습니다" if futures_last > futures_start else "전체 구간에서 선물이 하락했습니다")

        issues = self._data_quality_issues(snapshots)
        if issues:
            parts.append(f"데이터 확인 필요: {', '.join(issues)}")

        if score >= 80:
            tail = "종가베팅 시장 환경은 매우 우호적입니다."
        elif score >= 70:
            tail = "종가베팅 시장 환경은 우호적입니다."
        elif score >= 60:
            tail = "종가베팅은 보통 수준의 환경에서 선별 접근이 필요합니다."
        elif score >= 50:
            tail = "시장 신호가 엇갈려 종가베팅은 주의가 필요합니다."
        else:
            tail = "장 막판 수급이 약해 종가베팅은 신중하게 접근하는 것이 좋습니다."

        return f"{', '.join(parts)}. {tail}"

    def score_snapshots(self, snapshots):
        basis_score = self._score_basis(snapshots)
        program_score = self._score_program(snapshots)
        futures_score = self._score_futures_trend(snapshots)
        basis_valid = self._basis_is_valid(snapshots)
        if basis_valid:
            total_score = basis_score + program_score + futures_score
        else:
            total_score = round((program_score + futures_score) / 65 * 100)

        issues = self._data_quality_issues(snapshots)
        non_basis_issues = [issue for issue in issues if issue != "베이시스 시점값 누락/복제"]
        if non_basis_issues:
            total_score = min(total_score, 49)
        elif not basis_valid:
            total_score = min(total_score, 69)
        if snapshots[self.snapshot_times[-1]]["program_net"] < 0:
            total_score = min(total_score, 69)
        return {
            "market_strength_score": max(0, min(100, int(total_score))),
            "basis_score": basis_score,
            "program_score": program_score,
            "futures_trend_score": futures_score,
            "basis_valid": basis_valid,
        }

    def _combine_snapshots(self, program_snapshots, futures_snapshots):
        snapshots = {}
        for snapshot_time in self.snapshot_times:
            snapshots[snapshot_time] = {
                "foreign_futures_net": None,
                **program_snapshots.get(snapshot_time, {}),
                **futures_snapshots.get(snapshot_time, {}),
            }
        return snapshots

    def _save_snapshots(self, snapshots, scores, interpretation_text):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "DELETE FROM market_strength_snapshots WHERE trade_date = ? AND analysis_type = ?",
            (self.target_date, self.analysis_type),
        )
        for snapshot_time in self.snapshot_times:
            row = snapshots[snapshot_time]
            conn.execute(
                """
                INSERT OR REPLACE INTO market_strength_snapshots (
                    trade_date, analysis_type, analysis_label, snapshot_time,
                    foreign_futures_net, basis, program_net, arbitrage_net,
                    non_arbitrage_net, kospi200_futures_price,
                    futures_day_high, futures_day_low, futures_vwap,
                    market_strength_score, foreign_futures_score, basis_score,
                    program_score, futures_trend_score, interpretation_text, collected_at_kst
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.target_date,
                    self.analysis_type,
                    self._analysis_label(),
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
        if self.analysis_type == "closing" and (now.hour < 15 or (now.hour == 15 and now.minute < 35)):
            print("[Market Strength] 15:35 이전에는 시장강도 분석을 건너뜁니다.")
            return None
        if self.analysis_type == "morning" and (now.hour < 9 or (now.hour == 9 and now.minute < 45)):
            print("[Market Strength] Morning analysis is skipped before 09:45 KST.")
            return None
        if self.analysis_type == "afternoon" and now.hour < 14:
            print("[Market Strength] Afternoon analysis is skipped before 14:00 KST.")
            return None

        program_snapshots = self._fetch_program_snapshots()
        futures_snapshots = self._fetch_futures_snapshots()
        snapshots = self._combine_snapshots(program_snapshots, futures_snapshots)

        scores = self.score_snapshots(snapshots)
        basis_score = scores["basis_score"]
        program_score = scores["program_score"]
        futures_score = scores["futures_trend_score"]
        total_score = scores["market_strength_score"]
        interpretation_text = self._build_interpretation(
            total_score, basis_score, program_score, futures_score, snapshots
        )
        self._save_snapshots(snapshots, scores, interpretation_text)
        print(f"[Market Strength] 시장강도 {total_score}점 저장 완료")
        return snapshots, scores, interpretation_text
