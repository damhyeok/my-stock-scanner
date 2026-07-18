"""Streamlit renderer for the transparent staged close-bet rule model."""

from __future__ import annotations

import pandas as pd
import streamlit as st


def render_rule_model_section(data: pd.DataFrame, selected_date: str) -> None:
    st.subheader('④ 단계형 종가베팅 규칙 모델 · 시장강도 수동 판단')
    st.caption(
        '유동성 AND Price Action(A/B/C) AND AVWAP·표본 POC 위치 AND '
        '14:30 이후 흐름 AND 상대강도 프록시 AND 변동성을 모두 통과한 후보입니다. '
        '시장강도는 자동 점수에 포함하지 않으며 위 시장강도 탭을 보고 최종 판단합니다.'
    )
    with st.expander('모델 조건 간단 설명'):
        st.markdown(
            '- **Price Action**: 눌림 후 반등(A), 고점 돌파(B), 지지 이탈 후 회복(C) 중 하나\n'
            '- **AVWAP·위치**: 주요 저점/거래량 집중일 AVWAP 중 하나 위에서 마감하고 표본 POC 회복\n'
            '- **오후 흐름**: 가격 유지, 저점 미갱신, 상승봉 거래량 우세, 높은 CLV 중 3개 이상\n'
            '- **상대강도·변동성**: 업종·시장 대비 강하고 ATR이 지나치게 낮거나 높지 않은 종목\n'
            '- **최종 판단**: 기술조건 통과 후 시장강도 탭이 나쁘면 매수하지 않음\n\n'
            'AVWAP·POC·상대강도는 보유 데이터로 계산한 프록시이며, 실제 체결 Delta나 전체 분봉 Volume Profile은 아닙니다.'
        )
    if data.empty:
        st.info('아직 단계형 규칙 모델 실행 기록이 없습니다. 다음 전체 분석 후 표시됩니다.')
        return
    rows = data[data['trade_date'].astype(str).eq(str(selected_date))].copy()
    if rows.empty:
        st.info('선택한 날짜의 단계형 규칙 모델 기록이 없습니다.')
        return
    technical = rows[rows['technical_pass'].astype(bool)].copy()
    counts = st.columns(4)
    counts[0].metric('전체 평가', f'{len(rows)}개')
    counts[1].metric('Price Action', f"{int(rows['price_action_pass'].sum())}개")
    counts[2].metric('오후 흐름', f"{int(rows['afternoon_flow_pass'].sum())}개")
    counts[3].metric('기술 최종 통과', f'{len(technical)}개')
    if technical.empty:
        st.info('모든 기술 단계를 동시에 통과한 후보가 없습니다.')
        return
    display = technical[[
        'name', 'ticker', 'price_action_types', 'clv', 'return_after_1430',
        'sampled_afternoon_clv', 'rs5_sector_proxy', 'rs20_market_proxy',
        'atr14_pct', 'relative_strength_quality', 'decision',
    ]].copy()
    for column in [
        'clv', 'return_after_1430', 'sampled_afternoon_clv',
        'rs5_sector_proxy', 'rs20_market_proxy', 'atr14_pct',
    ]:
        display[column] = pd.to_numeric(display[column], errors='coerce') * 100
    display = display.rename(columns={
        'name': '종목명', 'ticker': '종목코드', 'price_action_types': 'Price Action',
        'clv': '일봉 CLV(%)', 'return_after_1430': '14:30 이후 수익률(%)',
        'sampled_afternoon_clv': '오후 CLV(%)',
        'rs5_sector_proxy': '업종 대비 5일 RS 프록시(%p)',
        'rs20_market_proxy': '시장 대비 20일 RS 프록시(%p)',
        'atr14_pct': 'ATR14(%)', 'relative_strength_quality': 'RS 품질',
        'decision': '판정',
    })
    st.dataframe(
        display.style.format({
            column: '{:,.2f}' for column in display.select_dtypes(include=['number']).columns
        }),
        use_container_width=True,
        hide_index=True,
    )
    st.warning('이 표는 기술조건 통과 목록입니다. 시장강도가 나쁘다고 판단하면 매수하지 않습니다.')
