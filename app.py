import streamlit as st
import pandas as pd
import sqlite3
from analyzer import StockAnalyzer

# 페이지 기본 설정
st.set_page_config(page_title="주식 분석 대시보드", layout="wide", page_icon="📈")

# 컬럼 한글 매핑 딕셔너리
COLUMN_MAP = {
    'date': '날짜',
    'session': '세션',
    'ticker': '종목코드',
    'name': '종목명',
    'close': '종가',
    'fluctuation_rate': '등락률(%)',
    'market_cap': '시가총액',
    'volume': '거래량',
    'trading_value': '거래대금',
    'foreign_net': '외인 순매수',
    'inst_net': '기관 순매수',
    'sector': '업종',
    'theme': '테마',
    'presence_index': '주도주 지표',
    'retention_ratio': '수급 보존율(%)',
    'is_pullback': '눌림목 여부',
    'total_score': '총점'
}

def display_formatted_df(df, use_container_width=True):
    """데이터프레임의 컬럼명을 한글로 변경하고 불필요한 열을 제거하여 출력합니다."""
    temp_df = df.copy()
    if 'category' in temp_df.columns:
        temp_df = temp_df.drop(columns=['category'])
    current_map = {k: v for k, v in COLUMN_MAP.items() if k in temp_df.columns}
    temp_df = temp_df.rename(columns=current_map)
    st.dataframe(temp_df, use_container_width=use_container_width)

def display_sector_summary(df):
    """해당 리스트의 업종별 요약과 포함된 종목 리스트를 아래에 출력합니다."""
    if 'sector' in df.columns and not df.empty:
        st.write("---")
        st.subheader("📊 업종별 종목 묶음 보기")
        
        # 업종별로 그룹화하여 종목 수 카운트 및 종목명 결합
        summary = df.groupby('sector').agg({
            'name': ['count', lambda x: ', '.join(x)]
        }).reset_index()
        
        # 멀티인덱스 컬럼 정리
        summary.columns = ['업종', '종목 수', '포함된 종목들']
        summary = summary.sort_values(by='종목 수', ascending=False)
        
        st.dataframe(summary, use_container_width=True)

st.title("📈 일일 주식 수급 & 눌림목 분석 대시보드")
st.markdown("매일 장 마감 후 자동으로 수집된 데이터를 바탕으로 주도 섹터와 추천 종목을 시각화합니다.")

# 데이터 로딩 함수 (캐싱 적용)
@st.cache_data(ttl=600)
def get_analyzed_data():
    analyzer = StockAnalyzer()
    return analyzer.run_analysis()

@st.cache_data(ttl=600)
def get_raw_data():
    try:
        conn = sqlite3.connect("stock_data.db")
        df = pd.read_sql("SELECT * FROM daily_stocks ORDER BY date DESC", conn)
        conn.close()
        return df
    except:
        return pd.DataFrame()

# 데이터 로드
with st.spinner("데이터를 불러오고 있습니다..."):
    df_analyzed = get_analyzed_data()
    df_raw = get_raw_data()

if df_analyzed is None or df_raw.empty:
    st.warning("⚠️ 분석할 데이터가 없습니다. 먼저 `crawler.py`를 실행하여 데이터를 수집해주세요.")
else:
    # ----------------- 사이드바 (날짜 및 세션 선택) -----------------
    st.sidebar.title("🔍 조회 및 분석 옵션")
    
    available_dates = sorted(df_raw['date'].unique().tolist(), reverse=True)
    selected_date = st.sidebar.selectbox("📅 조회할 날짜 선택:", available_dates)
    
    if 'session' in df_raw.columns:
        day_sessions = sorted(df_raw[df_raw['date'] == selected_date]['session'].unique().tolist(), reverse=True)
    else:
        day_sessions = ["데이터 없음 (DB 초기화 필요)"]
        
    selected_session = st.sidebar.selectbox("⏰ 세션 선택:", day_sessions)
    
    st.sidebar.divider()
    st.sidebar.subheader("📈 트렌드 분석 설정")
    trend_count = st.sidebar.slider("추적할 섹터 수 (상위 N개):", min_value=3, max_value=15, value=5)
    
    st.sidebar.divider()
    if st.sidebar.button("🔄 데이터 새로고침"):
        st.cache_data.clear()
        st.rerun()

    # 상단 KPI
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("선택된 날짜", selected_date)
    col2.metric("선택된 세션", selected_session)
    col3.metric("분석 대상 종목 수", f"{len(df_analyzed)}개")
    col4.metric("오늘의 눌림목 포착", f"{len(df_analyzed[df_analyzed['is_pullback'] == True])}개")
    
    st.divider()

    # 탭으로 분리
    tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
        "🏆 종합 추천종목", 
        "🔥 거래대금 Top", 
        "🟢 외인 순매수", 
        "🔴 기관 순매수",
        "📊 섹터별 자금",
        "📈 최근 섹터 흐름",
        "⚖️ 장중 vs 시간외 비교"
    ])
    
    if 'session' in df_raw.columns:
        df_selected = df_raw[(df_raw['date'] == selected_date) & (df_raw['session'] == selected_session)].copy()
    else:
        df_selected = df_raw[df_raw['date'] == selected_date].copy()

    # 탭 1: 종합 추천
    with tab1:
        st.header(f"🏆 {selected_session} 기준 추천 종목 Top 10")
        st.info("""
        💡 **분석 지표 설명**
        - **주도주 지표**: 최근 5거래일 동안 '거래대금' 또는 '수급' 상위권에 얼마나 자주 등장했는지(지속성)를 나타냅니다.
        - **수급 보존율**: 최근 5일간 전체 거래대금 중 외인/기관의 순매수금이 차지하는 비율로, 큰손들의 자금이 얼마나 잔존해 있는지를 뜻합니다.
        """)
        with st.expander("🧐 스코어(점수) 상세 산출 공식 보기"):
            st.markdown("""
            1. **주도주 지표 (10점/회)**: 시장의 관심을 지속적으로 받는 종목에 가점.
            2. **수급 보존율 (%)**: 수치 그대로 점수에 반영하여 자금 유입 강도 측정.
            3. **눌림목 가산점 (+20점)**: 급등 후 거래량 감소와 함께 소폭 조정 중인 '매수 적기' 패턴에 보너스.
            """)
        display_formatted_df(df_analyzed.head(10))

    # 탭 2: 거래대금
    with tab2:
        st.header(f"🔥 거래대금 Top 60 ({selected_session})")
        df_vol = df_selected[df_selected['category'] == 'VOLUME_TOP_60'].copy()
        display_formatted_df(df_vol)
        display_sector_summary(df_vol)

    # 탭 3: 외국인
    with tab3:
        st.header(f"🟢 외국인 순매수 Top 30 ({selected_session})")
        df_for = df_selected[df_selected['category'] == 'FOREIGN_TOP_30'].copy()
        df_for = df_for.sort_values(by='foreign_net', ascending=False)
        display_formatted_df(df_for)
        display_sector_summary(df_for)

    # 탭 4: 기관
    with tab4:
        st.header(f"🔴 기관 순매수 Top 30 ({selected_session})")
        df_inst = df_selected[df_selected['category'] == 'INST_TOP_30'].copy()
        df_inst = df_inst.sort_values(by='inst_net', ascending=False)
        display_formatted_df(df_inst)
        display_sector_summary(df_inst)
        
    # 탭 5: 섹터 요약
    with tab5:
        st.header(f"📊 섹터별 자금 유입 요약 ({selected_session} 기준)")
        df_selected['total_net'] = df_selected['foreign_net'] + df_selected['inst_net']
        sector_grouped = df_selected.groupby('sector').agg({'total_net':'sum', 'trading_value':'sum', 'name':'count'}).reset_index()
        sector_grouped = sector_grouped[sector_grouped['sector'] != '기타'].sort_values('trading_value', ascending=False).head(15)
        st.bar_chart(data=sector_grouped, x='sector', y='trading_value', use_container_width=True)
        sector_disp = sector_grouped.rename(columns={'sector': '업종', 'total_net': '합산 순매수', 'trading_value': '합산 거래대금', 'name': '종목 수'})
        st.dataframe(sector_disp, use_container_width=True)

    # 탭 6: 트렌드
    with tab6:
        st.header(f"📈 최근 섹터 흐름 (상위 {trend_count}개)")
        df_trend = df_raw.copy()
        df_trend['total_net'] = df_trend['foreign_net'] + df_trend['inst_net']
        recent_top_sectors = df_trend[(df_trend['date'] == selected_date) & (df_trend['session'] == selected_session)]
        recent_top_sectors = recent_top_sectors.groupby('sector')['total_net'].sum().sort_values(ascending=False)
        recent_top_sectors = recent_top_sectors[recent_top_sectors.index != '기타'].head(trend_count).index.tolist()
        if recent_top_sectors:
            df_filtered = df_trend[df_trend['sector'].isin(recent_top_sectors)]
            df_filtered['date_session'] = df_filtered['date'] + " " + df_filtered['session']
            trend_pivot = df_filtered.groupby(['date_session', 'sector'])['total_net'].sum().reset_index().pivot(index='date_session', columns='sector', values='total_net').fillna(0)
            st.line_chart(trend_pivot, use_container_width=True)

    # 탭 7: 정규장 vs 시간외 비교
    with tab7:
        st.header(f"⚖️ {selected_date} 정규장 vs 시간외 흐름 비교")
        df_day = df_raw[df_raw['date'] == selected_date].copy()
        if 'session' in df_day.columns:
            sessions = df_day['session'].unique()
            if len(sessions) < 2:
                st.info("비교를 위해서는 정규장(16:00)과 시간외(20:30) 데이터가 모두 수집되어야 합니다.")
            else:
                df_reg = df_day[df_day['session'].str.contains("16:00")].drop_duplicates(['ticker', 'category'])
                df_next = df_day[df_day['session'].str.contains("20:30")].drop_duplicates(['ticker', 'category'])
                compare_df = pd.merge(df_reg[['ticker', 'name', 'close', 'fluctuation_rate']], df_next[['ticker', 'close', 'fluctuation_rate']], on='ticker', suffixes=('_정규', '_시간외')).drop_duplicates('ticker')
                compare_df['등락률_차이'] = compare_df['fluctuation_rate_시간외'] - compare_df['fluctuation_rate_정규']
                compare_df = compare_df.sort_values('등락률_차이', ascending=False)
                compare_disp = compare_df.rename(columns={'ticker': '종목코드', 'name': '종목명', 'close_정규': '종가(정규)', 'fluctuation_rate_정규': '등락률(정규)', 'close_시간외': '종가(시간외)', 'fluctuation_rate_시간외': '등락률(시간외)'})
                st.subheader("🚀 정규장 대비 시간외에서 강세를 보인 종목")
                st.dataframe(compare_disp[compare_disp['등락률_차이'] > 0].head(10), use_container_width=True)
                st.subheader("📉 정규장 대비 시간외에서 약세를 보인 종목")
                st.dataframe(compare_disp[compare_disp['등락률_차이'] < 0].sort_values('등락률_차이').head(10), use_container_width=True)
        else:
            st.warning("DB에 세션 정보가 없습니다.")
