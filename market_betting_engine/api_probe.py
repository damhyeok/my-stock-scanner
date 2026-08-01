"""Read-only API audit probes for the market-betting engine.

Only explicitly allow-listed quotation endpoints can be called. The module
never calls order, account, balance, or position endpoints. Saved payloads are
redacted and truncated before they leave process memory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import requests

try:
    from dotenv import load_dotenv
except ImportError:  # Keep the audit CLI usable in the repository's lean test environment.
    def load_dotenv(dotenv_path=None, override=False):
        path = Path(dotenv_path or ".env")
        if not path.exists():
            return False
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if override or key not in os.environ:
                os.environ[key] = value
        return True


KST = timezone(timedelta(hours=9))
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "reports" / "api_probes"
DEFAULT_DB_PATH = DEFAULT_OUTPUT_DIR / "api_probe_results.db"

SENSITIVE_KEY_PARTS = (
    "authorization",
    "access_token",
    "approval_key",
    "appkey",
    "app_key",
    "appsecret",
    "app_secret",
    "secretkey",
    "secret_key",
    "account",
    "cano",
    "acnt",
    "hts_id",
)

KIS_ALLOWED_PATHS = frozenset(
    {
        "/uapi/domestic-stock/v1/quotations/inquire-price",
        "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
        "/uapi/domestic-stock/v1/quotations/inquire-time-indexchartprice",
        "/uapi/domestic-stock/v1/quotations/comp-program-trade-today",
        "/uapi/domestic-stock/v1/quotations/inquire-investor",
        "/uapi/domestic-futureoption/v1/quotations/display-board-futures",
        "/uapi/domestic-futureoption/v1/quotations/inquire-time-fuopchartprice",
    }
)

KIWOOM_ALLOWED_PATHS = frozenset(
    {
        "/api/dostk/chart",
        "/api/dostk/mrkcond",
    }
)

BLOCKED_PATH_TOKENS = (
    "order",
    "orders",
    "account",
    "balance",
    "position",
    "buy",
    "sell",
    "trading/order",
)


@dataclass(frozen=True)
class ProbeSpec:
    probe_id: str
    provider: str
    transport: str
    name: str
    path: str | None
    operation_code: str
    response_container: str | None
    expected_fields: tuple[str, ...] = ()
    current_status: str = "UNVERIFIED"
    executable: bool = True
    market_session_required: bool = False
    notes: str = ""


@dataclass
class ProbeResult:
    run_id: str
    probe_id: str
    provider: str
    transport: str
    started_at_kst: str
    completed_at_kst: str
    execution_status: str
    verification_status: str
    endpoint: str | None
    operation_code: str
    http_status: int | None = None
    provider_code: str | int | None = None
    provider_message: str | None = None
    response_top_level_keys: list[str] = field(default_factory=list)
    output_row_count: int | None = None
    source_trade_dates: list[str] = field(default_factory=list)
    source_times: list[str] = field(default_factory=list)
    observed_fields: list[str] = field(default_factory=list)
    missing_expected_fields: list[str] = field(default_factory=list)
    schema: dict[str, Any] = field(default_factory=dict)
    sanitized_sample: Any = None
    error_type: str | None = None
    error_message: str | None = None
    notes: list[str] = field(default_factory=list)


PROBE_SPECS: dict[str, ProbeSpec] = {
    "kis_stock_price": ProbeSpec(
        "kis_stock_price", "KIS", "REST", "국내주식 현재가",
        "/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100",
        "output", ("stck_prpr", "stck_oprc", "stck_hgpr", "stck_lwpr", "acml_vol"),
        notes="종목코드는 CLI --ticker로 지정",
    ),
    "kis_stock_minute": ProbeSpec(
        "kis_stock_minute", "KIS", "REST", "국내주식 당일 분봉",
        "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice", "FHKST03010200",
        "output2", ("stck_cntg_hour", "stck_prpr", "stck_oprc", "stck_hgpr", "stck_lwpr", "cntg_vol"),
    ),
    "kis_index_minute_kospi": ProbeSpec(
        "kis_index_minute_kospi", "KIS", "REST", "KOSPI 업종 분봉",
        "/uapi/domestic-stock/v1/quotations/inquire-time-indexchartprice", "FHKUP03500200",
        "output2", ("stck_cntg_hour",),
        notes="지수 가격 필드명은 실제 응답에서 확인",
    ),
    "kis_program_summary_kospi": ProbeSpec(
        "kis_program_summary_kospi", "KIS", "REST", "KOSPI 프로그램매매 종합",
        "/uapi/domestic-stock/v1/quotations/comp-program-trade-today", "FHPPG04600101",
        "output", (
            "bsop_hour", "whol_smtn_ntby_tr_pbmn", "arbt_smtn_ntby_tr_pbmn",
            "nabt_smtn_ntby_tr_pbmn",
        ),
        notes="최근 30분 제공 제약과 금액 단위를 함께 확인",
    ),
    "kis_investor_stock": ProbeSpec(
        "kis_investor_stock", "KIS", "REST", "종목별 투자자 수급",
        "/uapi/domestic-stock/v1/quotations/inquire-investor", "FHKST01010900",
        "output", ("stck_bsop_date", "frgn_ntby_tr_pbmn", "orgn_ntby_tr_pbmn"),
        notes="당일 제공 시각과 금액 단위를 확인",
    ),
    "kis_futures_board": ProbeSpec(
        "kis_futures_board", "KIS", "REST", "KOSPI200 선물 전광판",
        "/uapi/domestic-futureoption/v1/quotations/display-board-futures", "FHPIF05030200",
        "output", ("futs_shrn_iscd", "acml_vol"),
        notes="최근월물 선정과 베이시스 관련 필드를 실제 응답에서 확인",
    ),
    "kis_futures_minute_active": ProbeSpec(
        "kis_futures_minute_active", "KIS", "REST_COMPOSITE", "활성 KOSPI200 선물 분봉",
        "/uapi/domestic-futureoption/v1/quotations/inquire-time-fuopchartprice", "FHKIF03020200",
        "output2", ("stck_cntg_hour", "futs_prpr", "cntg_vol"),
        notes="선물 전광판에서 거래량 최대 종목을 먼저 선택",
    ),
    "kiwoom_stock_minute": ProbeSpec(
        "kiwoom_stock_minute", "KIWOOM", "REST", "키움 종목 분봉",
        "/api/dostk/chart", "ka10080", "stk_min_pole_chart_qry",
        ("cntr_tm", "cur_prc", "open_pric", "high_pric", "low_pric", "trde_qty"),
    ),
    "kiwoom_program_basis_current_session": ProbeSpec(
        "kiwoom_program_basis_current_session", "KIWOOM", "REST",
        "키움 현재 세션 프로그램·베이시스", "/api/dostk/mrkcond", "ka90005",
        "prm_trde_trnsn", ("cntr_tm", "all_netprps", "all_sel", "all_buy", "basis"),
        market_session_required=True,
        notes="응답에 검증 가능한 거래일이 없어 과거 날짜 반복 호출 금지",
    ),
    "kiwoom_market_breadth": ProbeSpec(
        "kiwoom_market_breadth", "KIWOOM", "REST", "시장 전체 상승·하락 종목 수",
        None, "ka20001/OPT20001", None, executable=False,
        notes="REST 요청 경로·본문·응답 필드가 공식 원문에서 확인될 때까지 호출 금지",
    ),
    "kis_program_ws": ProbeSpec(
        "kis_program_ws", "KIS", "WEBSOCKET", "실시간 프로그램매매",
        None, "H0UPPGM0", None, executable=False, market_session_required=True,
        notes="장중 패킷 캡처 검증 대기",
    ),
    "kis_trade_ws": ProbeSpec(
        "kis_trade_ws", "KIS", "WEBSOCKET", "실시간 주식 체결",
        None, "H0STCNT0", None, executable=False, market_session_required=True,
        notes="CCLD_DVSN 의미와 시각 정밀도 장중 검증 대기",
    ),
    "kis_orderbook_ws": ProbeSpec(
        "kis_orderbook_ws", "KIS", "WEBSOCKET", "실시간 주식 호가",
        None, "H0STASP0", None, executable=False, market_session_required=True,
        notes="체결 스트림과 이벤트 순서 장중 검증 대기",
    ),
}


def now_kst() -> datetime:
    return datetime.now(KST)


def iso_kst(value: datetime | None = None) -> str:
    return (value or now_kst()).isoformat(timespec="seconds")


def _is_sensitive_key(key: Any) -> bool:
    normalized = str(key).lower().replace("-", "_")
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def redact_sensitive(value: Any, *, max_list_items: int = 3, depth: int = 0) -> Any:
    """Recursively redact secrets and bound saved response size."""
    if depth > 8:
        return "<MAX_DEPTH>"
    if isinstance(value, Mapping):
        return {
            str(key): "<REDACTED>" if _is_sensitive_key(key) else redact_sensitive(
                item, max_list_items=max_list_items, depth=depth + 1
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        selected = list(value[:max_list_items])
        redacted = [
            redact_sensitive(item, max_list_items=max_list_items, depth=depth + 1)
            for item in selected
        ]
        if len(value) > max_list_items:
            redacted.append({"_truncated_items": len(value) - max_list_items})
        return redacted
    if isinstance(value, str):
        text = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+\-/=]+", "Bearer <REDACTED>", value)
        text = re.sub(
            r'(?i)(access_token|approval_key|appsecret|secretkey)(["\s:=]+)([^\s,"}]+)',
            r"\1\2<REDACTED>",
            text,
        )
        return text[:2000] + ("<TRUNCATED>" if len(text) > 2000 else "")
    return value


def infer_schema(value: Any, *, depth: int = 0) -> Any:
    """Return a compact structural schema without retaining payload values."""
    if depth > 8:
        return {"type": "max_depth"}
    if isinstance(value, Mapping):
        return {
            "type": "object",
            "fields": {str(key): infer_schema(item, depth=depth + 1) for key, item in value.items()},
        }
    if isinstance(value, list):
        return {
            "type": "array",
            "length": len(value),
            "item": infer_schema(value[0], depth=depth + 1) if value else None,
        }
    if value is None:
        type_name = "null"
    elif isinstance(value, bool):
        type_name = "boolean"
    elif isinstance(value, int):
        type_name = "integer"
    elif isinstance(value, float):
        type_name = "number"
    else:
        type_name = "string"
    return {"type": type_name}


def output_rows(payload: Mapping[str, Any], container: str | None) -> list[Mapping[str, Any]]:
    if not container:
        return []
    value = payload.get(container)
    if isinstance(value, list):
        return [row for row in value if isinstance(row, Mapping)]
    if isinstance(value, Mapping):
        return [value]
    return []


def extract_source_dates_and_times(
    rows: list[Mapping[str, Any]],
) -> tuple[list[str], list[str]]:
    dates: set[str] = set()
    times: set[str] = set()
    for row in rows:
        for key in ("stck_bsop_date", "bsop_date"):
            value = str(row.get(key) or "").strip()
            if len(value) == 8 and value.isdigit():
                dates.add(value)
        combined = str(row.get("cntr_tm") or "").strip()
        if len(combined) == 14 and combined.isdigit():
            dates.add(combined[:8])
            times.add(combined[8:])
        for key in ("stck_cntg_hour", "bsop_hour"):
            value = str(row.get(key) or "").strip()
            if value:
                times.add(value.zfill(6))
    return sorted(dates), sorted(times)


def validate_read_only_path(provider: str, path: str) -> None:
    lower_path = path.lower()
    if any(token in lower_path for token in BLOCKED_PATH_TOKENS):
        raise ValueError(f"Potentially mutating or private endpoint is blocked: {path}")
    allowed = KIS_ALLOWED_PATHS if provider == "KIS" else KIWOOM_ALLOWED_PATHS
    if path not in allowed:
        raise ValueError(f"Endpoint is not in the read-only allow-list: {provider} {path}")


class KisReadOnlyClient:
    def __init__(self, timeout: int = 15):
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        self.app_key = os.environ.get("KIS_APP_KEY", "").strip()
        self.app_secret = os.environ.get("KIS_APP_SECRET", "").strip()
        if not self.app_key or not self.app_secret:
            raise RuntimeError("KIS_APP_KEY and KIS_APP_SECRET are required")
        self.base_url = os.environ.get(
            "KIS_API_BASE_URL", "https://openapi.koreainvestment.com:9443"
        ).rstrip("/")
        self.timeout = timeout
        self._access_token: str | None = None

    def _cache_path(self) -> Path:
        return Path(os.environ.get("KIS_TOKEN_CACHE", PROJECT_ROOT / ".kis_token_cache.json"))

    def _load_cached_token(self) -> str | None:
        cache_path = self._cache_path()
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            expected_hash = hashlib.sha256(self.app_key.encode("utf-8")).hexdigest()
            if (
                cached.get("app_key_hash") == expected_hash
                and float(cached.get("expires_at", 0)) > time.time() + 300
                and cached.get("access_token")
            ):
                return str(cached["access_token"])
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return None
        return None

    def _token(self) -> str:
        if self._access_token:
            return self._access_token
        cached = self._load_cached_token()
        if cached:
            self._access_token = cached
            return cached
        response = requests.post(
            f"{self.base_url}/oauth2/tokenP",
            headers={"content-type": "application/json"},
            json={
                "grant_type": "client_credentials",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise RuntimeError("KIS token response did not include access_token")
        self._access_token = str(token)
        expires_in = int(payload.get("expires_in", 23 * 60 * 60))
        cache_path = self._cache_path()
        cache_path.write_text(
            json.dumps(
                {
                    "app_key_hash": hashlib.sha256(self.app_key.encode("utf-8")).hexdigest(),
                    "access_token": self._access_token,
                    "expires_at": time.time() + expires_in,
                }
            ),
            encoding="utf-8",
        )
        if os.name != "nt":
            cache_path.chmod(0o600)
        return self._access_token

    def get(self, path: str, tr_id: str, params: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
        validate_read_only_path("KIS", path)
        response = requests.get(
            f"{self.base_url}{path}",
            headers={
                "content-type": "application/json; charset=utf-8",
                "authorization": f"Bearer {self._token()}",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
                "tr_id": tr_id,
                "custtype": "P",
            },
            params=dict(params),
            timeout=self.timeout,
        )
        status = response.status_code
        response.raise_for_status()
        return response.json(), status


class KiwoomReadOnlyClient:
    def __init__(self, timeout: int = 30):
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        self.app_key = os.environ.get("KIWOOM_APP_KEY", "").strip()
        self.secret_key = os.environ.get("KIWOOM_SECRET_KEY", "").strip()
        if not self.app_key or not self.secret_key:
            raise RuntimeError("KIWOOM_APP_KEY and KIWOOM_SECRET_KEY are required")
        self.base_url = os.environ.get("KIWOOM_API_BASE_URL", "https://api.kiwoom.com").rstrip("/")
        self.timeout = timeout
        self._access_token: str | None = None

    def _post_raw(
        self, path: str, body: Mapping[str, Any], headers: Mapping[str, str] | None = None
    ) -> tuple[dict[str, Any], int]:
        response = requests.post(
            f"{self.base_url}{path}",
            headers={"Content-Type": "application/json;charset=UTF-8", **dict(headers or {})},
            json=dict(body),
            timeout=self.timeout,
        )
        status = response.status_code
        response.raise_for_status()
        return response.json(), status

    def _token(self) -> str:
        if self._access_token:
            return self._access_token
        payload, _ = self._post_raw(
            "/oauth2/token",
            {"grant_type": "client_credentials", "appkey": self.app_key, "secretkey": self.secret_key},
        )
        token = payload.get("token")
        if payload.get("return_code") != 0 or not token:
            raise RuntimeError(f"Kiwoom OAuth failed: {payload.get('return_msg', 'unknown error')}")
        self._access_token = str(token)
        return self._access_token

    def query(
        self, path: str, operation_code: str, body: Mapping[str, Any]
    ) -> tuple[dict[str, Any], int]:
        validate_read_only_path("KIWOOM", path)
        return self._post_raw(
            path,
            body,
            {"authorization": f"Bearer {self._token()}", "api-id": operation_code},
        )


_KIS_CLIENT: KisReadOnlyClient | None = None
_KIWOOM_CLIENT: KiwoomReadOnlyClient | None = None


def _kis_client() -> KisReadOnlyClient:
    global _KIS_CLIENT
    if _KIS_CLIENT is None:
        _KIS_CLIENT = KisReadOnlyClient()
    return _KIS_CLIENT


def _kiwoom_client() -> KiwoomReadOnlyClient:
    global _KIWOOM_CLIENT
    if _KIWOOM_CLIENT is None:
        _KIWOOM_CLIENT = KiwoomReadOnlyClient()
    return _KIWOOM_CLIENT


def _kis_request(spec: ProbeSpec, ticker: str, current: datetime) -> tuple[dict[str, Any], int]:
    client = _kis_client()
    hhmmss = current.strftime("%H%M%S")
    if spec.probe_id == "kis_stock_price":
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker}
    elif spec.probe_id == "kis_stock_minute":
        params = {
            "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker,
            "FID_INPUT_HOUR_1": hhmmss, "FID_PW_DATA_INCU_YN": "Y", "FID_ETC_CLS_CODE": "",
        }
    elif spec.probe_id == "kis_index_minute_kospi":
        params = {
            "FID_COND_MRKT_DIV_CODE": "U", "FID_ETC_CLS_CODE": "0",
            "FID_INPUT_ISCD": "0001", "FID_INPUT_HOUR_1": "60",
            "FID_PW_DATA_INCU_YN": "Y",
        }
    elif spec.probe_id == "kis_program_summary_kospi":
        params = {
            "FID_COND_MRKT_DIV_CODE": "J", "FID_MRKT_CLS_CODE": "K",
            "FID_SCTN_CLS_CODE": "", "FID_INPUT_ISCD": "",
            "FID_COND_MRKT_DIV_CODE1": "", "FID_INPUT_HOUR_1": "",
        }
    elif spec.probe_id == "kis_investor_stock":
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker}
    elif spec.probe_id == "kis_futures_board":
        params = {
            "FID_COND_MRKT_DIV_CODE": "F", "FID_COND_SCR_DIV_CODE": "20503",
            "FID_COND_MRKT_CLS_CODE": "MKI",
        }
    else:
        raise ValueError(f"No KIS request builder for {spec.probe_id}")
    assert spec.path is not None
    return client.get(spec.path, spec.operation_code, params)


def _to_number(value: Any) -> float:
    try:
        return float(str(value or "0").replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _kis_active_futures_request(current: datetime) -> tuple[dict[str, Any], int]:
    client = _kis_client()
    board_payload, _ = client.get(
        "/uapi/domestic-futureoption/v1/quotations/display-board-futures",
        "FHPIF05030200",
        {
            "FID_COND_MRKT_DIV_CODE": "F", "FID_COND_SCR_DIV_CODE": "20503",
            "FID_COND_MRKT_CLS_CODE": "MKI",
        },
    )
    rows = board_payload.get("output") or []
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("KIS futures board did not return rows")
    active = max(rows, key=lambda row: _to_number(row.get("acml_vol")))
    futures_code = str(active.get("futs_shrn_iscd") or "").strip()
    if not futures_code:
        raise RuntimeError("KIS futures board did not include futs_shrn_iscd")
    return client.get(
        "/uapi/domestic-futureoption/v1/quotations/inquire-time-fuopchartprice",
        "FHKIF03020200",
        {
            "FID_COND_MRKT_DIV_CODE": "F", "FID_INPUT_ISCD": futures_code,
            "FID_HOUR_CLS_CODE": "60", "FID_PW_DATA_INCU_YN": "Y",
            "FID_FAKE_TICK_INCU_YN": "N", "FID_INPUT_DATE_1": current.strftime("%Y%m%d"),
            "FID_INPUT_HOUR_1": current.strftime("%H%M%S"),
        },
    )


def _kiwoom_request(spec: ProbeSpec, ticker: str, current: datetime) -> tuple[dict[str, Any], int]:
    client = _kiwoom_client()
    if spec.probe_id == "kiwoom_stock_minute":
        body = {"stk_cd": ticker, "tic_scope": "1", "upd_stkpc_tp": "1"}
    elif spec.probe_id == "kiwoom_program_basis_current_session":
        body = {
            "date": current.strftime("%Y%m%d"), "amt_qty_tp": "1",
            "mrkt_tp": "P00101", "min_tic_tp": "1", "stex_tp": "1",
        }
    else:
        raise ValueError(f"No Kiwoom request builder for {spec.probe_id}")
    assert spec.path is not None
    return client.query(spec.path, spec.operation_code, body)


def _provider_status(provider: str, payload: Mapping[str, Any]) -> tuple[Any, str | None, bool]:
    if provider == "KIS":
        code = payload.get("rt_cd")
        message = payload.get("msg1")
        return code, message, code in (None, "0")
    code = payload.get("return_code")
    message = payload.get("return_msg")
    return code, message, code in (None, 0, "0")


def execute_probe(
    probe_id: str,
    *,
    ticker: str = "005930",
    force_session_probe: bool = False,
    current: datetime | None = None,
    request_override: Callable[[ProbeSpec, str, datetime], tuple[dict[str, Any], int]] | None = None,
    payload_consumer: Callable[[Mapping[str, Any]], None] | None = None,
) -> ProbeResult:
    if probe_id not in PROBE_SPECS:
        raise KeyError(f"Unknown probe: {probe_id}")
    spec = PROBE_SPECS[probe_id]
    started = current or now_kst()
    run_id = str(uuid.uuid4())
    common = dict(
        run_id=run_id, probe_id=probe_id, provider=spec.provider, transport=spec.transport,
        started_at_kst=iso_kst(started),
        endpoint=spec.path, operation_code=spec.operation_code,
    )
    if not spec.executable:
        pending = "PENDING_MARKET_SESSION" if spec.market_session_required else "UNVERIFIED"
        return ProbeResult(
            **common, completed_at_kst=iso_kst(started),
            execution_status="SKIPPED", verification_status=pending,
            notes=[spec.notes or "Probe is not executable until its request contract is verified."],
        )
    if spec.market_session_required and started.weekday() >= 5 and not force_session_probe:
        return ProbeResult(
            **common, completed_at_kst=iso_kst(started),
            execution_status="SKIPPED", verification_status="PENDING_MARKET_SESSION",
            notes=["Weekend session probe was not called. Use --force-session-probe only for diagnostics."],
        )
    try:
        if request_override:
            payload, http_status = request_override(spec, ticker, started)
        elif probe_id == "kis_futures_minute_active":
            payload, http_status = _kis_active_futures_request(started)
        elif spec.provider == "KIS":
            payload, http_status = _kis_request(spec, ticker, started)
        else:
            payload, http_status = _kiwoom_request(spec, ticker, started)
        if not isinstance(payload, Mapping):
            raise TypeError("Provider response must be a JSON object")
        if payload_consumer is not None:
            payload_consumer(payload)
        provider_code, provider_message, provider_ok = _provider_status(spec.provider, payload)
        rows = output_rows(payload, spec.response_container)
        source_trade_dates, source_times = extract_source_dates_and_times(rows)
        observed_fields = sorted({str(key) for row in rows for key in row.keys()})
        missing = [field for field in spec.expected_fields if field not in observed_fields]
        if not provider_ok:
            execution_status = "PROVIDER_ERROR"
            verification_status = "UNVERIFIED"
        elif not rows:
            execution_status = "EMPTY_OUTPUT"
            verification_status = "PARTIAL"
        elif missing:
            execution_status = "SCHEMA_MISMATCH"
            verification_status = "PARTIAL"
        else:
            execution_status = "SUCCESS"
            verification_status = "PARTIAL"
        return ProbeResult(
            **common,
            completed_at_kst=iso_kst(),
            execution_status=execution_status,
            verification_status=verification_status,
            http_status=http_status,
            provider_code=provider_code,
            provider_message=provider_message,
            response_top_level_keys=sorted(str(key) for key in payload.keys()),
            output_row_count=len(rows),
            source_trade_dates=source_trade_dates,
            source_times=source_times,
            observed_fields=observed_fields,
            missing_expected_fields=missing,
            schema=infer_schema(payload),
            sanitized_sample=redact_sensitive(payload),
            notes=[spec.notes] if spec.notes else [],
        )
    except Exception as error:  # Persist diagnostics without leaking request credentials.
        return ProbeResult(
            **common,
            completed_at_kst=iso_kst(),
            execution_status="ERROR",
            verification_status="UNVERIFIED",
            error_type=type(error).__name__,
            error_message=redact_sensitive(str(error)),
            notes=[spec.notes] if spec.notes else [],
        )


def init_probe_db(db_path: Path | str = DEFAULT_DB_PATH) -> Path:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS api_probe_runs (
                run_id TEXT PRIMARY KEY,
                probe_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                transport TEXT NOT NULL,
                started_at_kst TEXT NOT NULL,
                completed_at_kst TEXT NOT NULL,
                execution_status TEXT NOT NULL,
                verification_status TEXT NOT NULL,
                endpoint TEXT,
                operation_code TEXT NOT NULL,
                http_status INTEGER,
                provider_code TEXT,
                provider_message TEXT,
                output_row_count INTEGER,
                source_trade_dates_json TEXT NOT NULL DEFAULT '[]',
                source_times_json TEXT NOT NULL DEFAULT '[]',
                observed_fields_json TEXT NOT NULL,
                missing_expected_fields_json TEXT NOT NULL,
                schema_json TEXT NOT NULL,
                sanitized_sample_json TEXT,
                error_type TEXT,
                error_message TEXT,
                notes_json TEXT NOT NULL
            )
            """
        )
        existing_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(api_probe_runs)").fetchall()
        }
        for column in ("source_trade_dates_json", "source_times_json"):
            if column not in existing_columns:
                conn.execute(
                    f"ALTER TABLE api_probe_runs ADD COLUMN {column} TEXT NOT NULL DEFAULT '[]'"
                )
        conn.commit()
    finally:
        conn.close()
    return path


def save_probe_result(result: ProbeResult, db_path: Path | str = DEFAULT_DB_PATH) -> None:
    path = init_probe_db(db_path)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO api_probe_runs (
                run_id, probe_id, provider, transport, started_at_kst, completed_at_kst,
                execution_status, verification_status, endpoint, operation_code, http_status,
                provider_code, provider_message, output_row_count, source_trade_dates_json,
                source_times_json, observed_fields_json, missing_expected_fields_json,
                schema_json, sanitized_sample_json,
                error_type, error_message, notes_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.run_id, result.probe_id, result.provider, result.transport,
                result.started_at_kst, result.completed_at_kst, result.execution_status,
                result.verification_status, result.endpoint, result.operation_code,
                result.http_status, None if result.provider_code is None else str(result.provider_code),
                result.provider_message, result.output_row_count,
                json.dumps(result.source_trade_dates, ensure_ascii=False),
                json.dumps(result.source_times, ensure_ascii=False),
                json.dumps(result.observed_fields, ensure_ascii=False),
                json.dumps(result.missing_expected_fields, ensure_ascii=False),
                json.dumps(result.schema, ensure_ascii=False),
                json.dumps(result.sanitized_sample, ensure_ascii=False),
                result.error_type, result.error_message,
                json.dumps(result.notes, ensure_ascii=False),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def save_json_report(results: list[ProbeResult], output_dir: Path | str = DEFAULT_OUTPUT_DIR) -> Path:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = now_kst().strftime("%Y%m%d_%H%M%S")
    path = directory / f"probe_report_{stamp}.json"
    payload = {
        "generated_at_kst": iso_kst(),
        "contains_credentials": False,
        "results": [asdict(result) for result in results],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def registry_rows() -> list[dict[str, Any]]:
    return [asdict(PROBE_SPECS[key]) for key in sorted(PROBE_SPECS)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="list registered probes without API calls")
    parser.add_argument("--probe", action="append", choices=sorted(PROBE_SPECS))
    parser.add_argument("--all-executable", action="store_true", help="run every executable probe")
    parser.add_argument("--provider", choices=["KIS", "KIWOOM"], help="filter --all-executable")
    parser.add_argument("--ticker", default="005930")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--force-session-probe", action="store_true")
    args = parser.parse_args(argv)

    if args.list:
        print(json.dumps(registry_rows(), ensure_ascii=False, indent=2))
        return 0

    probe_ids = list(args.probe or [])
    if args.all_executable:
        probe_ids.extend(
            spec.probe_id for spec in PROBE_SPECS.values()
            if spec.executable and (not args.provider or spec.provider == args.provider)
        )
    probe_ids = list(dict.fromkeys(probe_ids))
    if not probe_ids:
        parser.error("choose --list, --probe, or --all-executable")

    results = []
    for probe_id in probe_ids:
        result = execute_probe(
            probe_id, ticker=str(args.ticker).zfill(6),
            force_session_probe=args.force_session_probe,
        )
        save_probe_result(result, args.db_path)
        results.append(result)
        print(
            f"[{result.provider}] {result.probe_id}: {result.execution_status} / "
            f"{result.verification_status} rows={result.output_row_count}"
        )
    report_path = save_json_report(results, args.output_dir)
    print(f"Sanitized report: {report_path}")
    return 1 if any(result.execution_status == "ERROR" for result in results) else 0


if __name__ == "__main__":
    sys.exit(main())
