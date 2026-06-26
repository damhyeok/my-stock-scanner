import streamlit as st
import pandas as pd
import sqlite3
import re
import os
import tempfile
import html
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import altair as alt
from analyzer import StockAnalyzer

# 페이지 기본 설정
st.set_page_config(page_title="주식 분석 대시보드", layout="wide", page_icon="📈")

# 컬럼 한글 매핑 딕셔너리
COLUMN_MAP = {
    'date': '날짜',
    'session': '시간',
    'ticker': '종목코드',
    'name': '종목명',
    'close': '현재가',
    'fluctuation_rate': '등락률(%)',
    'market_cap': '시가총액(억)',
    'volume': '거래량',
    'trading_value': '거래대금(억)',
    'foreign_net': '외인 순매수(억)',
    'inst_net': '기관 순매수(억)',
    'sector': '업종',
    'theme': '테마',
    'collected_at_kst': '실제 수집시각(KST)',
    'data_source': '데이터 출처',
    'scheduled_cron': '예약 실행값',
    'presence_index': '주도주 지표',
    'retention_ratio': '수급 보존율(%)',
    'is_pullback': '눌림목 여부',
    'total_score': '총점'
}

def session_sort_key(session):
    """세션명에 포함된 HH:MM 값을 분 단위로 변환해 시간순 정렬에 사용합니다."""
    match = re.search(r'\((\d{1,2}):(\d{2})\)', str(session))
    if not match:
        return -1
    hour, minute = map(int, match.groups())
    return hour * 60 + minute

def week_start_yyyymmdd(date_value):
    selected = datetime.strptime(str(date_value), "%Y%m%d")
    return (selected - timedelta(days=selected.weekday())).strftime("%Y%m%d")

def format_won_to_eok(value):
    """원 단위 금액을 억 단위 정수로 변환합니다."""
    numeric_value = pd.to_numeric(value, errors='coerce')
    if pd.isna(numeric_value):
        numeric_value = 0
    return round(numeric_value / 100_000_000)

def format_kis_flow_to_eok(value):
    """KIS 수급 금액을 억 단위 정수로 변환합니다."""
    numeric_value = pd.to_numeric(value, errors='coerce')
    if pd.isna(numeric_value):
        numeric_value = 0
    return round(numeric_value / 100)

def format_amount_columns(df):
    temp_df = df.copy()
    for col in ['market_cap', 'trading_value']:
        if col in temp_df.columns:
            temp_df[col] = temp_df[col].apply(format_won_to_eok)
    for col in ['foreign_net', 'inst_net']:
        if col in temp_df.columns:
            temp_df[col] = temp_df[col].apply(format_kis_flow_to_eok)
    return temp_df

def display_session_name(session):
    if session == "시간외(20:30)":
        return "NXT 시간외(20:30)"
    return session

def display_formatted_df(df, use_container_width=True, hidden_columns=None):
    """데이터프레임의 컬럼명을 한글로 변경하고 불필요한 열을 제거하여 출력합니다."""
    temp_df = format_amount_columns(df)
    drop_columns = ['category', 'collected_at_kst', 'data_source', 'scheduled_cron']
    if hidden_columns:
        drop_columns.extend(hidden_columns)
    existing_drop_columns = [col for col in drop_columns if col in temp_df.columns]
    if existing_drop_columns:
        temp_df = temp_df.drop(columns=existing_drop_columns)
    current_map = {k: v for k, v in COLUMN_MAP.items() if k in temp_df.columns}
    temp_df = temp_df.rename(columns=current_map)
    st.dataframe(temp_df, use_container_width=use_container_width)

def display_wrapped_table(df):
    escaped_df = df.copy()
    for col in escaped_df.columns:
        escaped_df[col] = escaped_df[col].map(lambda value: html.escape(str(value)))
    table_html = escaped_df.to_html(index=False, escape=False)
    st.markdown(
        """
        <style>
        .wrapped-table table {
            width: 100%;
            border-collapse: collapse;
            font-size: 0.92rem;
        }
        .wrapped-table th, .wrapped-table td {
            border: 1px solid rgba(128, 128, 128, 0.28);
            padding: 0.45rem 0.55rem;
            vertical-align: top;
            white-space: normal;
            word-break: keep-all;
            overflow-wrap: anywhere;
        }
        .wrapped-table th {
            font-weight: 700;
            background: rgba(128, 128, 128, 0.10);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(f'<div class="wrapped-table">{table_html}</div>', unsafe_allow_html=True)

def to_csv_bytes(df):
    return df.to_csv(index=False, encoding='utf-8-sig').encode('utf-8-sig')

def safe_filename(value):
    return re.sub(r'[^0-9A-Za-z가-힣_-]+', '_', str(value)).strip('_')

def get_config_value(key, default=""):
    try:
        return st.secrets.get(key, os.environ.get(key, default))
    except Exception:
        return os.environ.get(key, default)

@st.cache_data(ttl=60, show_spinner=False)
def get_database_path():
    repo = get_config_value("GITHUB_REPOSITORY", "damhyeok/my-stock-scanner")
    branch = get_config_value("GITHUB_BRANCH", "main")
    db_url = get_config_value(
        "STOCK_DB_URL",
        f"https://raw.githubusercontent.com/{repo}/{branch}/stock_data.db"
    )
    local_db_path = "stock_data.db"

    try:
        response = requests.get(db_url, timeout=20)
        response.raise_for_status()
        if not response.content.startswith(b"SQLite format 3"):
            raise ValueError("Downloaded file is not a SQLite database.")

        remote_db_path = os.path.join(tempfile.gettempdir(), "stock_data_latest.db")
        with open(remote_db_path, "wb") as db_file:
            db_file.write(response.content)
        return remote_db_path, "GitHub 최신 DB"
    except Exception:
        return local_db_path, "로컬 DB"

def trigger_github_workflow(market_strength_mode="manual", requested_at_kst=None):
    token = get_config_value("GITHUB_ACTIONS_TOKEN")
    repo = get_config_value("GITHUB_REPOSITORY", "damhyeok/my-stock-scanner")
    workflow_file = get_config_value("GITHUB_WORKFLOW_FILE", "main.yml")
    branch = get_config_value("GITHUB_BRANCH", "main")

    if not token:
        return False, "웹페이지에서 실행하려면 `GITHUB_ACTIONS_TOKEN` 설정이 필요합니다."

    url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow_file}/dispatches"
    inputs = {
        "market_strength_mode": market_strength_mode,
        "requested_at_kst": requested_at_kst or datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="minutes"),
    }
    response = requests.post(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={"ref": branch, "inputs": inputs},
        timeout=15,
    )

    if response.status_code == 204:
        return True, "GitHub Actions 실행을 요청했습니다. 완료 후 새로고침하면 최신 데이터가 보입니다."
    return False, f"GitHub Actions 실행 요청 실패: {response.status_code} {response.text}"

def get_latest_workflow_run():
    token = get_config_value("GITHUB_ACTIONS_TOKEN")
    repo = get_config_value("GITHUB_REPOSITORY", "damhyeok/my-stock-scanner")
    workflow_file = get_config_value("GITHUB_WORKFLOW_FILE", "main.yml")
    branch = get_config_value("GITHUB_BRANCH", "main")

    if not token:
        return None, "웹페이지에서 실행 상태를 확인하려면 `GITHUB_ACTIONS_TOKEN` 설정이 필요합니다."

    url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow_file}/runs"
    response = requests.get(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        params={"branch": branch, "per_page": 1},
        timeout=15,
    )

    if response.status_code != 200:
        return None, f"GitHub Actions 상태 확인 실패: {response.status_code} {response.text}"

    runs = response.json().get("workflow_runs", [])
    if not runs:
        return None, "최근 실행 내역이 없습니다."
    return runs[0], ""

def display_workflow_run_status(container):
    run, error = get_latest_workflow_run()
    if error:
        container.info(error)
        return

    status = run.get("status", "")
    conclusion = run.get("conclusion") or ""
    run_number = run.get("run_number", "-")
    html_url = run.get("html_url", "")
    updated_at = run.get("updated_at", "")

    status_map = {
        "queued": ("대기중", 10),
        "in_progress": ("실행중", 60),
        "completed": ("완료", 100),
    }
    label, progress = status_map.get(status, (status or "확인중", 30))
    if status == "completed" and conclusion and conclusion != "success":
        label = f"완료({conclusion})"

    container.progress(progress)
    container.caption(f"최근 실행 #{run_number}: {label} · 마지막 갱신 {updated_at}")
    if html_url:
        container.markdown(f"[GitHub Actions에서 자세히 보기]({html_url})")

def market_strength_status(score):
    if pd.isna(score):
        return "데이터 없음"
    if score >= 85:
        return "매우 좋음"
    if score >= 70:
        return "좋음"
    if score >= 55:
        return "보통"
    return "위험"

def display_sector_summary(df, title="📊 업종별 종목 묶음 보기", show_rate=False):
    """해당 리스트의 업종별 요약과 포함된 종목 리스트를 아래에 출력합니다."""
    if 'sector' in df.columns and not df.empty:
        st.write("---")
        st.subheader(title)
        sector_df = df.drop_duplicates(subset=['ticker']).copy()
        if show_rate and 'fluctuation_rate' in sector_df.columns:
            rates = pd.to_numeric(sector_df['fluctuation_rate'], errors='coerce')
            sector_df['summary_name'] = sector_df['name'].astype(str) + rates.map(
                lambda x: f" ({x:+.2f}%)" if pd.notna(x) else ""
            )
            name_column = 'summary_name'
        else:
            name_column = 'name'
        
        # 업종별로 그룹화하여 종목 수 카운트 및 종목명 결합
        summary = sector_df.groupby('sector').agg({
            'ticker': 'nunique',
            name_column: lambda x: ', '.join(dict.fromkeys(x.astype(str)))
        }).reset_index()
        
        # 멀티인덱스 컬럼 정리
        summary.columns = ['업종', '종목 수', '포함된 종목들']
        summary = summary.sort_values(by='종목 수', ascending=False)
        
        display_wrapped_table(summary)

st.title("📈 일일 주식 수급 & 눌림목 분석 대시보드")
st.markdown("매일 장 마감 후 자동으로 수집된 데이터를 바탕으로 주도 섹터와 추천 종목을 시각화합니다.")

# 데이터 로딩 함수 (캐싱 적용)
@st.cache_data(ttl=60)
def get_analyzed_data():
    db_path, _ = get_database_path()
    analyzer = StockAnalyzer(db_path=db_path)
    return analyzer.run_analysis()

@st.cache_data(ttl=60)
def get_raw_data():
    try:
        db_path, _ = get_database_path()
        conn = sqlite3.connect(db_path)
        df = pd.read_sql("SELECT * FROM daily_stocks WHERE session NOT LIKE '%시간외%' ORDER BY date DESC", conn)
        conn.close()
        return df
    except:
        return pd.DataFrame()

@st.cache_data(ttl=60)
def get_news_data():
    try:
        db_path, _ = get_database_path()
        conn = sqlite3.connect(db_path)
        df = pd.read_sql("SELECT * FROM stock_news ORDER BY date DESC, published_at DESC", conn)
        conn.close()
        return df
    except:
        return pd.DataFrame()

@st.cache_data(ttl=60)
def get_market_strength_data():
    try:
        db_path, _ = get_database_path()
        conn = sqlite3.connect(db_path)
        df = pd.read_sql("SELECT * FROM market_strength_snapshots ORDER BY trade_date DESC, snapshot_time ASC", conn)
        conn.close()
        if 'analysis_type' not in df.columns:
            df['analysis_type'] = 'closing'
        if 'analysis_label' not in df.columns:
            df['analysis_label'] = df['analysis_type'].map({
                'morning': '오전 흐름',
                'closing': '종가 흐름',
                'manual': '수동 흐름',
            }).fillna(df['analysis_type'])
        return df
    except:
        return pd.DataFrame()

# 데이터 로드
with st.spinner("데이터를 불러오고 있습니다..."):
    df_analyzed = get_analyzed_data()
    df_raw = get_raw_data()
    df_news = get_news_data()
    df_market_strength = get_market_strength_data()

if df_analyzed is None or df_raw.empty:
    st.warning("⚠️ 분석할 데이터가 없습니다. 먼저 `crawler.py`를 실행하여 데이터를 수집해주세요.")
else:
    # ----------------- 사이드바 (날짜 및 시간 선택) -----------------
    st.sidebar.title("🔍 조회 및 분석 옵션")
    
    available_dates = sorted(df_raw['date'].unique().tolist(), reverse=True)
    selected_date = st.sidebar.selectbox("📅 조회할 날짜 선택:", available_dates)
    
    if 'session' in df_raw.columns:
        day_sessions = sorted(
            df_raw[df_raw['date'] == selected_date]['session'].unique().tolist(),
            key=session_sort_key,
            reverse=True
        )
    else:
        day_sessions = ["데이터 없음 (DB 초기화 필요)"]
        
    selected_session = st.sidebar.selectbox("⏰ 시간 선택:", day_sessions)

    selected_session_df = df_raw[
        (df_raw['date'] == selected_date) & (df_raw['session'] == selected_session)
    ].copy()
    selected_date_df = df_raw[df_raw['date'] == selected_date].copy()

    st.sidebar.divider()
    st.sidebar.subheader("📥 데이터 다운로드")
    st.sidebar.download_button(
        "선택 시간 CSV",
        data=to_csv_bytes(selected_session_df),
        file_name=f"stock_data_{selected_date}_{safe_filename(selected_session)}.csv",
        mime="text/csv"
    )
    st.sidebar.download_button(
        "선택 날짜 CSV",
        data=to_csv_bytes(selected_date_df),
        file_name=f"stock_data_{selected_date}.csv",
        mime="text/csv"
    )
    st.sidebar.download_button(
        "전체 데이터 CSV",
        data=to_csv_bytes(df_raw),
        file_name="stock_data_all.csv",
        mime="text/csv"
    )
    
    st.sidebar.divider()
    st.sidebar.subheader("📈 트렌드 분석 설정")
    trend_count = st.sidebar.slider("추적할 섹터 수 (상위 N개):", min_value=3, max_value=15, value=5)
    
    st.sidebar.divider()
    st.sidebar.subheader("🚀 수동 분석 실행")
    st.sidebar.caption("현재 시각 기준으로 GitHub Actions 자동화를 실행합니다.")
    if st.sidebar.button("지금 분석 실행"):
        with st.sidebar.spinner("GitHub Actions 실행 요청 중..."):
            ok, message = trigger_github_workflow()
        if ok:
            st.sidebar.success(message)
        else:
            st.sidebar.error(message)
    st.sidebar.caption("완료 여부는 아래 최근 실행 상태에서 확인할 수 있습니다.")
    display_workflow_run_status(st.sidebar)

    st.sidebar.divider()
    if st.sidebar.button("🔄 데이터 새로고침"):
        st.cache_data.clear()
        st.rerun()

    # 상단 KPI
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("선택된 날짜", selected_date)
    selected_session_label = display_session_name(selected_session)
    col2.metric("선택된 시간", selected_session_label)
    col3.metric("분석 대상 종목 수", f"{len(df_analyzed)}개")
    col4.metric("오늘의 눌림목 포착", f"{len(df_analyzed[df_analyzed['is_pullback'] == True])}개")
    
    st.divider()

    # 탭으로 분리
    tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9 = st.tabs([
        "🏆 종합 추천종목", 
        "🔥 거래대금 Top", 
        "🟢 외인 순매수", 
        "🔴 기관 순매수",
        "📊 섹터별 자금",
        "📈 최근 섹터 흐름",
        "⚡ 실시간 변화(직전 대비)",
        "📰 뉴스 이슈 종목",
        "🌡️ 시장 강도 분석"
    ])
    
    if 'session' in df_raw.columns:
        df_selected = df_raw[(df_raw['date'] == selected_date) & (df_raw['session'] == selected_session)].copy()
    else:
        df_selected = df_raw[df_raw['date'] == selected_date].copy()

    # 탭 1: 종합 추천
    with tab1:
        st.header(f"🏆 {selected_session_label} 기준 추천 종목 Top 10")
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

    # 탭 2, 3, 4: 각 카테고리별 데이터
    with tab2:
        st.header(f"🔥 거래대금 Top 60 ({selected_session_label})")
        df_vol = df_selected[df_selected['category'] == 'VOLUME_TOP_60'].copy()
        df_vol = df_vol.sort_values(by='trading_value', ascending=False)
        display_formatted_df(df_vol, hidden_columns=['date', 'session', 'ticker', 'volume', 'theme'])
        display_sector_summary(df_vol)

        rising_df = df_vol[pd.to_numeric(df_vol['fluctuation_rate'], errors='coerce') > 0].copy()
        if rising_df.empty:
            st.write("---")
            st.subheader("📈 상승 종목 업종별 묶음 보기")
            st.info("등락률이 양수인 거래대금 Top 60 종목이 없습니다.")
        else:
            display_sector_summary(rising_df, title="📈 상승 종목 업종별 묶음 보기", show_rate=True)

        both_buy_df = df_vol[
            (pd.to_numeric(df_vol['foreign_net'], errors='coerce') > 0) &
            (pd.to_numeric(df_vol['inst_net'], errors='coerce') > 0)
        ].copy()
        st.write("---")
        st.subheader("🤝 외인·기관 동시 순매수 종목")
        if both_buy_df.empty:
            st.info("외인과 기관이 모두 순매수한 거래대금 Top 60 종목이 없습니다.")
        else:
            both_buy_df = both_buy_df.sort_values(
                by=['foreign_net', 'inst_net', 'trading_value'],
                ascending=False
            )
            display_formatted_df(both_buy_df, hidden_columns=['date', 'session', 'ticker', 'volume', 'theme'])

    with tab3:
        st.header(f"🟢 외국인 순매수 Top 30 ({selected_session_label})")
        df_for = df_selected[df_selected['category'] == 'FOREIGN_TOP_30'].copy()
        df_for = df_for.sort_values(by='foreign_net', ascending=False)
        display_formatted_df(df_for, hidden_columns=['date', 'session', 'ticker', 'volume', 'theme'])
        display_sector_summary(df_for)

    with tab4:
        st.header(f"🔴 기관 순매수 Top 30 ({selected_session_label})")
        df_inst = df_selected[df_selected['category'] == 'INST_TOP_30'].copy()
        df_inst = df_inst.sort_values(by='inst_net', ascending=False)
        display_formatted_df(df_inst, hidden_columns=['date', 'session', 'ticker', 'volume', 'theme'])
        display_sector_summary(df_inst)
        
    # 탭 5: 섹터 요약
    with tab5:
        st.header(f"📊 섹터별 자금 유입 요약 ({selected_session_label} 기준)")
        sector_base = df_selected[df_selected['category'] == 'VOLUME_TOP_60'].drop_duplicates(subset=['ticker']).copy()
        sector_base['total_net'] = sector_base['foreign_net'] + sector_base['inst_net']
        
        sector_grouped = sector_base.groupby('sector').agg(
            total_net=('total_net', 'sum'),
            trading_value=('trading_value', 'sum'),
            stock_count=('ticker', 'nunique'),
            included_stocks=('name', lambda x: ', '.join(dict.fromkeys(x.astype(str))))
        ).reset_index()
        
        sector_grouped = sector_grouped[sector_grouped['sector'] != '기타'].sort_values('trading_value', ascending=False).head(15)
        sector_chart = sector_grouped.copy()
        sector_chart['trading_value_eok'] = sector_chart['trading_value'].apply(format_won_to_eok)
        sector_chart['total_net_eok'] = sector_chart['total_net'].apply(format_kis_flow_to_eok)
        sector_chart['flow_type'] = sector_chart['total_net_eok'].apply(lambda value: '순매수' if value >= 0 else '순매도')
        sector_bar = alt.Chart(sector_chart).mark_bar(cornerRadiusEnd=3).encode(
            y=alt.Y('sector:N', sort='-x', title='업종'),
            x=alt.X(
                'trading_value_eok:Q',
                title='합산 거래대금(억, 압축 스케일)',
                scale=alt.Scale(type='sqrt')
            ),
            color=alt.Color(
                'flow_type:N',
                title='수급',
                scale=alt.Scale(domain=['순매수', '순매도'], range=['#2f80ed', '#eb5757'])
            ),
            tooltip=[
                alt.Tooltip('sector:N', title='업종'),
                alt.Tooltip('trading_value_eok:Q', title='거래대금(억)', format=',.0f'),
                alt.Tooltip('total_net_eok:Q', title='합산 순매수(억)', format=',.0f'),
                alt.Tooltip('stock_count:Q', title='종목 수'),
            ],
        ).properties(height=420)
        st.altair_chart(sector_bar, use_container_width=True)
        st.caption("거래대금 차이가 너무 큰 섹터 때문에 다른 섹터가 눌려 보이지 않도록 그래프 축만 압축해서 표시합니다. 정확한 금액은 아래 표에서 확인할 수 있습니다.")
        sector_disp = sector_grouped.rename(columns={'sector': '업종', 'total_net': '합산 순매수', 'trading_value': '합산 거래대금', 'stock_count': '종목 수', 'included_stocks': '포함된 종목들'})
        sector_disp['합산 순매수'] = sector_disp['합산 순매수'].apply(format_kis_flow_to_eok)
        sector_disp['합산 거래대금'] = sector_disp['합산 거래대금'].apply(format_won_to_eok)
        sector_disp = sector_disp.rename(columns={
            '합산 순매수': '합산 순매수(억)',
            '합산 거래대금': '합산 거래대금(억)'
        })
        st.dataframe(sector_disp, use_container_width=True)

    # 탭 6: 트렌드
    with tab6:
        st.header(f"📈 최근 섹터 흐름 (상위 {trend_count}개)")
        week_start = week_start_yyyymmdd(selected_date)
        df_trend = df_raw[
            (df_raw['date'] >= week_start) &
            (df_raw['date'] <= selected_date) &
            (df_raw['session'] == '정규장(16:00)') &
            (df_raw['category'] == 'VOLUME_TOP_60')
        ].drop_duplicates(subset=['date', 'session', 'ticker']).copy()
        if df_trend.empty:
            st.info("이번주 정규장 섹터 흐름 데이터가 없습니다.")
        else:
            df_trend['total_net'] = pd.to_numeric(df_trend['foreign_net'], errors='coerce').fillna(0) + pd.to_numeric(df_trend['inst_net'], errors='coerce').fillna(0)
            df_trend['trading_value'] = pd.to_numeric(df_trend['trading_value'], errors='coerce').fillna(0)
            weekly_sector_rank = (
                df_trend[df_trend['sector'] != '기타']
                .groupby('sector')['trading_value']
                .sum()
                .sort_values(ascending=False)
                .head(5)
                .index
                .tolist()
            )
            if not weekly_sector_rank:
                st.info("이번주 정규장 기준으로 표시할 섹터가 없습니다.")
            else:
                trend_grouped = (
                    df_trend[df_trend['sector'].isin(weekly_sector_rank)]
                    .groupby(['date', 'sector'])
                    .agg(
                        total_net=('total_net', 'sum'),
                        trading_value=('trading_value', 'sum'),
                        stock_count=('ticker', 'nunique'),
                    )
                    .reset_index()
                )
                trend_grouped['total_net_eok'] = trend_grouped['total_net'].apply(format_kis_flow_to_eok)
                trend_grouped['trading_value_eok'] = trend_grouped['trading_value'].apply(format_won_to_eok)
                trend_grouped['date_label'] = pd.to_datetime(trend_grouped['date'], format='%Y%m%d').dt.strftime('%m/%d')

                st.caption(f"{week_start}부터 {selected_date}까지의 정규장(16:00) 거래대금 누적 상위 5개 섹터를 고정해서 흐름을 표시합니다.")
                trend_line = alt.Chart(trend_grouped).mark_line(point=True).encode(
                    x=alt.X('date_label:N', title='날짜'),
                    y=alt.Y('total_net_eok:Q', title='외인+기관 순매수(억)'),
                    color=alt.Color('sector:N', title='업종'),
                    tooltip=[
                        alt.Tooltip('date_label:N', title='날짜'),
                        alt.Tooltip('sector:N', title='업종'),
                        alt.Tooltip('total_net_eok:Q', title='순매수(억)', format=',.0f'),
                        alt.Tooltip('trading_value_eok:Q', title='거래대금(억)', format=',.0f'),
                        alt.Tooltip('stock_count:Q', title='종목 수'),
                    ],
                ).properties(height=420)
                st.altair_chart(trend_line, use_container_width=True)

                latest_flow = trend_grouped[trend_grouped['date'] == trend_grouped['date'].max()].copy()
                latest_flow = latest_flow.sort_values('trading_value', ascending=False)
                latest_flow_disp = latest_flow[['sector', 'total_net_eok', 'trading_value_eok', 'stock_count']].rename(columns={
                    'sector': '업종',
                    'total_net_eok': '순매수(억)',
                    'trading_value_eok': '거래대금(억)',
                    'stock_count': '종목 수',
                })
                st.dataframe(latest_flow_disp, use_container_width=True)

    # 탭 7: 직전 시간 대비 변화
    with tab7:
        st.header(f"⚡ {selected_session_label} 기준 직전 시간 대비 변화")
        if 'session' in df_raw.columns and selected_session in day_sessions:
            current_session_idx = day_sessions.index(selected_session)
            if current_session_idx < len(day_sessions) - 1:
                prev_session = day_sessions[current_session_idx + 1]
                st.markdown(f"**비교 대상:** `{prev_session}` ➡️ `{selected_session}`")
                df_curr = df_raw[(df_raw['date'] == selected_date) & (df_raw['session'] == selected_session) & (df_raw['category'] == 'VOLUME_TOP_60')]
                df_prev = df_raw[(df_raw['date'] == selected_date) & (df_raw['session'] == prev_session) & (df_raw['category'] == 'VOLUME_TOP_60')]
                curr_tickers = set(df_curr['ticker']); prev_tickers = set(df_prev['ticker'])
                
                new_entries = curr_tickers - prev_tickers
                st.subheader("🚀 거래대금 Top 60 신규 진입 종목")
                if new_entries:
                    display_formatted_df(df_curr[df_curr['ticker'].isin(new_entries)].sort_values('trading_value', ascending=False))
                else: st.info("신규 진입 종목이 없습니다.")
                
                common_tickers = curr_tickers.intersection(prev_tickers)
                if common_tickers:
                    merged = pd.merge(df_curr[df_curr['ticker'].isin(common_tickers)][['ticker', 'name', 'trading_value', 'sector']], df_prev[df_prev['ticker'].isin(common_tickers)][['ticker', 'trading_value']], on='ticker', suffixes=('_현재', '_이전'))
                    merged['거래대금 급증률(%)'] = ((merged['trading_value_현재'] - merged['trading_value_이전']) / merged['trading_value_이전'] * 100).round(2)
                    st.subheader("🔥 이전 시간 대비 거래대금 급증 종목 Top 10")
                    merged_disp = merged.sort_values('거래대금 급증률(%)', ascending=False).head(10).reset_index(drop=True)
                    merged_disp['trading_value_현재'] = merged_disp['trading_value_현재'].apply(format_won_to_eok)
                    merged_disp['trading_value_이전'] = merged_disp['trading_value_이전'].apply(format_won_to_eok)
                    merged_disp = merged_disp.rename(columns={
                        'trading_value_현재': '거래대금 현재(억)',
                        'trading_value_이전': '거래대금 이전(억)'
                    })
                    st.dataframe(merged_disp, use_container_width=True)
            else: st.info("비교할 이전 시간 데이터가 없습니다.")

    with tab8:
        st.header(f"📰 뉴스 이슈 종목 ({selected_session_label})")
        if df_news.empty:
            st.info("수집된 뉴스 이슈 데이터가 없습니다. 다음 자동 실행 이후 표시됩니다.")
        else:
            news_selected = df_news[
                (df_news['date'] == selected_date) &
                (df_news['session'] == selected_session)
            ].copy()

            if news_selected.empty:
                st.info("선택한 날짜/시간에 수집된 뉴스 이슈 데이터가 없습니다.")
            else:
                summary = news_selected.groupby(['ticker', 'name', 'sector']).agg(
                    news_score=('sentiment_score', 'sum'),
                    news_count=('title', 'count'),
                    positive_count=('sentiment', lambda x: int((x == '긍정').sum())),
                    negative_count=('sentiment', lambda x: int((x == '부정').sum())),
                    neutral_count=('sentiment', lambda x: int((x == '중립').sum())),
                    keywords=('keywords', lambda x: ', '.join(dict.fromkeys(
                        keyword.strip()
                        for value in x.dropna().astype(str)
                        for keyword in value.split(',')
                        if keyword.strip()
                    )))
                ).reset_index()

                summary = summary.sort_values(
                    by=['news_score', 'positive_count', 'negative_count'],
                    ascending=[False, False, True]
                )
                summary_disp = summary.rename(columns={
                    'ticker': '종목코드',
                    'name': '종목명',
                    'sector': '업종',
                    'news_score': '뉴스 점수',
                    'news_count': '뉴스 수',
                    'positive_count': '긍정',
                    'negative_count': '부정',
                    'neutral_count': '중립',
                    'keywords': '주요 키워드'
                })
                st.dataframe(summary_disp.drop(columns=['종목코드']), use_container_width=True)

                st.write("---")
                st.subheader("종목별 뉴스 펼쳐보기")
                for _, row in summary.iterrows():
                    stock_news = news_selected[news_selected['ticker'] == row['ticker']].head(5)
                    label = (
                        f"{row['name']} | 점수 {row['news_score']} | "
                        f"긍정 {row['positive_count']} / 부정 {row['negative_count']} / 중립 {row['neutral_count']}"
                    )
                    with st.expander(label):
                        for idx, (_, news) in enumerate(stock_news.iterrows(), start=1):
                            published_at = news.get('published_at', '')
                            source = news.get('source', '')
                            sentiment = news.get('sentiment', '중립')
                            title = news.get('title', '')
                            link = news.get('link', '')
                            if link:
                                st.markdown(f"**{idx}. [{title}]({link})**")
                            else:
                                st.markdown(f"**{idx}. {title}**")
                            st.caption(f"{sentiment} | {source} | {published_at}")

    with tab9:
        st.header(f"🌡️ 시장 강도 분석 ({selected_date})")
        manual_col, refresh_col = st.columns([1, 3])
        if manual_col.button("지금 기준 시장강도 분석 실행"):
            requested_at = datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="minutes")
            with st.spinner("GitHub Actions 시장강도 분석 요청 중..."):
                ok, message = trigger_github_workflow(
                    market_strength_mode="manual",
                    requested_at_kst=requested_at,
                )
            if ok:
                st.success(f"{message} 기준시각: {requested_at}")
            else:
                st.error(message)
        refresh_col.caption("수동 실행은 버튼을 누른 시각, 15분 전, 30분 전의 시장강도 흐름을 저장합니다.")
        if df_market_strength.empty:
            st.info("시장강도 분석 데이터가 없습니다. 15:40 이후 자동 실행 또는 수동 실행 후 표시됩니다.")
        else:
            strength_selected = df_market_strength[df_market_strength['trade_date'] == selected_date].copy()
            if strength_selected.empty:
                st.info("선택한 날짜의 시장강도 분석 데이터가 없습니다.")
            else:
                group_options = (
                    strength_selected[['analysis_type', 'analysis_label']]
                    .drop_duplicates()
                )
                group_order = {'morning': 1, 'closing': 2, 'manual': 3}
                group_options['group_order'] = group_options['analysis_type'].map(group_order).fillna(99)
                group_options = group_options.sort_values('group_order')
                selected_group_label = st.selectbox(
                    "시장강도 흐름 선택",
                    group_options['analysis_label'].tolist(),
                    index=len(group_options) - 1,
                )
                selected_group_type = group_options.loc[
                    group_options['analysis_label'] == selected_group_label,
                    'analysis_type'
                ].iloc[0]
                strength_selected = strength_selected[
                    strength_selected['analysis_type'] == selected_group_type
                ].copy()
                strength_selected = strength_selected.sort_values('snapshot_time')
                latest_row = strength_selected.iloc[-1]
                total_score = pd.to_numeric(latest_row.get('market_strength_score'), errors='coerce')
                status_text = market_strength_status(total_score)

                score_col, status_col = st.columns(2)
                score_col.metric("시장강도", f"{int(total_score)}점" if pd.notna(total_score) else "-")
                status_col.metric("상태", status_text)

                interpretation = latest_row.get('interpretation_text', '')
                if interpretation:
                    st.info(interpretation)

                card1, card2, card3 = st.columns(3)
                card1.metric("베이시스 점수", f"{int(latest_row.get('basis_score', 0))} / 35")
                card2.metric("프로그램매매 점수", f"{int(latest_row.get('program_score', 0))} / 35")
                card3.metric("코스피200 선물 추세 점수", f"{int(latest_row.get('futures_trend_score', 0))} / 30")

                table_df = strength_selected.copy()
                table_df['변화 방향'] = ''
                for idx in range(len(table_df)):
                    if idx == 0:
                        table_df.iloc[idx, table_df.columns.get_loc('변화 방향')] = '기준'
                    else:
                        prev = table_df.iloc[idx - 1]
                        curr = table_df.iloc[idx]
                        directions = []
                        if pd.to_numeric(curr['basis'], errors='coerce') > pd.to_numeric(prev['basis'], errors='coerce'):
                            directions.append('베이시스 확대')
                        elif pd.to_numeric(curr['basis'], errors='coerce') < pd.to_numeric(prev['basis'], errors='coerce'):
                            directions.append('베이시스 축소')
                        if pd.to_numeric(curr['program_net'], errors='coerce') > pd.to_numeric(prev['program_net'], errors='coerce'):
                            directions.append('프로그램 개선')
                        elif pd.to_numeric(curr['program_net'], errors='coerce') < pd.to_numeric(prev['program_net'], errors='coerce'):
                            directions.append('프로그램 약화')
                        if pd.to_numeric(curr['kospi200_futures_price'], errors='coerce') > pd.to_numeric(prev['kospi200_futures_price'], errors='coerce'):
                            directions.append('선물 상승')
                        elif pd.to_numeric(curr['kospi200_futures_price'], errors='coerce') < pd.to_numeric(prev['kospi200_futures_price'], errors='coerce'):
                            directions.append('선물 하락')
                        table_df.iloc[idx, table_df.columns.get_loc('변화 방향')] = ', '.join(directions) if directions else '보합'

                display_df = table_df[[
                    'snapshot_time',
                    'basis',
                    'program_net',
                    'arbitrage_net',
                    'non_arbitrage_net',
                    'kospi200_futures_price',
                    '변화 방향'
                ]].rename(columns={
                    'snapshot_time': '시간',
                    'basis': '베이시스',
                    'program_net': '프로그램 순매수',
                    'arbitrage_net': '차익 순매수',
                    'non_arbitrage_net': '비차익 순매수',
                    'kospi200_futures_price': '코스피200 선물'
                })
                for col in ['프로그램 순매수', '차익 순매수', '비차익 순매수']:
                    display_df[col] = pd.to_numeric(display_df[col], errors='coerce').round(0).astype('Int64')
                for col in ['베이시스', '코스피200 선물']:
                    display_df[col] = pd.to_numeric(display_df[col], errors='coerce').round(2)
                st.subheader("시간별 시장강도 흐름")
                st.dataframe(display_df, use_container_width=True)
