"""Renderer for the market-betting engine tab inside the existing Streamlit app."""

from __future__ import annotations

import re
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
    "PRICE_VWAP_STRUCTURE": "현재 가격이 당일 평균 매매가격(VWAP) 위인지와 최근 가격 방향",
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
    "SECTOR_ABOVE_VWAP_RATIO": "추적 종목 중 당일 평균가격(VWAP) 위에 있는 종목 비율",
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
    db_source: str = "",
    position_api: Callable[..., tuple[Mapping[str, Any] | None, str]] | None = None,
) -> None:
    """Render into an already-created Streamlit tab container."""

    st.header(f"🧠 장중·오버나이트 베팅 분석 ({selected_date})")
    st.caption(
        "시장 → 섹터 → 종목 순서로 진입 가능성을 점검합니다. "
        "분석 보조 기능이며 주문을 실행하지 않습니다."
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

    labels = {
        row["run_id"]: (
            f"{row['evaluated_at_kst'][11:19]} · {decision_label(row['market_decision'])} · "
            f"{row['session_phase']}"
        )
        for row in runs
    }
    default_run_index = next(
        (
            index for index, row in enumerate(runs)
            if row.get("market_decision") != "NOT_EVALUABLE"
        ),
        0,
    )
    selected_run_id = st.selectbox(
        "분석 실행 시각",
        options=[row["run_id"] for row in runs],
        format_func=lambda run_id: labels[run_id],
        index=default_run_index,
        key=f"market_betting_run_{selected_date}",
    )
    detail = load_decision_run(db_path, selected_run_id)
    if detail is None:
        st.error("선택한 실행 기록을 읽지 못했습니다.")
        return
    view = build_run_view(detail)
    run = view["run"]
    market = view["market"]
    quality = run.get("quality") or {}

    cards = st.columns(5)
    cards[0].metric("장중 시장", decision_label(str(market.get("decision", run.get("market_decision", "-")))))
    close_new = view["overnight"].get("CLOSE_NEW_ENTRY")
    hold_existing = view["overnight"].get("HOLD_EXISTING")
    cards[1].metric("종가 신규진입", decision_label(close_new["decision"]) if close_new else "미평가")
    cards[2].metric("기존 보유", decision_label(hold_existing["decision"]) if hold_existing else "미평가")
    cards[3].metric("데이터 품질", "차단" if run.get("quality_blocking") else "통과")
    cards[4].metric("관측값", f"{int(run.get('observation_count', 0)):,}개")

    st.caption(
        f"평가시각 {run.get('evaluated_at_kst', '-')} · 설정 {run.get('config_version', '-')} · "
        f"엔진 {run.get('engine_version', '-')} · DB {db_source or db_path}"
    )
    if run.get("quality_blocking"):
        st.error("필수 데이터의 날짜·신선도·필드 의미가 확인되지 않아 투자 판단으로 승격하지 않았습니다.")
    elif "PLACEHOLDER" in str(market.get("confidence_label", "")):
        st.warning("현재 임계값은 전문가 초깃값(placeholder)이며 확정 운영값이 아닙니다.")

    summary_tab, sector_tab, stock_tab, position_tab, verification_tab, quality_tab = st.tabs(
        ["판단 근거", "섹터", "종목 상태", "보유 종목", "API 검증", "데이터 품질"]
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
            _render_evidence_group(st, "지지 근거", judgment.get("evidence", []), "success")
            _render_evidence_group(st, "반대 증거", judgment.get("counter_evidence", []), "error")
            _render_evidence_group(st, "경고", judgment.get("warnings", []), "warning")
            _render_evidence_group(st, "차단 사유", judgment.get("blockers", []), "error")

    with sector_tab:
        st.caption(
            "섹터 판정은 시장 전체가 아니라 거래 활동으로 선별한 추적 종목 표본을 기준으로 합니다."
        )
        if not view["sectors"]:
            st.info("저장된 섹터 판단이 없습니다.")
        else:
            rows = [
                {
                    "섹터": item["scope_id"],
                    "상태": decision_label(item["decision"]),
                    "지지 근거 수": len(item.get("evidence", [])),
                    "반대 증거 수": len(item.get("counter_evidence", [])),
                    "경고 수": len(item.get("warnings", [])),
                }
                for item in view["sectors"]
            ]
            st.dataframe(rows, use_container_width=True, hide_index=True)
            for item in view["sectors"]:
                with st.expander(f"{item['scope_id']} · {decision_label(item['decision'])}"):
                    _render_evidence_group(st, "지지 근거", item.get("evidence", []), "success")
                    _render_evidence_group(st, "반대 증거", item.get("counter_evidence", []), "error")
                    _render_evidence_group(st, "경고", item.get("warnings", []), "warning")

    with stock_tab:
        if not view["stocks"]:
            st.info("저장된 종목 상태가 없습니다.")
        else:
            rows = []
            for item in view["stocks"]:
                setup = view["setups"].get(item["symbol"], {})
                lifecycle = view["lifecycles"].get(item["symbol"], {})
                rows.append(
                    {
                        "종목코드": item["symbol"],
                        "진입 유형": state_label(setup.get("setup_type", "NONE")),
                        "이전 상태": state_label(item["previous_state"]),
                        "현재 상태": state_label(item["current_state"]),
                        "진입 기준가": setup.get("entry_reference"),
                        "무효화 가격": setup.get("invalidation_price"),
                        "목표 참고가": setup.get("reward_reference"),
                        "손익비": setup.get("reward_risk_ratio"),
                        "트리거 추적": ", ".join(lifecycle.get("reasons", [])),
                        "트리거 후 분봉": lifecycle.get("bars_since_trigger", 0),
                        "전환 사유": reason_label(item["reason_code"]),
                    }
                )
            st.dataframe(rows, use_container_width=True, hide_index=True)
            st.caption(
                "진입 기준가와 무효화 가격은 구조적 참고값입니다. "
                "실제 신규 진입 검토는 시장·섹터 게이트까지 통과한 TRIGGERED 상태에서만 가능합니다."
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
                        "심각도": item.get("severity", "-"),
                        "코드": item.get("code", "-"),
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
