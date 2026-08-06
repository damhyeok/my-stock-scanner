"""Renderer for the market-betting engine tab inside the existing Streamlit app."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from .storage import list_decision_runs, load_decision_run


_DECISION_KO = {
    "ALLOW": "진입 허용",
    "SELECTIVE": "선별 진입",
    "BLOCK": "신규 진입 차단",
    "NOT_EVALUABLE": "판단 불가",
    "ALLOWED": "신규 진입 허용",
    "BLOCKED": "신규 진입 차단",
    "HOLD": "보유 유지",
    "REDUCE": "비중 축소",
    "EXIT": "청산 검토",
    "LEADING": "주도",
    "EMERGING": "유입 초기",
    "NEUTRAL": "중립",
    "FADING": "약화",
    "AVOID": "회피",
}

_AXIS_KO = {
    "price_action": "가격 흐름",
    "actual_flow": "프로그램 실제 수급",
    "futures": "선물 흐름",
    "activity": "거래 활동성",
    "relative_strength": "시장 대비 종목 강도",
    "sector_participation": "섹터 참여도",
    "sector_relative_strength": "시장 대비 섹터 강도",
    "sector_activity": "섹터 거래 활동성",
    "coverage": "데이터 포착 범위",
    "concentration": "특정 종목 쏠림",
    "stock_thesis": "보유 투자 논리",
}

_CODE_KO = {
    "PRICE_VWAP_STRUCTURE": "현재 값이 분봉 기반 VWAP 기준선 위인지와 최근 가격 방향",
    "PRICE_STRUCTURE_UNAVAILABLE": "VWAP 및 최근 가격 방향 자료",
    "CLV_FLOW_PROXY_POSITIVE": "분봉 종가 위치로 추정한 매수 우위 — 실제 순매수 금액은 아님",
    "CLV_FLOW_PROXY_NEGATIVE": "분봉 종가 위치로 추정한 매도 우위 — 실제 순매수 금액은 아님",
    "CLV_FLOW_PROXY_NEUTRAL": "분봉 종가 위치로 본 매수·매도 우위가 뚜렷하지 않음",
    "CLV_FLOW_PROXY_UNAVAILABLE": "분봉 종가 위치 기반 매수·매도 우위 추정 자료",
    "PROGRAM_NON_ARBITRAGE_ACTUAL_FLOW": "프로그램 비차익 순매수 금액의 시간대별 변화",
    "PROGRAM_FLOW_SLOPE_UNAVAILABLE": "프로그램 수급의 방향을 계산할 연속 자료",
    "FUTURES_PRICE_CONFIRMATION": "KOSPI200 선물이 당일 평균가격 위인지와 최근 방향",
    "FUTURES_PRICE_CONFIRMATION_UNAVAILABLE": "선물 가격 확인 자료",
    "RELATIVE_SHORT_RETURN": "해당 종목의 최근 수익률이 시장보다 강한지",
    "RELATIVE_RETURN_UNAVAILABLE": "시장 대비 종목 수익률 자료",
    "RISING_ACTIVITY_CONFIRMS_ADVANCE": "거래 활동 증가가 가격 상승과 함께 나타남",
    "RISING_ACTIVITY_CONFIRMS_DECLINE": "거래 활동 증가가 가격 하락과 함께 나타남",
    "ACTIVITY_NOT_EXPANDING": "최근 거래 활동이 뚜렷하게 증가하지 않음",
    "ACTIVITY_CONFIRMATION_UNAVAILABLE": "최근 거래 활동 증가 여부 자료",
    "SECTOR_ABOVE_VWAP_RATIO": "추적 종목 중 각 종목의 분봉 기반 VWAP 위에 있는 종목 비율",
    "SECTOR_ABOVE_VWAP_RATIO_UNAVAILABLE": "VWAP 위에 있는 섹터 종목 비율",
    "SECTOR_OUTPERFORMING_RATIO": "추적 종목 중 시장보다 강한 종목 비율",
    "SECTOR_OUTPERFORMING_RATIO_UNAVAILABLE": "시장보다 강한 섹터 종목 비율",
    "SECTOR_ACTIVITY_CONFIRMING_RATIO": "거래 활동 증가와 가격 상승이 함께 나온 종목 비율",
    "SECTOR_ACTIVITY_CONFIRMING_RATIO_UNAVAILABLE": "거래 활동 증가가 확인된 섹터 종목 비율",
    "SECTOR_SINGLE_NAME_CONCENTRATION": "섹터 거래대금이 한 종목에 과도하게 집중됨",
    "SECTOR_MEMBER_COVERAGE_LOW": "분석에 포함된 섹터 종목 수가 부족함",
    "SECTOR_TURNOVER_COVERAGE_LOW": "포착한 섹터 거래대금 비율이 부족함",
    "SECTOR_UNIVERSE_INCOMPLETE": "섹터 전체가 아닌 추적 표본만 분석됨",
    "EXISTING_THESIS_NOT_SUPPLIED": "보유 종목의 투자 논리가 입력되지 않음",
    "EXISTING_THESIS_INVALID": "입력한 보유 투자 논리가 더 이상 유효하지 않음",
}

_STATE_KO = {
    "WATCH": "관찰 중",
    "SETUP": "진입 조건 대기",
    "TRIGGERED": "진입 조건 충족",
    "EXTENDED": "과열·추격 금지",
    "FAILED": "진입 신호 실패",
    "INVALIDATED": "상승 논리 무효",
    "NOT_EVALUABLE": "판단 자료 부족",
    "NONE": "해당 없음",
    "BREAKOUT": "돌파형",
    "PULLBACK": "눌림목형",
}

_REASON_KO = {
    "UPPER_GATE_NOT_OPEN": "시장 또는 섹터 조건이 열리지 않아 관찰만 합니다.",
    "RISK_REWARD_EXTENDED": "현재 가격에서 진입하면 손절 폭에 비해 기대수익이 작아 추격하지 않습니다.",
    "REQUIRED_DATA_NOT_EVALUABLE": "필수 종목 데이터가 부족해 판단을 보류합니다.",
    "SETUP_READY_TRIGGER_PENDING": "진입 후보 조건은 갖췄지만 실제 가격 신호를 기다리는 중입니다.",
    "WATCHING_FOR_SETUP": "아직 진입 후보 조건이 만들어지지 않았습니다.",
    "ALL_TRIGGER_GATES_CONFIRMED": "시장·섹터·종목의 모든 진입 조건이 충족됐습니다.",
    "STRUCTURAL_THESIS_INVALIDATED": "사전에 정한 가격 구조가 무너져 상승 논리가 무효화됐습니다.",
    "POST_TRIGGER_REACTION_FAILED": "진입 신호 이후 기대한 가격 반응이 나오지 않았습니다.",
    "ILLEGAL_TRANSITION_REJECTED": "허용되지 않은 상태 변경을 안전하게 거부했습니다.",
    "NO_ACTIVE_TRIGGER": "아직 실제 진입 신호가 발생하지 않았습니다.",
    "PREVIOUS_TRIGGER_STRUCTURE_MISSING": "이전 진입 신호의 기준 가격 정보가 없습니다.",
    "WAITING_FOR_POST_TRIGGER_BARS": "진입 신호 이후 가격 반응을 더 지켜보는 중입니다.",
    "MULTI_BAR_STRUCTURAL_INVALIDATION_CONFIRMED": "여러 분봉에서 구조적 지지선 이탈이 확인됐습니다.",
    "POST_TRIGGER_FOLLOW_THROUGH_FAILED": "진입 신호 뒤 추가 상승이 이어지지 않았습니다.",
    "TRIGGER_REMAINS_ACTIVE": "진입 신호와 가격 구조가 아직 유효합니다.",
}

_QUALITY_CODE_KO = {
    "OUT_OF_SESSION_ROW_EXCLUDED": "정규장 밖 시간의 데이터 제외",
    "SPECIAL_INDEX_TIME_ROW_EXCLUDED": "지수 응답의 특수 시간 행 제외",
    "NON_TARGET_ROWS_EXCLUDED": "선택 날짜·정규장 범위 밖 데이터 제외",
    "FIELD_SEMANTICS_PARTIAL": "필드 의미·단위 검증 미완료",
    "FIELD_SEMANTICS_UNVERIFIED": "필드 의미 미검증",
    "SOURCE_TRADE_DATE_MISSING": "원본 거래일 없음",
    "SOURCE_TRADE_DATE_MISMATCH": "원본 거래일 불일치",
    "OBSERVATION_STALE": "관측 데이터가 너무 오래됨",
    "ADAPTER_OUTPUT_EMPTY": "API 응답 데이터 없음",
    "COLLECTION_PAYLOAD_UNAVAILABLE": "API 원본 응답 수집 실패",
}

_SEVERITY_KO = {
    "INFO": "참고",
    "WARNING": "경고",
    "BLOCKING": "판단 차단",
    "ERROR": "오류",
}


def decision_label(value: str) -> str:
    return _DECISION_KO.get(value, value)


def state_label(value: Any) -> str:
    text = str(value or "-")
    return _STATE_KO.get(text, text)


def reason_label(value: Any) -> str:
    text = str(value or "-")
    return _REASON_KO.get(text, text.replace("_", " "))


def _percent(value: str) -> str:
    return f"{float(value) * 100:+.2f}%"


def _human_message(code: str, message: Any) -> str:
    text = str(message or "-")
    pair = re.search(r"VWAP distance=([-+0-9.]+), short return=([-+0-9.]+)", text)
    if pair:
        subject = "선물" if code.startswith("FUTURES_") else "가격"
        return f"{subject}의 VWAP 대비 위치 {_percent(pair.group(1))}, 최근 수익률 {_percent(pair.group(2))}"
    proxy = re.search(r"price-derived proxy ratio=([-+0-9.]+)", text)
    if proxy:
        return f"종가 위치 기반 매수·매도 우위 추정치 {_percent(proxy.group(1))} (실제 순매수 금액 아님)"
    ratio = re.search(r"equal-weight member ratio=([-+0-9.]+)", text)
    if ratio:
        return f"추적 종목 기준 {float(ratio.group(1)) * 100:.1f}%"
    activity = re.search(r"activity rate change=([-+0-9.]+), short return=([-+0-9.]+)", text)
    if activity:
        return f"거래 활동 변화 {_percent(activity.group(1))}, 최근 수익률 {_percent(activity.group(2))}"
    relative = re.search(r"asset minus benchmark short return=([-+0-9.]+)", text)
    if relative:
        return f"시장 대비 최근 초과수익률 {_percent(relative.group(1))}"
    program = re.search(r"provider net amount latest=([-+0-9.]+), slope=([-+0-9.]+)", text)
    if program:
        return f"최근 비차익 순매수 값 {float(program.group(1)):,.0f}, 변화 기울기 {float(program.group(2)):,.0f}"
    if code.endswith("_UNAVAILABLE") or "unavailable" in text.lower() or "required" in text.lower():
        return "계산에 필요한 데이터가 부족합니다."
    return text


def normalize_trade_date(value: Any) -> str:
    """Return the engine's ISO trade-date key from dashboard date values."""

    text = str(value).strip()
    compact = text.replace("-", "")
    if len(compact) == 8 and compact.isdigit():
        return f"{compact[:4]}-{compact[4:6]}-{compact[6:]}"
    return text


def selected_session_time(selected_date: Any, selected_session: Any) -> datetime | None:
    """Parse the dashboard session label into a naive KST wall-clock target."""

    normalized = normalize_trade_date(selected_date)
    match = re.search(r"(\d{1,2}):(\d{2})", str(selected_session or ""))
    if not match:
        return None
    try:
        return datetime.fromisoformat(
            f"{normalized}T{int(match.group(1)):02d}:{int(match.group(2)):02d}:00"
        )
    except ValueError:
        return None


def _run_data_time(run: Mapping[str, Any]) -> datetime | None:
    derived = run.get("derived_evidence")
    if isinstance(derived, Mapping):
        bundle = derived.get("bundle")
        if isinstance(bundle, Mapping):
            market = bundle.get("market_features")
            if isinstance(market, Mapping) and market.get("as_of"):
                try:
                    return datetime.fromisoformat(str(market["as_of"])).replace(tzinfo=None)
                except ValueError:
                    pass
    try:
        return datetime.fromisoformat(str(run.get("evaluated_at_kst"))).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def select_run_for_session(
    runs: Sequence[Mapping[str, Any]],
    selected_date: Any,
    selected_session: Any,
) -> Mapping[str, Any] | None:
    """Choose the usable run whose underlying market data is closest to the UI time."""

    if not runs:
        return None
    target = selected_session_time(selected_date, selected_session)
    if target is None:
        return runs[0]

    def distance(run: Mapping[str, Any]) -> float:
        run_time = _run_data_time(run)
        return abs((run_time - target).total_seconds()) if run_time else float("inf")

    nearest_distance = min(distance(run) for run in runs)
    # Never replace a 09:50 view with a valid-but-distant 15:30 result merely
    # because the 09:50-era run was not evaluable.  Prefer a usable run only
    # among records representing essentially the same selected time.
    near = [run for run in runs if distance(run) <= nearest_distance + 5 * 60]
    usable_near = [
        run for run in near
        if not run.get("quality_blocking") and run.get("market_decision") != "NOT_EVALUABLE"
    ]
    return min(usable_near or near, key=distance)


def _stock_identity(view: Mapping[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    run = view.get("run", {})
    derived = run.get("derived_evidence") if isinstance(run, Mapping) else {}
    adaptive = derived.get("adaptive_universe", {}) if isinstance(derived, Mapping) else {}
    rows = adaptive.get("stocks", []) if isinstance(adaptive, Mapping) else []
    names: dict[str, str] = {}
    sectors: dict[str, str] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, Mapping):
            continue
        ticker = str(row.get("ticker", "")).zfill(6)
        names[ticker] = str(row.get("name") or ticker)
        sectors[ticker] = str(row.get("sector") or "기타")
    return names, sectors


def _stock_action(state: str) -> str:
    return {
        "TRIGGERED": "진입가·손절가를 확인하고 진입 검토",
        "SETUP": "후보 종목 — 가격 신호가 나올 때까지 대기",
        "WATCH": "아직 매수 대상 아님 — 관찰",
        "EXTENDED": "이미 많이 올라 추격 매수 금지",
        "FAILED": "신호 실패 — 매수하지 않기",
        "INVALIDATED": "상승 논리 훼손 — 매수하지 않기",
        "NOT_EVALUABLE": "자료 부족 — 판단 보류",
    }.get(state, "관찰")


def contextual_stock_action(state: str, market_decision: str, sector_decision: str) -> str:
    """Apply upper gates before showing a price-setup action to the user."""

    if market_decision == "BLOCK":
        return "시장 신규진입 차단 — 가격 신호가 나와도 매수하지 않기"
    if market_decision == "NOT_EVALUABLE":
        return "시장 판단 자료 부족 — 매수 보류"
    if sector_decision in {"FADING", "AVOID"}:
        return "섹터가 약함 — 가격 신호가 나와도 현재는 매수하지 않기"
    if sector_decision == "NEUTRAL":
        return "섹터 강세 전환을 먼저 기다리기"
    if sector_decision == "NOT_EVALUABLE":
        return "섹터 판단 자료 부족 — 매수 보류"
    return _stock_action(state)


def _price_text(value: Any) -> str:
    try:
        return f"약 {float(value):,.0f}원"
    except (TypeError, ValueError):
        return "계산 불가"


def _setup_trigger_price(setup: Mapping[str, Any]) -> float | None:
    value = setup.get("trigger_price")
    if value is not None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    if setup.get("setup_type") == "BREAKOUT" and setup.get("reference_level") is not None:
        # Backward compatibility for runs saved before trigger_price was added.
        return float(setup["reference_level"]) * 1.001
    return None


def entry_condition_text(setup: Mapping[str, Any], state: str) -> str:
    """Explain the exact observable event required before an entry review."""

    if state == "EXTENDED":
        return "현재는 손익비가 불리합니다. 새 눌림 구조와 가까운 손절선이 다시 만들어질 때까지 기다립니다."
    if state in {"FAILED", "INVALIDATED"}:
        return "현재 상승 구조가 깨졌으므로 새 진입 구조가 다시 생기기 전에는 매수하지 않습니다."
    if state == "NOT_EVALUABLE":
        return "필수 분봉 자료가 확보된 뒤 다시 계산합니다."
    setup_type = str(setup.get("setup_type", "NONE"))
    if setup_type == "BREAKOUT":
        trigger = _price_text(_setup_trigger_price(setup))
        return (
            f"1분봉 종가가 돌파 확인선 {trigger} 이상에서 마감하고, 그 시점의 당일 종목 VWAP 위를 "
            "유지하며, KOSPI 대비 강세와 거래 활동 증가가 함께 확인되어야 합니다."
        )
    if setup_type == "PULLBACK":
        return (
            "1분봉이 직전 1분봉 종가보다 높고 양봉으로 마감하면서, 그 시점의 당일 종목 VWAP "
            "부근 지지를 유지하고 KOSPI 대비 강세·거래 활동 증가가 함께 확인되어야 합니다."
        )
    return "아직 돌파 또는 눌림목 구조가 없어 구체적인 진입 가격을 제시할 수 없습니다."


def invalidation_condition_text(setup: Mapping[str, Any], state: str) -> str:
    invalidation = setup.get("invalidation_price")
    target = setup.get("reward_reference")
    if invalidation is None:
        return "구조적 손절선을 계산하지 못했으므로 진입하지 않습니다."
    text = f"진입 후 1분봉 종가가 구조 무효화선 {_price_text(invalidation)} 아래에서 2개 연속 마감하면 무효입니다."
    if target is not None and state in {"SETUP", "TRIGGERED"}:
        text += f" 시초가부터 목표 참고선 {_price_text(target)} 부근을 크게 넘겨 출발하면 추격하지 않습니다."
    return text


def reference_values_text(
    setup: Mapping[str, Any],
    stock_evidence: Mapping[str, Any],
) -> str:
    features = stock_evidence.get("features", {}) if isinstance(stock_evidence, Mapping) else {}
    vwap = features.get("session_vwap", {}) if isinstance(features, Mapping) else {}
    parts = []
    if features.get("last_close") is not None:
        parts.append(f"현재 {_price_text(features['last_close'])}")
    if isinstance(vwap, Mapping) and vwap.get("value") is not None:
        parts.append(f"당일 종목 VWAP {_price_text(vwap['value'])}")
    if setup.get("setup_type") == "BREAKOUT" and setup.get("reference_level") is not None:
        parts.append(f"저항선 {_price_text(setup['reference_level'])}")
    trigger = _setup_trigger_price(setup)
    if trigger is not None:
        parts.append(f"진입 확인선 {_price_text(trigger)}")
    return " · ".join(parts) if parts else "계산 가능한 참고값 없음"


def _candidate_text(name: str, setup: Mapping[str, Any]) -> str:
    if setup.get("setup_type") == "BREAKOUT":
        return f"{name} — 1분봉 종가 {_price_text(_setup_trigger_price(setup))} 이상 확인 필요"
    if setup.get("setup_type") == "PULLBACK":
        return f"{name} — VWAP 지지 후 1분봉 반등 확인 필요"
    return f"{name} — 새 가격 구조 확인 필요"


def build_sector_action_rows(view: Mapping[str, Any]) -> list[dict[str, str]]:
    """Turn engine states into direct intraday/close-bet instructions."""

    names, stock_sectors = _stock_identity(view)
    stock_rows = list(view.get("stocks", []))
    setups_by_symbol = view.get("setups", {}) if isinstance(view.get("setups"), Mapping) else {}
    market_decision = str(view.get("market", {}).get("decision", "NOT_EVALUABLE"))
    close_decision = str(
        view.get("overnight", {}).get("CLOSE_NEW_ENTRY", {}).get("decision", "NOT_EVALUABLE")
    )
    result = []
    strength_label = {
        "LEADING": "강세 지속",
        "EMERGING": "강세 시작",
        "NEUTRAL": "중립",
        "FADING": "강세 약화",
        "AVOID": "약세·회피",
        "NOT_EVALUABLE": "자료 부족",
    }
    for sector in view.get("sectors", []):
        sector_name = str(sector.get("scope_id", "기타"))
        decision = str(sector.get("decision", "NOT_EVALUABLE"))
        members = [
            row for row in stock_rows
            if stock_sectors.get(str(row.get("symbol", "")).zfill(6)) == sector_name
        ]
        triggered = []
        setups = []
        for row in members:
            symbol = str(row.get("symbol", "")).zfill(6)
            candidate = _candidate_text(
                names.get(symbol, symbol),
                setups_by_symbol.get(symbol, {}),
            )
            if row.get("current_state") == "TRIGGERED":
                triggered.append(candidate)
            elif row.get("current_state") == "SETUP":
                setups.append(candidate)
        if decision in {"FADING", "AVOID"}:
            intraday = "신규매수 피하기"
            close = "종가베팅 피하기"
        elif decision in {"LEADING", "EMERGING"} and market_decision in {"ALLOW", "SELECTIVE"}:
            if triggered:
                intraday = f"진입 검토: {', '.join(triggered[:3])}"
            elif setups:
                intraday = f"후보: {', '.join(setups[:3])}"
            else:
                intraday = "강한 섹터지만 현재 진입 신호 종목 없음"
            if close_decision in {"ALLOWED", "SELECTIVE"} and triggered:
                close = f"종가베팅 검토: {', '.join(triggered[:3])}"
            elif close_decision in {"ALLOWED", "SELECTIVE"} and setups:
                close = f"조건부 후보: {', '.join(setups[:3])}"
            elif close_decision in {"ALLOWED", "SELECTIVE"}:
                close = "현재 종가베팅 후보 없음"
            else:
                close = "시장 종가 조건이 열리지 않음"
        elif decision == "NEUTRAL":
            intraday = "강세 확인 전까지 관찰"
            close = "종가베팅 대기"
        else:
            intraday = "자료 부족 — 판단 보류"
            close = "자료 부족 — 판단 보류"
        result.append(
            {
                "섹터": sector_name,
                "현재 강도": strength_label.get(decision, decision_label(decision)),
                "장중에는": intraday,
                "종가에는": close,
            }
        )
    order = {"LEADING": 0, "EMERGING": 1, "NEUTRAL": 2, "FADING": 3, "AVOID": 4, "NOT_EVALUABLE": 5}
    decision_by_sector = {str(item.get("scope_id")): str(item.get("decision")) for item in view.get("sectors", [])}
    return sorted(result, key=lambda row: order.get(decision_by_sector.get(row["섹터"], ""), 9))


def selection_instruction(view: Mapping[str, Any]) -> str:
    market = str(view.get("market", {}).get("decision", "NOT_EVALUABLE"))
    sector_rows = build_sector_action_rows(view)
    eligible = [row for row in sector_rows if row["현재 강도"] in {"강세 지속", "강세 시작"}]
    if market == "ALLOW":
        prefix = "시장 환경은 신규 진입을 허용합니다."
    elif market == "SELECTIVE":
        prefix = "시장 전체를 사는 장은 아닙니다."
    elif market == "BLOCK":
        return "지금은 신규 진입을 쉬는 구간입니다. 강한 종목이 보여도 새 매수는 피하세요."
    else:
        return "필수 시장 자료가 부족해 지금 매수해도 되는지 판단할 수 없습니다."
    if not eligible:
        return f"{prefix} 현재 강세로 통과한 섹터가 없어 신규매수는 기다리세요."
    details = " / ".join(f"{row['섹터']}: {row['장중에는']}" for row in eligible[:3])
    return f"{prefix} 선별 대상은 강세 섹터뿐입니다. {details}"


_SECTOR_SCORE = {
    "GENUINE": 3,
    "EXPANDING": 2,
    "CANDIDATE": 1,
    "LEADING": 2,
    "EMERGING": 1,
    "NEUTRAL": 0,
    "FADING": -1,
    "AVOID": -2,
}

_SECTOR_STRENGTH_KO = {
    "GENUINE": "진짜 강세",
    "EXPANDING": "강세 확산",
    "CANDIDATE": "강세 후보",
    "LEADING": "강세 지속",
    "EMERGING": "강세 시작",
    "NEUTRAL": "중립",
    "FADING": "강세 약화",
    "AVOID": "약세·회피",
    "NOT_EVALUABLE": "자료 부족",
}

_POSITIVE_SECTOR_TIERS = {"GENUINE", "EXPANDING", "CANDIDATE", "LEADING", "EMERGING"}


def _sector_summary(detail: Mapping[str, Any], sector: str) -> Mapping[str, Any]:
    run = detail.get("run", {})
    derived = run.get("derived_evidence") if isinstance(run, Mapping) else None
    bundle = derived.get("bundle") if isinstance(derived, Mapping) else None
    sectors = bundle.get("sectors") if isinstance(bundle, Mapping) else None
    sector_data = sectors.get(sector) if isinstance(sectors, Mapping) else None
    summary = sector_data.get("summary") if isinstance(sector_data, Mapping) else None
    return summary if isinstance(summary, Mapping) else {}


def _sector_member_groups(detail: Mapping[str, Any], sector: str) -> dict[str, list[str]]:
    """Return stock names satisfying each sector-strength condition."""

    result = {"above_vwap": [], "outperforming": [], "activity_confirming": []}
    run = detail.get("run", {})
    derived = run.get("derived_evidence") if isinstance(run, Mapping) else None
    bundle = derived.get("bundle") if isinstance(derived, Mapping) else None
    sectors = bundle.get("sectors") if isinstance(bundle, Mapping) else None
    stocks = bundle.get("stocks") if isinstance(bundle, Mapping) else None
    sector_data = sectors.get(sector) if isinstance(sectors, Mapping) else None
    observed = sector_data.get("observed_members", []) if isinstance(sector_data, Mapping) else []
    adaptive = derived.get("adaptive_universe") if isinstance(derived, Mapping) else None
    universe_rows = adaptive.get("stocks", []) if isinstance(adaptive, Mapping) else []
    names = {
        str(row.get("ticker", "")).zfill(6): str(
            row.get("name") or row.get("stock_name") or row.get("ticker") or ""
        )
        for row in universe_rows
        if isinstance(row, Mapping)
    }

    def value(container: Any, *path: str) -> float | None:
        current = container
        for key in path:
            if not isinstance(current, Mapping):
                return None
            current = current.get(key)
        try:
            return float(current) if current is not None else None
        except (TypeError, ValueError):
            return None

    for raw_symbol in observed:
        symbol = str(raw_symbol).zfill(6)
        stock = stocks.get(symbol) if isinstance(stocks, Mapping) else None
        if not isinstance(stock, Mapping):
            continue
        display_name = names.get(symbol) or symbol
        vwap_distance = value(stock, "features", "vwap_distance_ratio", "value")
        relative_return = value(stock, "relative", "relative_short_return", "value")
        activity = value(stock, "features", "activity_acceleration", "value")
        short_return = value(stock, "features", "short_return", "value")
        if vwap_distance is not None and vwap_distance > 0:
            result["above_vwap"].append(display_name)
        if relative_return is not None and relative_return > 0:
            result["outperforming"].append(display_name)
        if activity is not None and activity > 0 and short_return is not None and short_return > 0:
            result["activity_confirming"].append(display_name)
    return result


def _sector_breadth_tier(
    judgment: Mapping[str, Any], summary: Mapping[str, Any]
) -> str:
    """Turn the three breadth ratios into an early/expanding/genuine tier."""

    try:
        member_count = int(summary.get("member_count") or 0)
        ratios = [
            float(summary[name])
            for name in (
                "above_vwap_ratio",
                "outperforming_ratio",
                "activity_confirming_ratio",
            )
            if summary.get(name) is not None
        ]
    except (TypeError, ValueError):
        ratios = []
        member_count = 0
    if member_count >= 4 and len(ratios) == 3:
        supporting_counts = [round(value * member_count) for value in ratios]
        if min(supporting_counts) >= 3:
            minimum_ratio = min(ratios)
            if minimum_ratio >= 0.60:
                return "GENUINE"
            if minimum_ratio >= 0.50:
                return "EXPANDING"
            if minimum_ratio >= 0.40:
                return "CANDIDATE"
    return str(judgment.get("decision", "NOT_EVALUABLE"))


def _sector_reason_text(
    judgment: Mapping[str, Any],
    summary: Mapping[str, Any] | None = None,
    tier: str | None = None,
) -> str:
    """Explain a sector judgment in plain Korean using its saved evidence."""

    explanations = []
    summary = summary or {}
    member_count = int(summary.get("member_count") or 0)
    summary_metrics = (
        ("above_vwap_ratio", "각 종목의 VWAP 위"),
        ("outperforming_ratio", "같은 시간 코스피보다 강함"),
        ("activity_confirming_ratio", "거래 증가와 가격 상승이 함께 나타남"),
    )
    for name, label in summary_metrics:
        value = summary.get(name)
        if value is not None and member_count:
            ratio = float(value)
            explanations.append(
                f"{round(ratio * member_count)}/{member_count}종목({ratio * 100:.0f}%)이 {label}"
            )

    all_evidence = []
    for field in ("evidence", "warnings", "counter_evidence", "blockers"):
        all_evidence.extend(judgment.get(field, []))
    for item in all_evidence if not explanations else []:
        code = str(item.get("code", ""))
        message = str(item.get("message", ""))
        ratio = re.search(r"equal-weight member ratio=([-+0-9.]+)", message)
        percentage = f"{float(ratio.group(1)) * 100:.0f}%" if ratio else None
        if code == "SECTOR_ABOVE_VWAP_RATIO":
            explanations.append(
                f"추적 종목의 {percentage}가 당일 평균 매매가격(VWAP) 위에 있음"
                if percentage else "섹터 종목 다수가 당일 평균 매매가격(VWAP) 위에 있음"
            )
        elif code == "SECTOR_OUTPERFORMING_RATIO":
            explanations.append(
                f"추적 종목의 {percentage}가 같은 시간 코스피보다 강함"
                if percentage else "섹터 종목 다수가 같은 시간 코스피보다 강함"
            )
        elif code == "SECTOR_ACTIVITY_CONFIRMING_RATIO":
            explanations.append(
                f"추적 종목의 {percentage}에서 거래 증가와 가격 상승이 함께 나타남"
                if percentage else "섹터 종목 다수에서 거래 증가와 가격 상승이 함께 나타남"
            )

    decision = tier or str(judgment.get("decision", ""))
    if decision == "GENUINE":
        explanations.append("세 조건 모두 60% 이상으로 섹터 전반에 힘이 넓게 퍼진 진짜 강세")
    elif decision == "EXPANDING":
        explanations.append("세 조건 모두 50% 이상으로 강세가 여러 종목으로 확산 중")
    elif decision == "CANDIDATE":
        explanations.append("세 조건 모두 40% 이상이고 최소 3종목이 참여해 초기 강세 후보로 포착")
    elif decision == "LEADING":
        explanations.append("앞선 분석에서도 강세여서 흐름이 이어지는 중")
    elif decision == "EMERGING":
        explanations.append("이번 분석 시각에 강세 조건을 새로 통과")
    return " · ".join(explanations) or "저장된 세부 근거가 없어 강약 상태만 표시"


def build_sector_strength_history(
    db_path: str,
    selected_date: str,
    selected_session: str,
) -> tuple[list[dict[str, Any]], Mapping[str, Any] | None]:
    """Build one clean sector-state snapshot per underlying market-data minute."""

    runs = list_decision_runs(
        db_path,
        target_trade_date=normalize_trade_date(selected_date),
        limit=100,
    )
    selected_run = select_run_for_session(runs, selected_date, selected_session)
    if selected_run is None:
        return [], None
    cutoff = _run_data_time(selected_run)
    grouped: dict[str, Mapping[str, Any]] = {}

    def usable(run: Mapping[str, Any]) -> bool:
        return not run.get("quality_blocking") and run.get("market_decision") != "NOT_EVALUABLE"

    for run in runs:
        data_time = _run_data_time(run)
        if data_time is None or (cutoff is not None and data_time > cutoff):
            continue
        key = data_time.strftime("%Y-%m-%dT%H:%M")
        existing = grouped.get(key)
        if existing is None or (usable(run) and not usable(existing)):
            grouped[key] = run

    history = []
    for key, run in sorted(grouped.items()):
        detail = load_decision_run(db_path, str(run["run_id"]))
        if detail is None:
            continue
        data_time = _run_data_time(run)
        for judgment in detail.get("judgments", []):
            if judgment.get("scope_type") != "SECTOR":
                continue
            sector = str(judgment.get("scope_id", "기타"))
            summary = _sector_summary(detail, sector)
            member_groups = _sector_member_groups(detail, sector)
            decision = _sector_breadth_tier(judgment, summary)
            history.append(
                {
                    "time": data_time,
                    "time_label": data_time.strftime("%H:%M") if data_time else key[-5:],
                    "sector": sector,
                    "decision": decision,
                    "status": _SECTOR_STRENGTH_KO.get(decision, decision),
                    "score": _SECTOR_SCORE.get(decision),
                    "reason": _sector_reason_text(judgment, summary, decision),
                    "vwap_members": ", ".join(member_groups["above_vwap"]),
                    "outperforming_members": ", ".join(member_groups["outperforming"]),
                    "activity_members": ", ".join(member_groups["activity_confirming"]),
                }
            )
    return history, selected_run


def build_daily_sector_strength_history(
    db_path: str,
    selected_date: str,
    selected_session: str,
    *,
    recent_days: int = 10,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Build one same-session sector snapshot for each recent trade date."""

    target_date = normalize_trade_date(selected_date)
    runs = [
        run
        for run in list_decision_runs(db_path, limit=1000)
        if str(run.get("target_trade_date", "")) <= target_date
    ]
    dates = sorted({str(run.get("target_trade_date")) for run in runs if run.get("target_trade_date")})
    dates = dates[-max(1, min(int(recent_days), 30)):]
    history = []
    for trade_date in dates:
        date_runs = [run for run in runs if str(run.get("target_trade_date")) == trade_date]
        selected_run = select_run_for_session(date_runs, trade_date, selected_session)
        if selected_run is None:
            continue
        detail = load_decision_run(db_path, str(selected_run["run_id"]))
        if detail is None:
            continue
        data_time = _run_data_time(selected_run)
        for judgment in detail.get("judgments", []):
            if judgment.get("scope_type") != "SECTOR":
                continue
            sector = str(judgment.get("scope_id", "기타"))
            summary = _sector_summary(detail, sector)
            member_groups = _sector_member_groups(detail, sector)
            decision = _sector_breadth_tier(judgment, summary)
            history.append(
                {
                    "date": trade_date,
                    "date_label": trade_date[5:].replace("-", "/"),
                    "data_time_label": data_time.strftime("%H:%M") if data_time else "-",
                    "sector": sector,
                    "decision": decision,
                    "status": _SECTOR_STRENGTH_KO.get(decision, decision),
                    "score": _SECTOR_SCORE.get(decision),
                    "reason": _sector_reason_text(judgment, summary, decision),
                    "vwap_members": ", ".join(member_groups["above_vwap"]),
                    "outperforming_members": ", ".join(member_groups["outperforming"]),
                    "activity_members": ", ".join(member_groups["activity_confirming"]),
                }
            )
    return history, dates


def render_sector_strength_flow_tab(
    st,
    *,
    db_path: str,
    selected_date: str,
    selected_session: str,
) -> None:
    """Render sector strength from the same decisions used by market betting."""

    import altair as alt
    import pandas as pd

    st.header(f"🧭 오늘 섹터 강약 흐름 ({selected_date} · {selected_session})")
    st.caption(
        "장중·오버나이트 분석과 동일한 기준으로 섹터를 판정합니다. "
        "초록색은 강세, 회색은 중립, 붉은색은 약세입니다."
    )
    with st.expander("📖 강세 조건이란? 쉽게 보기", expanded=False):
        st.markdown(
            """
한 종목만 잠깐 오르는 것이 아니라 **그 섹터의 여러 종목이 함께, 시장보다 강하게, 거래를 동반해 오르는지** 확인합니다.

**1. 섹터 종목들이 자기 평균 매수가격보다 위에 있어야 합니다.**
각 종목의 현재가가 **선택 시각까지 거래량을 반영한 평균 거래가격(VWAP)** 위에 있는지 봅니다. VWAP은 코스피나 섹터 평균가격이 아니라 종목마다 따로 계산됩니다. 현재가가 그 위라면 오늘 거래가 많이 이뤄진 중심 가격보다 잘 버티는 상태라는 뜻입니다.

**2. 코스피가 올라서 덩달아 오른 것보다 더 강해야 합니다.**
예를 들어 코스피가 1% 오를 때 섹터 종목도 1% 정도만 올랐다면 그 섹터만 특별히 강한 것은 아닙니다. 여러 종목이 같은 시간의 코스피보다 더 강해야 섹터 고유의 힘이 있다고 봅니다.

**3. 가격 상승에 실제 거래 증가가 따라와야 합니다.**
최근 거래 활동이 늘면서 가격도 함께 오르는 종목이 몇 개인지 봅니다. 거래 없이 가격만 살짝 움직인 경우보다 실제 참여가 커지는 움직임을 찾기 위한 조건입니다.

**강세는 한 번에 잘라 판단하지 않고 세 단계로 넓게 알려드립니다.**
- **강세 후보:** 세 조건이 각각 40% 이상이고, 조건마다 최소 3종목이 참여한 초기 움직임
- **강세 확산:** 세 조건이 각각 50% 이상으로 여러 종목에 힘이 퍼지는 상태
- **진짜 강세:** 세 조건이 각각 60% 이상으로 섹터 전반의 동반 강세가 확인된 상태

최소 4종목 이상이 관측되어야 판정하며, 한 종목의 급등만으로는 강세가 되지 않습니다. **강세 후보는 일찍 알려주는 관심 신호이고, 실제 진입 검토는 강세 확산부터** 가능합니다. 추적 종목은 해당 섹터의 모든 상장 종목이 아니라 현재 분석 대상으로 선정된 주요 종목입니다.
            """
        )
    history, selected_run = build_sector_strength_history(
        db_path, selected_date, selected_session
    )
    if not history or selected_run is None:
        st.info("선택한 날짜·시간까지 저장된 섹터 강약 분석이 없습니다.")
        return

    frame = pd.DataFrame(history).dropna(subset=["score"])
    if frame.empty:
        st.info("섹터 상태를 그래프로 표시할 수 있는 분석 기록이 없습니다.")
        return
    current_time = frame["time"].max()
    current = frame[frame["time"] == current_time].copy()
    current = current.sort_values(["score", "sector"], ascending=[False, True])
    genuine = current[current["decision"] == "GENUINE"]["sector"].tolist()
    expanding = current[current["decision"] == "EXPANDING"]["sector"].tolist()
    candidates = current[current["decision"] == "CANDIDATE"]["sector"].tolist()
    legacy_strong = current[current["decision"].isin(["LEADING", "EMERGING"])]["sector"].tolist()
    weak = current[current["decision"].isin(["FADING", "AVOID"])]["sector"].tolist()
    if genuine:
        st.success(f"🔥 현재 진짜 강세: {', '.join(genuine)}")
    if expanding:
        st.info(f"📈 강세가 확산 중인 섹터: {', '.join(expanding)}")
    if candidates:
        st.info(f"👀 초기 강세 후보(아직 진입 확정 아님): {', '.join(candidates)}")
    if legacy_strong:
        st.success(f"현재 강한 섹터: {', '.join(legacy_strong)}")
    if not (genuine or expanding or candidates or legacy_strong):
        st.warning("현재 강세 후보 이상의 조건을 통과한 섹터가 없습니다.")
    if weak:
        st.caption(f"현재 약하거나 약화 중인 섹터: {', '.join(weak)}")

    color_domain = [
        "진짜 강세", "강세 확산", "강세 후보", "강세 지속", "강세 시작",
        "중립", "강세 약화", "약세·회피", "자료 부족", "미추적",
    ]
    color_range = [
        "#07523b", "#19945f", "#8bcf9b", "#0b6e4f", "#55a868",
        "#b8b8b8", "#e07a5f", "#b23a48", "#d9d9d9", "#f2f2f2",
    ]
    st.subheader(f"{current_time:%H:%M} 현재 섹터 강약")
    current_chart = (
        alt.Chart(current)
        .mark_bar(cornerRadiusEnd=5)
        .encode(
            x=alt.X(
                "score:Q",
                title="약세  ←  섹터 강도  →  강세",
                scale=alt.Scale(domain=[-2.2, 3.2]),
                axis=alt.Axis(values=[-2, -1, 0, 1, 2, 3], labels=False),
            ),
            y=alt.Y("sector:N", title=None, sort="-x"),
            color=alt.Color(
                "status:N",
                title="판정",
                scale=alt.Scale(domain=color_domain, range=color_range),
            ),
            tooltip=[
                alt.Tooltip("sector:N", title="섹터"),
                alt.Tooltip("status:N", title="현재 상태"),
                alt.Tooltip("reason:N", title="판단 이유"),
                alt.Tooltip("time_label:N", title="데이터 시각"),
            ],
        )
        .properties(height=max(260, len(current) * 48))
    )
    st.altair_chart(current_chart, use_container_width=True)

    with st.expander("🔎 조건에 해당한 종목명 확인", expanded=False):
        selected_sector = st.selectbox(
            "확인할 섹터",
            current["sector"].tolist(),
            key=f"sector_condition_members_{selected_date}_{selected_session}",
        )
        selected_row = current[current["sector"] == selected_sector].iloc[0]

        def member_line(raw_names: Any) -> str:
            names = [name.strip() for name in str(raw_names or "").split(",") if name.strip()]
            return f"{len(names)}종목: {', '.join(names)}" if names else "해당 종목 없음"

        st.markdown(
            f"**각 종목의 VWAP 위**  \n{member_line(selected_row['vwap_members'])}\n\n"
            f"**같은 시간 코스피보다 강함**  \n{member_line(selected_row['outperforming_members'])}\n\n"
            f"**거래 증가와 가격 상승이 함께 나타남**  \n{member_line(selected_row['activity_members'])}"
        )
        st.caption(
            "세 목록의 종목은 서로 다를 수 있습니다. 강세 단계는 각 조건의 참여 비율을 "
            "모두 확인해 결정하므로, 한 조건의 종목이 많다고 바로 강세가 되는 것은 아닙니다."
        )

    st.subheader("아침부터 선택 시각까지 강약 변화")
    sector_order = current["sector"].tolist()
    time_order = (
        frame[["time", "time_label"]]
        .drop_duplicates()
        .sort_values("time")["time_label"]
        .tolist()
    )
    heatmap = (
        alt.Chart(frame)
        .mark_rect(cornerRadius=3)
        .encode(
            x=alt.X("time_label:N", title="분석 데이터 시각", sort=time_order),
            y=alt.Y("sector:N", title=None, sort=sector_order),
            color=alt.Color(
                "status:N",
                title="판정",
                scale=alt.Scale(domain=color_domain, range=color_range),
            ),
            tooltip=[
                alt.Tooltip("time_label:N", title="시각"),
                alt.Tooltip("sector:N", title="섹터"),
                alt.Tooltip("status:N", title="상태"),
                alt.Tooltip("reason:N", title="판단 이유"),
            ],
        )
        .properties(height=max(260, len(sector_order) * 48))
    )
    st.altair_chart(heatmap, use_container_width=True)

    st.divider()
    st.subheader("📅 최근 거래일별 섹터 강약 흐름")
    st.caption(
        f"각 거래일의 {selected_session}에 가장 가까운 분석 결과를 한 칸으로 표시합니다. "
        "같은 시간 기준으로 비교하므로 하루 중 서로 다른 시각을 섞지 않습니다."
    )
    recent_days = st.selectbox(
        "최근 몇 거래일을 볼까요?",
        [5, 10, 20],
        index=1,
        key=f"daily_sector_days_{selected_date}_{selected_session}",
    )
    daily_history, daily_dates = build_daily_sector_strength_history(
        db_path,
        selected_date,
        selected_session,
        recent_days=recent_days,
    )
    if not daily_history:
        st.info("최근 거래일별 섹터 분석 기록이 아직 없습니다.")
    else:
        daily_frame = pd.DataFrame(daily_history)
        ranking = (
            daily_frame.groupby("sector", as_index=False)["score"]
            .max()
            .sort_values(["score", "sector"], ascending=[False, True])
        )
        latest_daily_date = max(daily_dates)
        latest_sectors = daily_frame[daily_frame["date"] == latest_daily_date]["sector"].tolist()
        positive_sectors = ranking[ranking["score"] > 0]["sector"].tolist()
        default_sectors = list(dict.fromkeys(latest_sectors + positive_sectors))[:10]
        sector_options = ranking["sector"].tolist()
        selected_sectors = st.multiselect(
            "그래프에 표시할 섹터",
            sector_options,
            default=default_sectors,
            key=f"daily_sector_selection_{selected_date}_{selected_session}",
        )
        if not selected_sectors:
            st.info("그래프에서 확인할 섹터를 하나 이상 선택해 주세요.")
        else:
            lookup = {
                (str(row.date), str(row.sector)): row._asdict()
                for row in daily_frame.itertuples(index=False)
            }
            daily_grid = []
            for trade_date in daily_dates:
                for sector in selected_sectors:
                    row = lookup.get((trade_date, sector))
                    daily_grid.append(
                        row
                        or {
                            "date": trade_date,
                            "date_label": trade_date[5:].replace("-", "/"),
                            "data_time_label": "-",
                            "sector": sector,
                            "decision": "UNTRACKED",
                            "status": "미추적",
                            "score": None,
                            "reason": "이 거래일에는 주요 분석 섹터로 선정되지 않음",
                            "vwap_members": "",
                            "outperforming_members": "",
                            "activity_members": "",
                        }
                    )
            daily_grid_frame = pd.DataFrame(daily_grid)
            date_order = [date[5:].replace("-", "/") for date in daily_dates]
            daily_chart = (
                alt.Chart(daily_grid_frame)
                .mark_rect(cornerRadius=3)
                .encode(
                    x=alt.X("date_label:N", title="거래일", sort=date_order),
                    y=alt.Y("sector:N", title=None, sort=selected_sectors),
                    color=alt.Color(
                        "status:N",
                        title="판정",
                        scale=alt.Scale(domain=color_domain, range=color_range),
                    ),
                    tooltip=[
                        alt.Tooltip("date:N", title="날짜"),
                        alt.Tooltip("data_time_label:N", title="사용한 데이터 시각"),
                        alt.Tooltip("sector:N", title="섹터"),
                        alt.Tooltip("status:N", title="상태"),
                        alt.Tooltip("reason:N", title="판단 이유"),
                    ],
                )
                .properties(height=max(260, len(selected_sectors) * 48))
            )
            st.altair_chart(daily_chart, use_container_width=True)
            st.caption(
                "흰색 ‘미추적’은 약세라는 뜻이 아니라, 해당 날짜에 거래활동 상위 분석 섹터로 "
                "선정되지 않았다는 뜻입니다."
            )

            with st.expander("🔎 날짜별 판정 이유와 종목 보기", expanded=False):
                detail_date = st.selectbox(
                    "날짜",
                    list(reversed(daily_dates)),
                    key=f"daily_sector_detail_date_{selected_date}_{selected_session}",
                )
                detail_sector = st.selectbox(
                    "섹터",
                    selected_sectors,
                    key=f"daily_sector_detail_name_{selected_date}_{selected_session}",
                )
                detail_rows = daily_grid_frame[
                    (daily_grid_frame["date"] == detail_date)
                    & (daily_grid_frame["sector"] == detail_sector)
                ]
                if detail_rows.empty or detail_rows.iloc[0]["status"] == "미추적":
                    st.info("선택한 날짜에는 이 섹터가 주요 분석 대상으로 선정되지 않았습니다.")
                else:
                    detail_row = detail_rows.iloc[0]
                    st.markdown(f"**{detail_sector} · {detail_row['status']}**")
                    st.write(detail_row["reason"])
                    st.markdown(
                        f"**각 종목의 VWAP 위**  \n{member_line(detail_row['vwap_members'])}\n\n"
                        f"**같은 시간 코스피보다 강함**  \n{member_line(detail_row['outperforming_members'])}\n\n"
                        f"**거래 증가와 가격 상승이 함께 나타남**  \n{member_line(detail_row['activity_members'])}"
                    )

    st.subheader("현재 강세 섹터를 그렇게 판단한 이유")
    current_strong = current[current["decision"].isin(_POSITIVE_SECTOR_TIERS)]
    if current_strong.empty:
        st.info(
            "선택한 시각에는 세 가지 강세 조건을 모두 통과한 섹터가 없습니다. "
            "아래 시각별 표에서 장중에 강했던 섹터와 당시 이유를 확인할 수 있습니다."
        )
    else:
        for row in current_strong.itertuples():
            st.markdown(f"**{row.sector} · {row.status}**")
            st.write(row.reason)

    leaders = []
    for time_label in time_order:
        snapshot = frame[frame["time_label"] == time_label]
        passed = snapshot[snapshot["decision"].isin(_POSITIVE_SECTOR_TIERS)]
        leaders.append(
            {
                "시각": time_label,
                "강세로 통과한 섹터": ", ".join(
                    passed.sort_values("score", ascending=False)["sector"].tolist()
                ) or "없음",
                "왜 강했나": " / ".join(
                    f"{row.sector}: {row.reason}"
                    for row in passed.sort_values("score", ascending=False).itertuples()
                ) or "강세 조건을 모두 통과한 섹터 없음",
            }
        )
    st.subheader("시각별 강세 섹터 요약")
    st.dataframe(
        leaders,
        use_container_width=True,
        hide_index=True,
        column_config={
            "시각": st.column_config.TextColumn(width="small"),
            "강세로 통과한 섹터": st.column_config.TextColumn(width="medium"),
            "왜 강했나": st.column_config.TextColumn(width="large"),
        },
    )
    st.caption(
        "이 그래프는 거래대금 자체를 돈의 순유입으로 단정하지 않습니다. "
        "섹터 종목의 VWAP 위치, KOSPI 대비 강도, 거래 활동 증가와 가격 방향을 함께 사용합니다."
    )


def evidence_table(items: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "분석 항목": _AXIS_KO.get(str(item.get("axis", "-")), str(item.get("axis", "-"))),
            "무엇을 보는지": _CODE_KO.get(
                str(item.get("code", "-")),
                str(item.get("code", "-")).replace("_", " "),
            ),
            "현재 관측": _human_message(str(item.get("code", "-")), item.get("message", "-")),
        }
        for item in items
    ]


def build_run_view(detail: Mapping[str, Any]) -> dict[str, Any]:
    judgments = list(detail.get("judgments", []))
    by_scope = {}
    for item in judgments:
        by_scope[(item["scope_type"], item["scope_id"])] = item
    market = by_scope.get(("MARKET", "KOSPI"), {})
    overnight = {
        scope_id: item
        for (scope_type, scope_id), item in by_scope.items()
        if scope_type == "OVERNIGHT"
    }
    sectors = [item for item in judgments if item.get("scope_type") == "SECTOR"]
    run = detail.get("run", {})
    derived = run.get("derived_evidence") if isinstance(run, Mapping) else {}
    setups = derived.get("stock_setups", {}) if isinstance(derived, Mapping) else {}
    lifecycles = derived.get("stock_lifecycles", {}) if isinstance(derived, Mapping) else {}
    position_assessments = (
        derived.get("position_assessments", {}) if isinstance(derived, Mapping) else {}
    )
    return {
        "run": run,
        "market": market,
        "overnight": overnight,
        "sectors": sectors,
        "stocks": list(detail.get("stocks", [])),
        "setups": setups if isinstance(setups, Mapping) else {},
        "lifecycles": lifecycles if isinstance(lifecycles, Mapping) else {},
        "position_assessments": (
            position_assessments if isinstance(position_assessments, Mapping) else {}
        ),
    }


def _render_evidence_group(st, title: str, items: Sequence[Mapping[str, Any]], kind: str) -> None:
    if not items:
        return
    if kind == "error":
        st.error(title)
    elif kind == "warning":
        st.warning(title)
    else:
        st.success(title)
    st.dataframe(evidence_table(items), use_container_width=True, hide_index=True)


def render_market_betting_tab(
    st,
    *,
    db_path: str,
    selected_date: str,
    selected_session: str = "",
    db_source: str = "",
    position_api: Callable[..., tuple[Mapping[str, Any] | None, str]] | None = None,
) -> None:
    """Render into an already-created Streamlit tab container."""

    st.header(f"🧠 장중·오버나이트 베팅 분석 ({selected_date} · {selected_session or '최근 시각'})")
    st.caption(
        "선택한 시간까지의 시장을 기준으로 지금 신규매수가 가능한지, 어느 섹터와 종목을 "
        "봐야 하는지, 종가까지 보유할 만한지를 순서대로 보여줍니다."
    )
    runs = list_decision_runs(
        db_path,
        target_trade_date=normalize_trade_date(selected_date),
        limit=30,
    )
    if not runs:
        st.info(
            "선택한 날짜의 새 분석 엔진 실행 기록이 없습니다. "
            "현재 API 필드가 PARTIAL 상태이면 안전 규칙에 따라 판단 결과가 생성되지 않을 수 있습니다."
        )
        if db_source:
            st.caption(f"조회 DB: {db_source}")
        return

    selected_run = select_run_for_session(runs, selected_date, selected_session)
    if selected_run is None:
        st.info("선택 시간에 대응하는 분석 기록이 없습니다.")
        return
    detail = load_decision_run(db_path, str(selected_run["run_id"]))
    if detail is None:
        st.error("선택한 실행 기록을 읽지 못했습니다.")
        return
    view = build_run_view(detail)
    run = view["run"]
    market = view["market"]
    quality = run.get("quality") or {}
    close_new = view["overnight"].get("CLOSE_NEW_ENTRY")
    hold_existing = view["overnight"].get("HOLD_EXISTING")

    target_time = selected_session_time(selected_date, selected_session)
    data_time = _run_data_time(run)
    if target_time and data_time:
        gap_minutes = abs((data_time - target_time).total_seconds()) / 60
        time_text = (
            f"선택 시간 {target_time:%H:%M} · 실제 사용 데이터 {data_time:%H:%M}"
        )
        is_regular_close = (
            "정규장" in str(selected_session)
            and target_time.strftime("%H:%M") == "16:00"
            and data_time.strftime("%H:%M") == "15:30"
        )
        if is_regular_close:
            st.caption("16:00 마감 조회 · 정규장 최종 체결 데이터(15:30) 기준")
        elif gap_minutes > 10:
            st.warning(f"{time_text} — 정확히 일치하는 분석이 없어 가장 가까운 기록을 사용했습니다.")
        else:
            st.caption(time_text)

    st.subheader("지금 어떻게 행동하면 되나")
    instruction = selection_instruction(view)
    if str(market.get("decision")) == "BLOCK":
        st.error(instruction)
    elif str(market.get("decision")) == "NOT_EVALUABLE":
        st.warning(instruction)
    else:
        st.info(instruction)

    sector_actions = build_sector_action_rows(view)
    close_candidates = [
        row for row in sector_actions
        if row["종가에는"].startswith(("종가베팅 검토", "조건부 후보"))
    ]
    if close_new:
        close_label = decision_label(str(close_new.get("decision", "NOT_EVALUABLE")))
        if close_candidates:
            candidate_text = " / ".join(
                f"{row['섹터']}: {row['종가에는']}" for row in close_candidates[:3]
            )
            st.info(f"종가 신규진입은 ‘{close_label}’입니다. {candidate_text}")
            st.caption(
                "조건부 후보는 아직 매수 승인이 아닙니다. 다음 거래일에는 그날의 시장·섹터·VWAP을 "
                "새로 계산하며, 시초가가 확인선을 크게 뛰어넘으면 추격하지 않습니다."
            )
        else:
            st.info(f"종가 신규진입은 ‘{close_label}’이지만 현재 종가베팅 조건을 갖춘 종목은 없습니다.")

    with st.expander("VWAP가 정확히 무엇인지 보기"):
        st.markdown(
            "- **종목 VWAP**: 해당 종목의 1분봉 대표가격을 거래량으로 가중한 당일 평균 기준선입니다. "
            "실제 틱 체결 전체로 계산한 증권사 VWAP와는 조금 다를 수 있습니다.\n"
            "- **KOSPI VWAP**: KOSPI는 직접 사고파는 종목이 아니므로 ‘코스피 매수가’가 아닙니다. "
            "KOSPI 1분 지수값을 KIS가 제공한 지수 활동량으로 가중한 **장중 평균 지수 기준선**입니다.\n"
            "- **선물 VWAP**: 분석 중인 KOSPI200 선물의 분봉 가격을 계약 거래량으로 가중한 평균 기준선입니다.\n\n"
            "가격이 VWAP 위면 오늘 거래가 집중된 평균 구간보다 위에서 버티는 것이고, "
            "아래면 평균 구간보다 약하다는 뜻입니다. VWAP 하나만으로 매수하지는 않습니다."
        )

    cards = st.columns(5)
    cards[0].metric("장중 시장", decision_label(str(market.get("decision", run.get("market_decision", "-")))))
    cards[1].metric("종가 신규진입", decision_label(close_new["decision"]) if close_new else "미평가")
    cards[2].metric("기존 보유", decision_label(hold_existing["decision"]) if hold_existing else "미평가")
    cards[3].metric("데이터 품질", "차단" if run.get("quality_blocking") else "통과")
    cards[4].metric("관측값", f"{int(run.get('observation_count', 0)):,}개")

    st.caption(
        f"분석 작업 완료 {run.get('evaluated_at_kst', '-')} · DB {db_source or db_path}"
    )
    if run.get("quality_blocking"):
        st.error("필수 데이터의 날짜·신선도·필드 의미가 확인되지 않아 투자 판단으로 승격하지 않았습니다.")
    elif "PLACEHOLDER" in str(market.get("confidence_label", "")):
        st.warning("현재 임계값은 전문가 초깃값(placeholder)이며 확정 운영값이 아닙니다.")

    summary_tab, sector_tab, stock_tab, position_tab, verification_tab, quality_tab = st.tabs(
        ["왜 이런 결론인가", "섹터 강약·종가베팅", "종목별 지금 할 일", "보유 종목", "API 검증", "데이터 품질"]
    )
    with summary_tab:
        for title, judgment in [
            ("장중 시장", market),
            ("종가 신규진입", close_new or {}),
            ("기존 보유", hold_existing or {}),
        ]:
            if not judgment:
                continue
            st.subheader(f"{title}: {decision_label(judgment['decision'])}")
            st.caption(
                "‘들어가도 되는 이유’가 우세해야 진입 쪽으로 판단합니다. "
                "‘조심해야 하는 이유’는 약세 증거, ‘추가 확인할 점’은 확신을 낮추는 요소입니다."
            )
            _render_evidence_group(st, "들어가도 되는 이유", judgment.get("evidence", []), "success")
            _render_evidence_group(st, "조심해야 하는 이유", judgment.get("counter_evidence", []), "error")
            _render_evidence_group(st, "추가 확인할 점", judgment.get("warnings", []), "warning")
            _render_evidence_group(st, "지금 들어가면 안 되는 이유", judgment.get("blockers", []), "error")

    with sector_tab:
        st.caption(
            "강세 섹터와 약세 섹터, 장중 대응, 현재 시점 기준 종가베팅 후보를 한 번에 보여줍니다. "
            "섹터 전체 종목이 아니라 거래가 활발한 추적 종목 표본을 기준으로 합니다."
        )
        if not view["sectors"]:
            st.info("저장된 섹터 판단이 없습니다.")
        else:
            st.dataframe(sector_actions, use_container_width=True, hide_index=True)
            for item in view["sectors"]:
                with st.expander(f"{item['scope_id']} · {decision_label(item['decision'])}"):
                    _render_evidence_group(st, "강하다고 보는 이유", item.get("evidence", []), "success")
                    _render_evidence_group(st, "약하다고 보는 이유", item.get("counter_evidence", []), "error")
                    _render_evidence_group(st, "추가 확인할 점", item.get("warnings", []), "warning")

    with stock_tab:
        if not view["stocks"]:
            st.info("저장된 종목 상태가 없습니다.")
        else:
            names, stock_sectors = _stock_identity(view)
            market_decision = str(market.get("decision", "NOT_EVALUABLE"))
            sector_decisions = {
                str(item.get("scope_id")): str(item.get("decision", "NOT_EVALUABLE"))
                for item in view["sectors"]
            }
            derived = run.get("derived_evidence") if isinstance(run, Mapping) else {}
            bundle = derived.get("bundle", {}) if isinstance(derived, Mapping) else {}
            stock_evidence = bundle.get("stocks", {}) if isinstance(bundle, Mapping) else {}
            rows = []
            for item in view["stocks"]:
                setup = view["setups"].get(item["symbol"], {})
                state = str(item["current_state"])
                sector_name = stock_sectors.get(item["symbol"], "기타")
                sector_decision = sector_decisions.get(sector_name, "NOT_EVALUABLE")
                rows.append(
                    {
                        "_state": state,
                        "종목명": names.get(item["symbol"], item["symbol"]),
                        "섹터": sector_name,
                        "현재 판단": state_label(state),
                        "지금 할 일": contextual_stock_action(
                            state, market_decision, sector_decision
                        ),
                        "현재 가격 기준": reference_values_text(
                            setup,
                            stock_evidence.get(item["symbol"], {})
                            if isinstance(stock_evidence, Mapping) else {},
                        ),
                        "진입하려면 확인할 신호": entry_condition_text(setup, state),
                        "진입 취소·손절 조건": invalidation_condition_text(setup, state),
                        "판단 이유": reason_label(item["reason_code"]),
                    }
                )
            state_order = {
                "진입 조건 충족": 0, "진입 조건 대기": 1, "관찰 중": 2,
                "과열·추격 금지": 3, "판단 자료 부족": 4,
                "진입 신호 실패": 5, "상승 논리 무효": 6,
            }
            rows.sort(key=lambda row: state_order.get(row["현재 판단"], 9))
            candidate_count = sum(
                row["_state"] in {"TRIGGERED", "SETUP"} for row in rows
            )
            extended_count = sum(row["_state"] == "EXTENDED" for row in rows)
            summary_columns = st.columns(3)
            summary_columns[0].metric("진입 검토·대기", f"{candidate_count}개")
            summary_columns[1].metric("추격 금지", f"{extended_count}개")
            summary_columns[2].metric("전체 추적", f"{len(rows)}개")

            st.caption("종목을 누르면 가격 기준과 진입·손절 조건이 잘리지 않고 모두 표시됩니다.")
            state_icon = {
                "TRIGGERED": "🟢",
                "SETUP": "🟡",
                "WATCH": "⚪",
                "EXTENDED": "🟠",
                "FAILED": "🔴",
                "INVALIDATED": "🔴",
                "NOT_EVALUABLE": "⚫",
            }
            for row in rows:
                state = row["_state"]
                title = (
                    f"{state_icon.get(state, '⚪')} {row['종목명']} · "
                    f"{row['섹터']} · {row['현재 판단']}"
                )
                with st.expander(title, expanded=state in {"TRIGGERED", "SETUP"}):
                    st.markdown("**지금 할 일**")
                    st.write(row["지금 할 일"])
                    st.markdown("**현재 가격 기준**")
                    st.write(row["현재 가격 기준"])
                    st.markdown("**진입하려면 확인할 신호**")
                    st.write(row["진입하려면 확인할 신호"])
                    st.markdown("**진입 취소·손절 조건**")
                    st.write(row["진입 취소·손절 조건"])
                    st.markdown("**판단 이유**")
                    st.write(row["판단 이유"])
            st.caption(
                "‘진입 조건 대기’는 후보일 뿐 매수 신호가 아닙니다. 표에 적힌 가격 조건과 함께 "
                "시장·섹터·상대강도·거래 활동 조건이 유지될 때만 ‘진입 조건 충족’으로 바뀝니다."
            )
            with st.expander("트리거와 상태가 무엇인지 보기"):
                st.markdown(
                    "- **트리거**: 관심 후보가 실제 매수 검토 단계로 넘어가기 위한 가격 신호입니다. "
                    "예를 들어 저항 돌파 후 유지, 눌림목에서 VWAP 지지 등이 해당합니다.\n"
                    "- **신호가 아직 없음**: 오류가 아니라, 후보 조건은 있어도 실제 가격 확인이 끝나지 않았다는 뜻입니다.\n"
                    "- **가격 칸이 비어 있음**: 돌파·눌림 구조 또는 손절 기준을 계산할 수 없어 억지로 가격을 만들지 않은 것입니다."
                )

    with position_tab:
        st.subheader("내 보유 종목")
        st.caption(
            "평균매수가와 수익률은 위험 여유를 보여주는 참고값입니다. "
            "보유 판단은 투자 논리 상태, 무효화 가격, 마감 시장 품질을 우선합니다."
        )
        live_positions = []
        if position_api is None:
            st.info("오라클 보유 종목 API가 연결되지 않았습니다.")
        else:
            response, error = position_api("GET", "/positions")
            if error:
                st.warning(error)
            elif response:
                live_positions = list(response.get("positions", []))

            with st.form("market_betting_position_form", clear_on_submit=True):
                form_columns = st.columns(2)
                ticker = form_columns[0].text_input("종목코드", max_chars=6, placeholder="005930")
                name = form_columns[1].text_input("종목명", placeholder="삼성전자")
                average_price = form_columns[0].number_input(
                    "평균매수가", min_value=0.0, step=100.0
                )
                quantity = form_columns[1].number_input(
                    "보유수량", min_value=0.0, step=1.0
                )
                thesis_label = form_columns[0].selectbox(
                    "현재 투자 논리",
                    ["판단 보류", "유효", "훼손"],
                    help="시스템이 뉴스나 개인의 매수 이유를 임의로 추측하지 않도록 직접 지정합니다.",
                )
                invalidation_price = form_columns[1].number_input(
                    "무효화 가격(선택)", min_value=0.0, step=100.0,
                    help="이 가격 이하에서는 기존 보유 논리가 깨졌다고 볼 구조적 기준입니다.",
                )
                thesis_note = st.text_input(
                    "투자 논리 메모(선택)", placeholder="예: 전고점 돌파 후 지지, 실적 상향 추세"
                )
                submitted = st.form_submit_button("보유 정보 저장")
            if submitted:
                normalized = ticker.strip().zfill(6)
                if len(normalized) != 6 or not normalized.isdigit():
                    st.error("종목코드는 숫자 6자리로 입력해주세요.")
                elif not name.strip() or average_price <= 0 or quantity <= 0:
                    st.error("종목명, 평균매수가, 보유수량을 올바르게 입력해주세요.")
                else:
                    thesis_status = {"판단 보류": "UNSPECIFIED", "유효": "ACTIVE", "훼손": "BROKEN"}[thesis_label]
                    payload = {
                        "action": "upsert",
                        "ticker": normalized,
                        "name": name.strip(),
                        "average_price": average_price,
                        "quantity": quantity,
                        "thesis_status": thesis_status,
                        "thesis_note": thesis_note.strip(),
                        "invalidation_price": invalidation_price or None,
                    }
                    result, save_error = position_api("POST", "/positions", json_body=payload)
                    if save_error:
                        st.error(save_error)
                    else:
                        st.success((result or {}).get("message", "보유 정보를 저장 중입니다."))

        assessments = view["position_assessments"]
        if live_positions:
            rows = []
            for position in live_positions:
                ticker_value = str(position.get("ticker", ""))
                assessment = assessments.get(ticker_value, {})
                rows.append(
                    {
                        "종목": position.get("name", ticker_value),
                        "종목코드": ticker_value,
                        "평균매수가": position.get("average_price"),
                        "보유수량": position.get("quantity"),
                        "현재가": assessment.get("current_price"),
                        "수익률(%)": (
                            round(float(assessment["profit_loss_ratio"]) * 100, 2)
                            if assessment.get("profit_loss_ratio") is not None else None
                        ),
                        "논리 상태": position.get("thesis_status"),
                        "무효화 가격": position.get("invalidation_price"),
                        "보유 판단": decision_label(str(assessment.get("decision", "분석 대기"))),
                        "판단 사유": ", ".join(assessment.get("reasons", [])),
                    }
                )
            st.dataframe(rows, use_container_width=True, hide_index=True)
            if position_api is not None:
                remove_options = {
                    f"{item.get('name', '')} ({item.get('ticker', '')})": item.get("ticker", "")
                    for item in live_positions
                }
                remove_label = st.selectbox("삭제할 보유 종목", list(remove_options))
                if st.button("선택 종목 삭제", type="secondary"):
                    result, remove_error = position_api(
                        "POST", "/positions",
                        json_body={"action": "remove", "ticker": remove_options[remove_label]},
                    )
                    if remove_error:
                        st.error(remove_error)
                    else:
                        st.success((result or {}).get("message", "삭제 중입니다."))
        elif position_api is not None:
            st.info("입력된 보유 종목이 없습니다.")

    with verification_tab:
        st.subheader("KIS 실시간 필드 검증")
        st.caption(
            "개장·장중·마감 세 구간의 라이브 증거가 모두 통과한 뒤에만 수동 검토 대상으로 표시합니다. "
            "이 화면의 통과 상태도 필드를 자동 승인하지 않습니다."
        )
        if position_api is None:
            st.info("오라클 검증 상태 API가 연결되지 않았습니다.")
        else:
            readiness, readiness_error = position_api("GET", "/verification-readiness")
            if readiness_error:
                st.warning(readiness_error)
            elif readiness:
                overall = str(readiness.get("overall_status", "PENDING_CHECKPOINTS"))
                if overall == "READY_FOR_MANUAL_REVIEW":
                    st.success("세 구간 증거 수집 완료 · 수동 필드 검토 대기")
                else:
                    st.info("라이브 검증 체크포인트 수집 대기")
                metrics = st.columns(3)
                metrics[0].metric("검증 거래일", readiness.get("trade_date") or "아직 없음")
                metrics[1].metric("전체 상태", overall)
                metrics[2].metric(
                    "자동 승인", "사용 안 함" if not readiness.get("auto_promotes_registry") else "경고"
                )
                probe_rows = []
                for probe_id, probe in (readiness.get("probes") or {}).items():
                    checkpoints = probe.get("checkpoints") or {}
                    probe_rows.append(
                        {
                            "API": probe_id,
                            "상태": probe.get("status", "PENDING_CHECKPOINTS"),
                            "개장": (checkpoints.get("OPEN") or {}).get("status", "대기"),
                            "장중": (checkpoints.get("MID") or {}).get("status", "대기"),
                            "마감": (checkpoints.get("CLOSE") or {}).get("status", "대기"),
                            "미수집 구간": ", ".join(probe.get("missing_checkpoints", [])),
                        }
                    )
                if probe_rows:
                    st.dataframe(probe_rows, use_container_width=True, hide_index=True)
                else:
                    st.info("월요일 첫 검증 실행 전입니다.")

    with quality_tab:
        issues = quality.get("issues", []) if isinstance(quality, dict) else []
        if not issues:
            st.success("저장된 데이터 품질 문제가 없습니다.")
        else:
            st.dataframe(
                [
                    {
                        "심각도": _SEVERITY_KO.get(
                            str(item.get("severity", "-")), str(item.get("severity", "-"))
                        ),
                        "문제 유형": _QUALITY_CODE_KO.get(
                            str(item.get("code", "-")),
                            str(item.get("code", "-")).replace("_", " "),
                        ),
                        "설명": item.get("message", "-"),
                        "원천": ", ".join(item.get("sources", [])),
                    }
                    for item in issues
                ],
                use_container_width=True,
                hide_index=True,
            )
        with st.expander("파생값 원문 보기"):
            derived = run.get("derived_evidence")
            if derived is None:
                st.caption("이 실행에는 파생값 스냅샷이 저장되지 않았습니다.")
            else:
                st.json(derived)
