"""Renderer for the market-betting engine tab inside the existing Streamlit app."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

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


def decision_label(value: str) -> str:
    return _DECISION_KO.get(value, value)


def evidence_table(items: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "축": str(item.get("axis", "-")),
            "코드": str(item.get("code", "-")),
            "설명": str(item.get("message", "-")),
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
    return {
        "run": run,
        "market": market,
        "overnight": overnight,
        "sectors": sectors,
        "stocks": list(detail.get("stocks", [])),
        "setups": setups if isinstance(setups, Mapping) else {},
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


def render_market_betting_tab(st, *, db_path: str, selected_date: str, db_source: str = "") -> None:
    """Render into an already-created Streamlit tab container."""

    st.header(f"🧠 장중·오버나이트 베팅 분석 ({selected_date})")
    st.caption(
        "시장 → 섹터 → 종목 순서로 진입 가능성을 점검합니다. "
        "분석 보조 기능이며 주문을 실행하지 않습니다."
    )
    runs = list_decision_runs(db_path, target_trade_date=str(selected_date), limit=30)
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
    selected_run_id = st.selectbox(
        "분석 실행 시각",
        options=[row["run_id"] for row in runs],
        format_func=lambda run_id: labels[run_id],
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

    summary_tab, sector_tab, stock_tab, quality_tab = st.tabs(
        ["판단 근거", "섹터", "종목 상태", "데이터 품질"]
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
                rows.append(
                    {
                        "종목코드": item["symbol"],
                        "진입 유형": setup.get("setup_type", "NONE"),
                        "이전 상태": item["previous_state"],
                        "현재 상태": item["current_state"],
                        "진입 기준가": setup.get("entry_reference"),
                        "무효화 가격": setup.get("invalidation_price"),
                        "목표 참고가": setup.get("reward_reference"),
                        "손익비": setup.get("reward_risk_ratio"),
                        "전환 사유": item["reason_code"],
                    }
                )
            st.dataframe(rows, use_container_width=True, hide_index=True)
            st.caption(
                "진입 기준가와 무효화 가격은 구조적 참고값입니다. "
                "실제 신규 진입 검토는 시장·섹터 게이트까지 통과한 TRIGGERED 상태에서만 가능합니다."
            )

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
