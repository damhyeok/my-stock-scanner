"""ETF-first overview; existing constituent-based judgments stay untouched."""
from contextlib import closing
import html
import json
from pathlib import Path
import sqlite3

import pandas as pd
import streamlit as st
from etf_sector_flow import ETF_UNIVERSE
from streamlit_layout import layout_width


@st.cache_data(ttl=60)
def load_strength_data(path,modified,day):
    with closing(sqlite3.connect(f'file:{path}?mode=ro',uri=True)) as conn:
        def read(table):
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name=?",(table,)).fetchone():
                return pd.DataFrame()
            return pd.read_sql_query(f'SELECT * FROM {table} WHERE trade_date=?',conn,params=(day,))
        return read('etf_sector_snapshots'),read('etf_strength_display')


def build_overview(snapshots,derived,day,session):
    snaps=snapshots[(snapshots.trade_date==day)&(snapshots.session==session)] if not snapshots.empty else snapshots
    summaries=derived[(derived.trade_date==day)&(derived.session==session)] if not derived.empty else derived
    payloads={r.ticker:json.loads(r.payload) for r in summaries.itertuples()}
    quotes={r['ticker']:r for r in snaps.to_dict('records')}
    rows=[]
    for sector,ticker,name,note in ETF_UNIVERSE:
        quote=quotes.get(ticker,{})
        payload=payloads.get(ticker,{})
        path=payload.get('hourly',{})
        rate=quote.get('change_rate')
        bench=quote.get('kospi_rate')
        since=payload.get('since_open')
        label=payload.get('hourly_label','분봉 자료 부족' if quote else '시세 없음')
        if since is None:
            interpretation='시가 자료 부족'
        elif path.get('status')!='COMPLETE':
            interpretation='장중 상승' if since>0 else '장중 하락' if since<0 else '시가 수준'
            interpretation+=' / 60분 판단 보류'
        elif since>0:
            interpretation='장중 상승 / 최근 약화' if label=='하락 흐름' else '장중 상승 / 상승 흐름 유지' if label=='상승 흐름 유지' else '장중 상승 / 최근 혼조'
        elif since<0:
            interpretation='장중 약세 / 최근 반등' if label=='상승 흐름 유지' else '장중 약세 / 최근 하락' if label=='하락 흐름' else '장중 약세 / 최근 혼조'
        else:
            interpretation='시가 수준 / '+label
        rows.append(dict(ticker=ticker,sector=sector,name=name,note=note,change_rate=rate,
                         since_open=since,hourly_return=path.get('return_ratio')*100 if path.get('status')=='COMPLETE' and path.get('return_ratio') is not None else None,
                         excess=rate-bench if pd.notna(rate) and pd.notna(bench) else None,
                         hourly_label=label,interpretation=interpretation,
                         minutes=f"{path.get('observed_minutes',0)}/{path.get('expected_minutes',60)}",
                         collected=quote.get('collected_at_kst'),payload=payload))
    return pd.DataFrame(rows).sort_values(['change_rate','sector'],ascending=[False,True],na_position='last')


def show_overview(frame):
    cols=[('sector','섹터'),('change_rate','오늘 등락률'),('since_open','시가 이후'),
          ('hourly_return','최근 60분'),('excess','코스피 대비'),('hourly_label','최근 흐름'),('interpretation','해석')]
    def cell(key,value):
        if key in {'change_rate','since_open','hourly_return','excess'}:
            return '미확보' if pd.isna(value) else f'{value:+.2f}'+('%p' if key=='excess' else '%')
        return html.escape(str(value))
    header=''.join('<th>'+label+'</th>' for _,label in cols)
    body=''.join('<tr>'+''.join('<td>'+cell(key,row[key])+'</td>' for key,_ in cols)+'</tr>' for row in frame.to_dict('records'))
    st.markdown('<style>.etf-strength-grid{width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch}.etf-strength-grid table{border-collapse:collapse;font-size:13px}.etf-strength-grid th,.etf-strength-grid td{padding:8px;white-space:nowrap;border-bottom:1px solid #ddd}.etf-strength-grid th:first-child,.etf-strength-grid td:first-child{position:sticky;left:0;background:var(--background-color,#fff);z-index:1;min-width:110px}</style>'
                +'<div class="etf-strength-grid"><table><thead><tr>'+header+'</tr></thead><tbody>'+body+'</tbody></table></div>',unsafe_allow_html=True)


@st.fragment
def render_today_sector_flow(path,day,session):
    import altair as alt
    st.header('🧭 오늘 섹터 흐름 · 대표 ETF 기준')
    st.caption('대표 ETF 18개를 오늘 등락률순으로 비교합니다. ETF 가격 흐름과 개별종목의 동반 강세는 다릅니다. 모바일에서는 표를 좌우로 밀어 확인하세요.')
    snapshots,derived=load_strength_data(str(path),Path(path).stat().st_mtime_ns,str(day))
    frame=build_overview(snapshots,derived,str(day),str(session))
    available=frame.change_rate.notna().sum()
    ready=frame[frame.payload.map(lambda p:p.get('hourly',{}).get('status')=='COMPLETE')]
    st.caption(f'{day} · {session} · 시세 {available}/18개 · 최근 60분 자료 충족 {len(ready)}/18개')
    if not available:
        st.info('선택한 날짜·분석시간의 ETF 시세가 없습니다. 다른 시간의 값으로 대체하지 않습니다.')
    show_overview(frame)
    st.caption('오늘 등락률·코스피 대비는 각 분석 시세 수집 시점의 전일 종가 대비 값입니다. 시가 이후는 ETF 시가 대비 수집 가격입니다. 최근 60분은 선택 시각 이전의 완성된 1분봉 전체를 분석하며, 15:20 이후 동시호가는 제외합니다. 정규장 마감의 최근 60분 구간은 14:20~15:19입니다.')
    st.caption('개장 후 60분 미만은 잠정, 분봉 누락 과다 시 판단 보류입니다. ETF 시세 수집 시각과 분봉 평가 종료 시각은 서로 다를 수 있습니다. 기존 과거 기록에 분봉이 없으면 새 판정을 소급 생성하지 않습니다.')
    with st.expander('해석과 한계 · 간단 설명'):
        st.write('장중 상승 / 최근 약화: 시가보다 높지만 마지막 60분은 하락 흐름입니다. 장중 약세 / 최근 반등: 시가보다 낮지만 마지막 60분의 상승 구조가 확인됩니다. 매수·매도 지시나 다음 날 예측이 아닙니다.')
        st.write('상승 흐름 유지: 최근 구간 수익률과 전체 분봉 추세가 양수이고, 10분 구간별 저점 상승 비율 60% 이상, 구간 상승폭 반납 50% 이하인 상태입니다. 단순 상승률과 함께 보세요. 임계값은 수익성으로 검증되지 않았습니다.')
        st.write('ETF 거래대금은 섹터 구성종목의 거래대금 합계나 순유입액이 아닙니다. ETF는 편입 비중이 높은 일부 종목의 영향을 받을 수 있고, 시장가격과 순자산가치가 다를 수 있습니다. 코스피 대비는 공통 비교용이며 각 ETF의 추종지수 대비 성과를 뜻하지 않습니다.')
    st.subheader('분석 시간별 최근 60분 수익률')
    points=[]
    if not derived.empty:
        from etf_strength import cutoff_for_session
        limit=cutoff_for_session(day,session)
        for row in derived.itertuples():
            if cutoff_for_session(day,row.session)>limit:
                continue
            p=json.loads(row.payload)
            hp=p.get('hourly',{})
            if hp.get('status')=='COMPLETE' and hp.get('return_ratio') is not None:
                points.append({'섹터':p['sector'],'시각':cutoff_for_session(day,row.session).strftime('%H:%M'),
                               '최근60분(%)':hp['return_ratio']*100})
    if points:
        chart=alt.Chart(pd.DataFrame(points)).mark_rect().encode(
            x=alt.X('시각:N',sort='ascending'),y=alt.Y('섹터:N',sort=frame.sector.tolist()),
            color=alt.Color('최근60분(%):Q',scale=alt.Scale(scheme='redblue',domainMid=0,reverse=True)),
            tooltip=['섹터','시각',alt.Tooltip('최근60분(%):Q',format='+.2f')]).properties(height=450)
        st.altair_chart(chart,**layout_width())
    else:
        st.info('60분 분봉이 충족된 분석부터 시간별 흐름이 쌓입니다. 빈 칸은 보합이 아니라 미확보입니다.')
    with st.expander('대표 ETF · 편입 범위 · 세부 계산 확인'):
        for row in frame.itertuples():
            st.markdown(f'**{row.sector} — {row.name} ({row.ticker})**')
            st.caption(f'{row.note} · 시세 수집: {row.collected or "미확보"} · 분봉: {row.minutes}')
            hp=row.payload.get('hourly',{})
            st.caption(f'분봉 평가: {hp.get("window_start","미확보")} ~ {hp.get("window_end","미확보")} · 자료 상태: {hp.get("status","미확보")}')
    with st.expander('보조 확인 · 기존 개별종목 기반 섹터 강약',expanded=False):
        st.caption('아래는 기존 후보 섹터·개별종목 기반 결과를 그대로 보존한 자료입니다. ETF 대표 범위와 분석 종목 구성이 다르므로 판정이 같을 필요는 없습니다.')
        from market_betting_engine.streamlit_tab import render_sector_strength_flow_tab
        render_sector_strength_flow_tab(st,db_path=str(path),selected_date=str(day),selected_session=str(session))
