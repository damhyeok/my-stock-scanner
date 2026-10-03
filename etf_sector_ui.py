"""Render stored ETF observations only; no quotes/API requests in the web app."""
import html
import sqlite3

import pandas as pd
import plotly.express as px
import streamlit as st

from etf_sector_flow import ETF_UNIVERSE, build_ranking, build_trend
from streamlit_layout import layout_width


@st.cache_data(ttl=60)
def read_etf_data(path, version):
    frames = {}
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        for table in ("etf_sector_snapshots", "etf_sector_daily", "etf_sector_holdings", "etf_sector_runs"):
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                frames[table] = pd.read_sql_query(f'SELECT * FROM "{table}"', conn)
            else:
                frames[table] = pd.DataFrame()
    return frames


def fmt(value, suffix="", signed=False):
    return "집계 중" if pd.isna(value) else (f"{value:+.2f}" if signed else f"{value:,.2f}") + suffix


def render_etf_sector_tab(path, version, day, session):
    st.header("🧭 ETF 섹터 흐름")
    st.caption("오늘 상승률 높은 순서입니다. ETF 거래대금은 구성종목 거래대금 합계나 순유입 금액이 아닙니다. 매수 신호가 아닌 섹터 관찰 자료입니다.")
    data = read_etf_data(str(path), str(version))
    snapshots, daily = data["etf_sector_snapshots"], data["etf_sector_daily"]
    ranking = build_ranking(snapshots, daily, str(day), str(session))
    runs = data["etf_sector_runs"]
    if not runs.empty:
        run = runs[(runs.trade_date == str(day)) & (runs.session == str(session))]
        if not run.empty:
            row = run.iloc[0]
            st.caption(f"{day} · {session} · 시세 수집 {row.success_count}/{row.expected_count}개")
            if row.status != "success":
                st.warning("일부 ETF 또는 구성종목 조회가 누락됐습니다. 누락값을 0으로 처리하거나 과거 시세로 대신 표시하지 않습니다.")
                with st.expander("수집 상태 확인"):
                    st.text(str(row.errors))
    if ranking.empty:
        st.info("선택한 날짜·시간의 ETF 수집 결과가 없습니다. 추가 이전의 장중 결과는 소급 생성하지 않으며, 다음 정상 분석부터 저장됩니다.")
        with st.expander("관찰 ETF 목록"):
            st.dataframe(pd.DataFrame(ETF_UNIVERSE, columns=["섹터", "코드", "대표 ETF", "참고"]), hide_index=True, **layout_width())
        return

    st.subheader("오늘 섹터 상승률 순위")
    # Mobile cards expose the essential three fields without an embedded scroll trap.
    cards = []
    for row in ranking.itertuples():
        rate = fmt(row.change_rate, "%", True)
        color = "#c62828" if row.change_rate > 0 else "#1565c0"
        cards.append(f'<div class="etf-flow-card"><span>{row.rank}. {html.escape(row.sector)}</span>'
                     f'<strong style="color:{color}">{rate}</strong>'
                     f'<small>거래배수 {fmt(row.trading_multiple, "배")}</small></div>')
    st.markdown('<style>.etf-flow-card{display:grid;grid-template-columns:1fr auto;gap:5px 12px;'
                'padding:10px 4px;border-bottom:1px solid #ddd}.etf-flow-card small{grid-column:1/-1;opacity:.75}'
                '@media(min-width:769px){.etf-flow-mobile{display:none}}</style>'
                '<div class="etf-flow-mobile">' + "".join(cards) + '</div>', unsafe_allow_html=True)
    display = ranking[["rank", "sector", "name", "change_rate", "excess_rate", "trading_eok", "trading_multiple", "return_5d"]].rename(columns={
        "rank": "순위", "sector": "섹터", "name": "대표 ETF", "change_rate": "오늘 상승률(%)",
        "excess_rate": "코스피 대비(%p)", "trading_eok": "ETF 거래대금(억)",
        "trading_multiple": "평소 대비(배)", "return_5d": "최근 5일(%)"})
    st.markdown('<style>.etf-flow-desktop{overflow-x:auto}.etf-flow-desktop table{width:100%;border-collapse:collapse;font-size:14px}'
                '.etf-flow-desktop th,.etf-flow-desktop td{padding:8px;border-bottom:1px solid #ddd;text-align:left}'
                '@media(max-width:768px){.etf-flow-desktop{display:none}}</style>'
                '<div class="etf-flow-desktop">' + display.to_html(index=False, escape=True, na_rep="집계 중", float_format=lambda v: f"{v:,.2f}")
                + '</div>', unsafe_allow_html=True)
    with st.expander("전체 수치 표", expanded=False):
        st.dataframe(display.style.format({"오늘 상승률(%)": "{:+.2f}", "코스피 대비(%p)": "{:+.2f}",
                     "ETF 거래대금(억)": "{:,.2f}", "평소 대비(배)": "{:.2f}", "최근 5일(%)": "{:+.2f}"}, na_rep="집계 중"),
                     hide_index=True, **layout_width())
    with st.expander("지표 간단 설명"):
        st.markdown("- 오늘 상승률: 전일 종가 대비 선택한 분석 시점의 가격 변화.\n"
                    "- 코스피 대비: ETF 상승률에서 같은 수집 시점의 코스피 상승률을 뺀 값(%p).\n"
                    "- 평소 대비: 최근 최대 20거래일의 동일 분석 시간·실제 수집시각 ±5분 거래대금 평균 대비 배수. 과거 5일 이상 필요.\n"
                    "- 최근 5일: 5거래일 전 종가 대비 선택 시점 수익률.\n"
                    "- 거래 증가를 동반한 상승도 순유입이나 추가 상승을 보장하지 않습니다.\n"
                    "- ETF는 우리 섹터 분류와 다를 수 있고 구성종목이 중복됩니다. 수익률은 분배금을 포함하지 않는 가격 기준입니다.")

    st.subheader("최근 10거래일 흐름")
    options = ranking.sector.tolist()
    selected = st.multiselect("비교할 섹터", options, default=options[:8], key="etf_flow_sectors")
    tickers = ranking.loc[ranking.sector.isin(selected), "ticker"].tolist()
    trend = build_trend(daily, ranking, str(day), tickers)
    if not trend.empty:
        trend["date"] = pd.to_datetime(trend.date, format="%Y%m%d")
        chart = px.line(trend, x="date", y="return", color="sector", markers=True,
                        labels={"date": "거래일", "return": "첫 표시일 대비 수익률(%)", "sector": "섹터"})
        chart.update_layout(height=380, margin=dict(l=5, r=5, t=15, b=5), legend=dict(orientation="h"))
        st.plotly_chart(chart, **layout_width())
        st.caption("이전 날짜는 종가, 마지막 날짜는 선택한 분석 시점 가격입니다. 휴장일은 제외하며 최대 10거래일을 표시합니다.")
    else:
        st.info("비교할 섹터를 선택하거나 일별 가격 수집을 기다려 주세요.")

    st.subheader("섹터 구성종목 확인")
    sector = st.selectbox("확인할 섹터", options, key="etf_flow_detail")
    chosen = ranking[ranking.sector == sector].iloc[0]
    note = next(item[3] for item in ETF_UNIVERSE if item[1] == chosen.ticker)
    st.caption(f"{chosen['name']} ({chosen.ticker}) · {note} · 실제 수집 {chosen.collected_at_kst}")
    st.write(f"오늘 {fmt(chosen.change_rate, '%', True)} · 코스피 대비 {fmt(chosen.excess_rate, '%p', True)} · "
             f"거래대금 {fmt(chosen.trading_eok, '억')} · 최근 5일 {fmt(chosen.return_5d, '%', True)}")
    holdings = data["etf_sector_holdings"]
    if not holdings.empty:
        holdings = holdings[(holdings.trade_date == str(day)) & (holdings.session == str(session))
                            & (holdings.etf_ticker == chosen.ticker)].sort_values("weight", ascending=False)
    if holdings.empty:
        st.info("이 시점의 구성종목 조회 결과가 없습니다.")
    else:
        shown = holdings[["name", "weight", "change_rate"]].rename(columns={"name": "종목명", "weight": "편입비중(%)", "change_rate": "오늘 상승률(%)"})
        st.dataframe(shown.style.format({"편입비중(%)": "{:.2f}", "오늘 상승률(%)": "{:+.2f}"}, na_rep="-"),
                     hide_index=True, **layout_width())
