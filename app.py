import streamlit as st
import pandas as pd
import sqlite3
import re
import os
import tempfile
import html
import json
import shutil
import requests
import hashlib
import hmac
import time
import uuid
from datetime import datetime, timedelta, timezone
import altair as alt
import plotly.express as px
import plotly.graph_objects as go
from analyzer import StockAnalyzer
from model_data_collector import ModelDataCollector
from model_features import ModelFeatureBuilder
from model_labels import ModelLabelBuilder
from model_schema import init_model_tables
from stock_chart_analyzer import StockChartAnalyzer

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

def calculate_consecutive_buy_streaks(
    raw_data, current_stocks, selected_date, selected_session, mode, min_days=2
):
    if current_stocks.empty:
        return pd.DataFrame()

    history = raw_data[
        (raw_data['date'].astype(str) <= str(selected_date))
        & (raw_data['session'] == selected_session)
    ].copy()
    if history.empty:
        return pd.DataFrame()

    for column in ['foreign_net', 'inst_net', 'trading_value']:
        history[column] = pd.to_numeric(history[column], errors='coerce').fillna(0)
    history['ticker'] = history['ticker'].astype(str).str.zfill(6)
    history = history.sort_values(['date', 'ticker', 'category']).drop_duplicates(
        ['date', 'ticker'], keep='first'
    )
    trading_dates = sorted(history['date'].astype(str).unique().tolist(), reverse=True)

    current = current_stocks.copy()
    current['ticker'] = current['ticker'].astype(str).str.zfill(6)
    for column in ['foreign_net', 'inst_net', 'trading_value']:
        current[column] = pd.to_numeric(current[column], errors='coerce').fillna(0)

    rows = []
    for stock_row in current.drop_duplicates('ticker').itertuples(index=False):
        ticker_history = history[history['ticker'] == stock_row.ticker].set_index(
            history[history['ticker'] == stock_row.ticker]['date'].astype(str)
        )
        streak = 0
        streak_start = None
        for trade_date in trading_dates:
            if trade_date not in ticker_history.index:
                break
            record = ticker_history.loc[trade_date]
            if isinstance(record, pd.DataFrame):
                record = record.iloc[0]
            foreign_positive = record['foreign_net'] > 0
            institution_positive = record['inst_net'] > 0
            condition = {
                'both': foreign_positive and institution_positive,
                'foreign': foreign_positive,
                'institution': institution_positive,
            }[mode]
            if not condition:
                break
            streak += 1
            streak_start = trade_date

        if streak >= min_days:
            rows.append({
                'name': stock_row.name,
                'sector': stock_row.sector,
                'streak_days': streak,
                'streak_start': streak_start,
                'foreign_net': stock_row.foreign_net,
                'inst_net': stock_row.inst_net,
                'total_net': stock_row.foreign_net + stock_row.inst_net,
                'trading_value': stock_row.trading_value,
            })

    result = pd.DataFrame(rows)
    if result.empty:
        return result
    sort_value = {'both': 'total_net', 'foreign': 'foreign_net', 'institution': 'inst_net'}[mode]
    return result.sort_values(['streak_days', sort_value], ascending=[False, False])

def display_consecutive_buy_table(streaks, mode):
    if streaks.empty:
        st.info("현재 종목 중 2거래일 이상 연속 순매수한 종목이 없습니다.")
        return

    common_columns = ['name', 'sector', 'streak_days', 'streak_start']
    amount_columns = {
        'both': ['foreign_net', 'inst_net', 'total_net'],
        'foreign': ['foreign_net'],
        'institution': ['inst_net'],
    }[mode]
    display = streaks[common_columns + amount_columns + ['trading_value']].copy()
    for column in amount_columns:
        display[column] = display[column].apply(format_kis_flow_to_eok)
    display['trading_value'] = display['trading_value'].apply(format_won_to_eok)
    display = display.rename(columns={
        'name': '종목명',
        'sector': '업종',
        'streak_days': '연속 순매수(거래일)',
        'streak_start': '연속 시작일',
        'foreign_net': '현재 외국인(억)',
        'inst_net': '현재 기관(억)',
        'total_net': '현재 합산(억)',
        'trading_value': '현재 거래대금(억)',
    })
    st.dataframe(display, use_container_width=True, hide_index=True)

def to_csv_bytes(df):
    return df.to_csv(index=False, encoding='utf-8-sig').encode('utf-8-sig')

def safe_filename(value):
    return re.sub(r'[^0-9A-Za-z가-힣_-]+', '_', str(value)).strip('_')

def get_config_value(key, default=""):
    try:
        return st.secrets.get(key, os.environ.get(key, default))
    except Exception:
        return os.environ.get(key, default)

def configure_model_runtime_secrets():
    for key in ("KIS_APP_KEY", "KIS_APP_SECRET"):
        value = get_config_value(key)
        if value:
            os.environ[key] = value

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

def get_chart_model_database_path(base_db_path):
    model_db_path = os.path.join(tempfile.gettempdir(), "stock_chart_model_runtime.db")
    if not os.path.exists(model_db_path):
        try:
            shutil.copy2(base_db_path, model_db_path)
        except Exception:
            model_db_path = base_db_path
    init_model_tables(model_db_path)
    return model_db_path

def trigger_github_workflow(run_mode="full", market_strength_mode="manual", requested_at_kst=None):
    token = get_config_value("GITHUB_ACTIONS_TOKEN")
    repo = get_config_value("GITHUB_REPOSITORY", "damhyeok/my-stock-scanner")
    workflow_file = get_config_value("GITHUB_WORKFLOW_FILE", "main.yml")
    branch = get_config_value("GITHUB_BRANCH", "main")

    if not token:
        return False, "웹페이지에서 실행하려면 `GITHUB_ACTIONS_TOKEN` 설정이 필요합니다."

    url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow_file}/dispatches"
    inputs = {
        "run_mode": run_mode,
        "market_strength_mode": market_strength_mode,
        "requested_at_kst": requested_at_kst or "",
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
        return True, "GitHub Actions 실행을 요청했습니다. 실제 시작시각 기준으로 분석하며, 완료 후 새로고침하면 최신 데이터가 보입니다."
    return False, f"GitHub Actions 실행 요청 실패: {response.status_code} {response.text}"

def oracle_request(method, path):
    base_url = get_config_value("ORACLE_TRIGGER_URL", "http://161.33.27.132:8765").rstrip("/")
    secret = get_config_value("ORACLE_TRIGGER_SECRET")
    if not secret:
        return None, "웹페이지에 `ORACLE_TRIGGER_SECRET` 설정이 필요합니다."

    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    body = b"{}" if method == "POST" else b""
    body_hash = hashlib.sha256(body).hexdigest()
    payload = f"{method}\n{path}\n{timestamp}\n{nonce}\n{body_hash}".encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    headers = {
        "X-Trigger-Timestamp": timestamp,
        "X-Trigger-Nonce": nonce,
        "X-Trigger-Signature": signature,
        "Content-Type": "application/json",
    }
    try:
        response = requests.request(
            method,
            f"{base_url}{path}",
            data=body if method == "POST" else None,
            headers=headers,
            timeout=15,
        )
    except requests.RequestException as exc:
        return None, f"Oracle 서버 연결 실패: {exc}"

    try:
        result = response.json()
    except requests.JSONDecodeError:
        result = {}
    if response.status_code not in (200, 202, 409):
        return None, f"Oracle 서버 요청 실패: HTTP {response.status_code}"
    return result, ""

def trigger_oracle_analysis():
    status, error = oracle_request("POST", "/run")
    if error:
        return False, error, None
    if status.get("state") == "running":
        return True, status.get("message", "Oracle 서버에서 분석을 시작했습니다."), status
    return False, "현재 실행 상태를 확인할 수 없습니다.", status

def display_oracle_run_status(container):
    status, error = oracle_request("GET", "/status")
    if error:
        container.info(error)
        return

    state = status.get("state", "idle")
    progress = int(status.get("progress", 0))
    labels = {
        "idle": "대기 중",
        "running": "실행 중",
        "success": "완료",
        "failed": "실패",
    }
    container.progress(max(0, min(progress, 100)))
    container.caption(
        f"Oracle 실행 상태: {labels.get(state, state)} · {status.get('message', '')} "
        f"· 마지막 갱신 {status.get('updated_at', '-')}"
    )
    if state == "success":
        container.success("최신 DB가 GitHub에 반영되었습니다. 아래 데이터 새로고침을 눌러 확인하세요.")
    elif state == "failed":
        container.error("Oracle 분석이 실패했습니다. 서버 로그를 확인해야 합니다.")

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
                'afternoon': '오후 흐름',
                'closing': '종가 흐름',
                'manual': '수동 흐름',
            }).fillna(df['analysis_type'])
        return df
    except:
        return pd.DataFrame()

@st.cache_data(ttl=60)
def get_sector_flow_data():
    try:
        db_path, _ = get_database_path()
        conn = sqlite3.connect(db_path)
        df = pd.read_sql(
            "SELECT * FROM sector_flow_windows ORDER BY trade_date DESC, window_key ASC",
            conn,
        )
        conn.close()
        return df
    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=60)
def get_close_bet_data():
    try:
        db_path, _ = get_database_path()
        conn = sqlite3.connect(db_path)
        df = pd.read_sql(
            "SELECT * FROM close_bet_scans ORDER BY trade_date DESC, session, market_cap DESC",
            conn,
        )
        conn.close()
        return df
    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=60)
def get_close_bet_runs():
    try:
        db_path, _ = get_database_path()
        conn = sqlite3.connect(db_path)
        df = pd.read_sql(
            "SELECT * FROM close_bet_scan_runs ORDER BY trade_date DESC, session",
            conn,
        )
        conn.close()
        return df
    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=60)
def get_bottom_candidate_data():
    try:
        db_path, _ = get_database_path()
        init_model_tables(db_path)
        conn = sqlite3.connect(db_path)
        df = pd.read_sql(
            """
            SELECT
                s.*,
                o.change_rate AS today_change_rate
            FROM model_bottom_signals s
            LEFT JOIN model_ohlcv_daily o
                ON o.ticker = s.ticker
                AND o.date = s.signal_date
                AND o.universe_type = s.universe_type
            WHERE s.universe_type = 'market_cap_10000eok_plus'
            ORDER BY s.signal_date DESC, s.bottom_score DESC
            """,
            conn,
        )
        conn.close()
        return df
    except Exception:
        return pd.DataFrame()

def collect_and_build_single_stock(query, db_path):
    configure_model_runtime_secrets()
    collector = ModelDataCollector(db_path=db_path)
    summary = collector.collect_single_stock_ohlcv(
        query,
        min_market_cap=500_000_000_000,
        universe_type="custom_5000eok_plus",
        lookback_days=180,
    )
    ModelFeatureBuilder(db_path=db_path).run("custom_5000eok_plus")
    ModelLabelBuilder(db_path=db_path).run("custom_5000eok_plus")
    return summary

# 데이터 로드
with st.spinner("데이터를 불러오고 있습니다..."):
    df_analyzed = get_analyzed_data()
    df_raw = get_raw_data()
    df_news = get_news_data()
    df_market_strength = get_market_strength_data()
    df_sector_flow = get_sector_flow_data()
    df_close_bet = get_close_bet_data()
    df_close_bet_runs = get_close_bet_runs()
    df_bottom_candidates = get_bottom_candidate_data()

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
    st.sidebar.caption("Oracle 서버가 버튼을 누른 현재 시각 기준으로 전체 분석을 즉시 시작합니다.")
    st.sidebar.caption("장 운영시간 외에는 최신 정규장 DB를 기준으로 뉴스·시장강도·종합분석을 다시 실행합니다.")
    if st.sidebar.button("지금 분석 실행"):
        with st.sidebar.spinner("Oracle 서버에 실행 요청 중..."):
            ok, message, status = trigger_oracle_analysis()
        if ok:
            st.session_state["manual_analysis_notice"] = ("success", message)
            st.session_state["oracle_manual_run_id"] = status.get("run_id") if status else None
        else:
            st.session_state["manual_analysis_notice"] = ("error", message)
    notice = st.session_state.get("manual_analysis_notice")
    if notice:
        notice_type, notice_message = notice
        if notice_type == "success":
            st.sidebar.success(notice_message)
        else:
            st.sidebar.error(notice_message)
    st.sidebar.caption("완료 여부는 아래 Oracle 실행 상태에서 확인할 수 있습니다.")
    display_oracle_run_status(st.sidebar)
    if st.sidebar.button("실행 상태 새로고침"):
        st.rerun()

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
    tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9, tab10, tab11, tab12, tab13 = st.tabs([
        "🏆 종합 추천종목", 
        "🔥 거래대금 Top", 
        "🟢 외인 순매수", 
        "🔴 기관 순매수",
        "📊 섹터별 자금",
        "📈 최근 섹터 흐름",
        "⚡ 실시간 변화(직전 대비)",
        "📰 뉴스 이슈 종목",
        "🌡️ 시장 강도 분석",
        "🧭 오늘 섹터 흐름",
        "🎯 종가베팅 스캐너",
        "🧱 바닥 후보 종목",
        "🔎 종목 차트 분석"
    ])
    
    if 'session' in df_raw.columns:
        df_selected = df_raw[(df_raw['date'] == selected_date) & (df_raw['session'] == selected_session)].copy()
    else:
        df_selected = df_raw[df_raw['date'] == selected_date].copy()

    with tab12:
        st.header("🧱 바닥 후보 종목")
        st.markdown("""
        **바닥 후보 점수 해석**
        - **70점 이상**: 매수 검토 가능 구간입니다. 바로 전량 매수보다 리스크 근거와 시장 흐름을 확인한 뒤 분할 접근을 권장합니다.
        - **55~69점**: 관심 후보입니다. 반등 확인, 거래량 증가, 지지선 방어를 더 보고 판단하는 구간입니다.
        - **40~54점**: 약한 관찰 후보입니다. 실패를 줄이는 기준에서는 매수보다 모니터링에 가깝습니다.
        - **40점 미만**: 현재 기준에서는 제외합니다.
        """)
        if df_bottom_candidates.empty:
            st.info("아직 생성된 바닥 후보 신호가 없습니다. `bottom_detector.py`를 먼저 실행해주세요.")
            display_workflow_run_status(st)
            if st.button("바닥 후보 데이터 생성 요청"):
                with st.spinner("GitHub Actions에 바닥 후보 모델 생성을 요청 중..."):
                    ok, message = trigger_github_workflow(run_mode="bottom_model")
                if ok:
                    st.success("요청 완료. 몇 분 뒤 새로고침하면 바닥 후보가 표시됩니다.")
                    display_workflow_run_status(st)
                else:
                    st.error(message)
        else:
            bottom_day = df_bottom_candidates[
                df_bottom_candidates['signal_date'].astype(str) == str(selected_date)
            ].copy()
            if bottom_day.empty:
                latest_signal_date = df_bottom_candidates['signal_date'].astype(str).max()
                st.info(f"{selected_date} 신호가 없어 최신 신호일({latest_signal_date})을 표시합니다.")
                bottom_day = df_bottom_candidates[
                    df_bottom_candidates['signal_date'].astype(str) == latest_signal_date
                ].copy()

            def compact_json_list(value):
                try:
                    items = json.loads(value) if isinstance(value, str) else []
                    return " / ".join(str(item) for item in items[:4])
                except Exception:
                    return str(value)

            display = bottom_day.copy()
            if 'bottom_score' in display.columns and 'grade' in display.columns:
                display.loc[
                    (pd.to_numeric(display['bottom_score'], errors='coerce') >= 40)
                    & (pd.to_numeric(display['bottom_score'], errors='coerce') < 55),
                    'grade'
                ] = '약한 관찰'
            for column in ['reasons', 'risk_reasons']:
                if column in display.columns:
                    display[column] = display[column].apply(compact_json_list)
            if 'today_change_rate' in display.columns:
                display['today_change_rate'] = pd.to_numeric(
                    display['today_change_rate'], errors='coerce'
                ).round(2)
            display['decision_risk_summary'] = (
                display.get('reasons', pd.Series('', index=display.index)).fillna('').astype(str)
                + " | 리스크: "
                + display.get('risk_reasons', pd.Series('', index=display.index)).fillna('').astype(str)
            )

            display_columns = [
                'signal_date', 'name', 'current_price', 'today_change_rate', 'bottom_score', 'grade',
                'chart_score', 'supply_score', 'sector_market_score',
                'risk_penalty', 'decision_risk_summary',
            ]
            display_columns = [column for column in display_columns if column in display.columns]
            display = display[display_columns].rename(columns={
                'signal_date': '날짜',
                'name': '종목명',
                'current_price': '현재가',
                'today_change_rate': '오늘 상승률(%)',
                'bottom_score': '바닥 후보 점수',
                'grade': '등급',
                'chart_score': '차트 점수',
                'supply_score': '수급 점수',
                'sector_market_score': '섹터/시장 점수',
                'risk_penalty': '리스크 감점',
                'decision_risk_summary': '판단/리스크 근거',
            })
            display_wrapped_table(display)

    with tab13:
        st.header("🔎 종목 차트 분석")
        query = st.text_input("종목명 또는 종목코드", value="", placeholder="예: 삼성전자 또는 005930")
        if query:
            base_db_path, _ = get_database_path()
            db_path = get_chart_model_database_path(base_db_path)
            init_model_tables(db_path)
            chart_analyzer = StockChartAnalyzer(db_path=db_path)
            analysis = chart_analyzer.analyze(query)
            if analysis is None:
                st.warning("저장된 모델 데이터에서 종목을 찾지 못했습니다.")
                st.caption("시총 5천억 이상 종목이면 KIS에서 1년치 일봉을 받아 model_ 전용 DB에 캐시 저장할 수 있습니다.")
                if st.button("KIS에서 종목 데이터 저장 후 분석"):
                    with st.spinner("KIS에서 종목 확인 및 일봉 저장 중..."):
                        try:
                            summary = collect_and_build_single_stock(query, db_path)
                            st.success(
                                f"{summary['stock']['name']} 데이터 저장 완료: "
                                f"{summary['ohlcv_rows']}개 일봉"
                            )
                            st.rerun()
                        except Exception as error:
                            st.error(f"데이터 저장 실패: {error}")
            else:
                stock = analysis["stock"]
                metric_cols = st.columns(6)
                metric_cols[0].metric("종목", f"{stock['name']} ({stock['ticker']})")
                metric_cols[1].metric("점수", analysis["score"])
                metric_cols[2].metric("등급", analysis["grade"])
                metric_cols[3].metric("시장 레짐", analysis.get("market_regime") or "-")
                metric_cols[4].metric("유사 승률", "-" if analysis["similar_pattern_win_rate"] is None else f"{analysis['similar_pattern_win_rate']}%")
                metric_cols[5].metric("유사 표본", analysis["similar_pattern_count"])

                score_cols = st.columns(4)
                score_cols[0].metric("차트", analysis["chart_score"])
                score_cols[1].metric("수급", analysis["supply_score"])
                score_cols[2].metric("섹터/시장", analysis["sector_market_score"])
                score_cols[3].metric("리스크 감점", analysis["risk_penalty"])

                st.plotly_chart(analysis["figure"], use_container_width=True)

                reason_col, risk_col = st.columns(2)
                with reason_col:
                    st.subheader("판단 근거")
                    if analysis["reasons"]:
                        for reason in analysis["reasons"]:
                            st.write(f"- {reason}")
                    else:
                        st.info("강한 긍정 근거가 아직 부족합니다.")
                with risk_col:
                    st.subheader("리스크 근거")
                    if analysis["risk_reasons"]:
                        for reason in analysis["risk_reasons"]:
                            st.write(f"- {reason}")
                    else:
                        st.info("큰 리스크 감점 요인이 없습니다.")

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

        st.write("---")
        st.subheader("🔁 외인·기관 동반 연속 순매수 종목")
        both_streaks = calculate_consecutive_buy_streaks(
            df_raw, both_buy_df, selected_date, selected_session, 'both'
        )
        display_consecutive_buy_table(both_streaks, 'both')

    with tab3:
        st.header(f"🟢 외국인 순매수 Top 30 ({selected_session_label})")
        df_for = df_selected[df_selected['category'] == 'FOREIGN_TOP_30'].copy()
        df_for = df_for.sort_values(by='foreign_net', ascending=False)
        display_formatted_df(df_for, hidden_columns=['date', 'session', 'ticker', 'volume', 'theme'])
        display_sector_summary(df_for)
        st.write("---")
        st.subheader("🔁 외국인 연속 순매수 종목")
        foreign_streaks = calculate_consecutive_buy_streaks(
            df_raw, df_for, selected_date, selected_session, 'foreign'
        )
        display_consecutive_buy_table(foreign_streaks, 'foreign')

    with tab4:
        st.header(f"🔴 기관 순매수 Top 30 ({selected_session_label})")
        df_inst = df_selected[df_selected['category'] == 'INST_TOP_30'].copy()
        df_inst = df_inst.sort_values(by='inst_net', ascending=False)
        display_formatted_df(df_inst, hidden_columns=['date', 'session', 'ticker', 'volume', 'theme'])
        display_sector_summary(df_inst)
        st.write("---")
        st.subheader("🔁 기관 연속 순매수 종목")
        institution_streaks = calculate_consecutive_buy_streaks(
            df_raw, df_inst, selected_date, selected_session, 'institution'
        )
        display_consecutive_buy_table(institution_streaks, 'institution')
        
    # 탭 5: 섹터 요약
    with tab5:
        st.header(f"📊 섹터별 자금 유입 요약 ({selected_session_label} 기준)")
        sector_base = df_selected[df_selected['category'] == 'VOLUME_TOP_60'].drop_duplicates(subset=['ticker']).copy()
        for column in ['foreign_net', 'inst_net', 'trading_value']:
            sector_base[column] = pd.to_numeric(sector_base[column], errors='coerce').fillna(0)
        sector_base['total_net'] = sector_base['foreign_net'] + sector_base['inst_net']
        
        sector_grouped = sector_base.groupby('sector').agg(
            foreign_net=('foreign_net', 'sum'),
            inst_net=('inst_net', 'sum'),
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
        def classify_sector_flow(row):
            if row['foreign_net'] > 0 and row['inst_net'] > 0:
                return '동반 순매수'
            if row['foreign_net'] < 0 and row['inst_net'] < 0:
                return '동반 순매도'
            if row['foreign_net'] > 0:
                return '외인 매수 우위'
            if row['inst_net'] > 0:
                return '기관 매수 우위'
            return '중립'

        sector_disp = sector_grouped.copy()
        sector_disp['flow_status'] = sector_disp.apply(classify_sector_flow, axis=1)
        sector_disp['trading_value'] = sector_disp['trading_value'].apply(format_won_to_eok)
        for column in ['foreign_net', 'inst_net', 'total_net']:
            sector_disp[column] = sector_disp[column].apply(format_kis_flow_to_eok)
        sector_disp = sector_disp[[
            'sector', 'trading_value', 'stock_count', 'foreign_net',
            'inst_net', 'total_net', 'flow_status', 'included_stocks'
        ]].rename(columns={
            'sector': '업종',
            'trading_value': '합산 거래대금(억)',
            'stock_count': '종목 수',
            'foreign_net': '외국인(억)',
            'inst_net': '기관(억)',
            'total_net': '합산 순매수(억)',
            'flow_status': '수급 상태',
            'included_stocks': '포함된 종목들',
        })
        st.dataframe(sector_disp, use_container_width=True)

    # 탭 6: 트렌드
    with tab6:
        st.header(f"📈 최근 섹터 거래대금 순위 흐름 (상위 {trend_count}개)")
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
            df_trend['trading_value'] = pd.to_numeric(df_trend['trading_value'], errors='coerce').fillna(0)
            weekly_sector_rank = (
                df_trend[df_trend['sector'] != '기타']
                .groupby('sector')['trading_value']
                .sum()
                .sort_values(ascending=False)
                .head(trend_count)
                .index
                .tolist()
            )
            if not weekly_sector_rank:
                st.info("이번주 정규장 기준으로 표시할 섹터가 없습니다.")
            else:
                daily_sector_rank = (
                    df_trend[df_trend['sector'] != '기타']
                    .groupby(['date', 'sector'])
                    .agg(
                        trading_value=('trading_value', 'sum'),
                        stock_count=('ticker', 'nunique'),
                        included_stocks=('name', lambda names: ', '.join(dict.fromkeys(names.astype(str)))),
                    )
                    .reset_index()
                )
                daily_sector_rank['trading_rank'] = (
                    daily_sector_rank.groupby('date')['trading_value']
                    .rank(method='min', ascending=False)
                    .astype(int)
                )
                daily_sector_rank['trading_value_eok'] = daily_sector_rank['trading_value'].apply(format_won_to_eok)
                daily_sector_rank['date_label'] = pd.to_datetime(
                    daily_sector_rank['date'], format='%Y%m%d'
                ).dt.strftime('%m/%d')
                latest_sector_date = daily_sector_rank['date'].max()
                latest_flow = daily_sector_rank[
                    daily_sector_rank['date'] == latest_sector_date
                ].sort_values('trading_rank').head(trend_count)
                latest_sector_names = latest_flow['sector'].tolist()
                trend_grouped = daily_sector_rank[
                    daily_sector_rank['sector'].isin(latest_sector_names)
                ].copy()

                st.caption(f"최신 정규장({latest_sector_date}) 거래대금 1~{len(latest_flow)}위 업종을 기준으로 {week_start}부터의 실제 순위 흐름을 표시합니다.")
                trend_line = alt.Chart(trend_grouped).mark_line(point=True).encode(
                    x=alt.X('date_label:N', title='날짜'),
                    y=alt.Y(
                        'trading_rank:Q',
                        title='거래대금 순위',
                        scale=alt.Scale(reverse=True, zero=False),
                        axis=alt.Axis(tickMinStep=1),
                    ),
                    color=alt.Color('sector:N', title='업종', sort=latest_sector_names),
                    tooltip=[
                        alt.Tooltip('date_label:N', title='날짜'),
                        alt.Tooltip('sector:N', title='업종'),
                        alt.Tooltip('trading_rank:Q', title='거래대금 순위', format='.0f'),
                        alt.Tooltip('trading_value_eok:Q', title='거래대금(억)', format=',.0f'),
                        alt.Tooltip('stock_count:Q', title='종목 수'),
                    ],
                ).properties(height=420)
                st.altair_chart(trend_line, use_container_width=True)

                latest_flow_disp = latest_flow[['sector', 'trading_rank', 'trading_value_eok', 'stock_count', 'included_stocks']].rename(columns={
                    'sector': '업종',
                    'trading_rank': '거래대금 순위',
                    'trading_value_eok': '거래대금(억)',
                    'stock_count': '종목 수',
                    'included_stocks': '포함 종목',
                })
                display_wrapped_table(latest_flow_disp)

                st.write("---")
                st.subheader(f"상승 종목 주도 업종 거래대금 순위 흐름 (상위 {trend_count}개)")
                rising_trend = df_trend[
                    pd.to_numeric(df_trend['fluctuation_rate'], errors='coerce').fillna(0) > 0
                ].copy()
                if rising_trend.empty:
                    st.info("이번주 정규장 TOP60에 상승 종목이 없습니다.")
                else:
                    rising_trend['fluctuation_rate'] = pd.to_numeric(
                        rising_trend['fluctuation_rate'], errors='coerce'
                    ).fillna(0)
                    rising_trend['stock_label'] = (
                        rising_trend['name'].astype(str)
                        + rising_trend['fluctuation_rate'].map(lambda rate: f" ({rate:+.2f}%)")
                    )
                    daily_rising_rank = (
                        rising_trend[rising_trend['sector'] != '기타']
                        .groupby(['date', 'sector'])
                        .agg(
                            trading_value=('trading_value', 'sum'),
                            stock_count=('ticker', 'nunique'),
                            included_stocks=('stock_label', lambda labels: ', '.join(dict.fromkeys(labels.astype(str)))),
                        )
                        .reset_index()
                    )
                    daily_rising_rank['trading_rank'] = (
                        daily_rising_rank.groupby('date')['trading_value']
                        .rank(method='min', ascending=False)
                        .astype(int)
                    )
                    daily_rising_rank['trading_value_eok'] = daily_rising_rank['trading_value'].apply(format_won_to_eok)
                    daily_rising_rank['date_label'] = pd.to_datetime(
                        daily_rising_rank['date'], format='%Y%m%d'
                    ).dt.strftime('%m/%d')
                    latest_rising_date = daily_rising_rank['date'].max()
                    latest_rising = daily_rising_rank[
                        daily_rising_rank['date'] == latest_rising_date
                    ].sort_values('trading_rank')
                    latest_rising_sectors = latest_rising.head(trend_count)['sector'].tolist()
                    rising_grouped = daily_rising_rank[
                        daily_rising_rank['sector'].isin(latest_rising_sectors)
                    ].copy()

                    st.caption(
                        f"{week_start}부터 {selected_date}까지 매일 상승한 TOP60 종목만 업종별로 합산하고, "
                        f"최신 정규장({latest_rising_date}) 상승 거래대금 상위 {trend_count}개 업종의 과거 순위를 표시합니다."
                    )
                    rising_line = alt.Chart(rising_grouped).mark_line(point=True).encode(
                        x=alt.X('date_label:N', title='날짜'),
                        y=alt.Y(
                            'trading_rank:Q',
                            title='상승 종목 거래대금 순위',
                            scale=alt.Scale(reverse=True, zero=False),
                            axis=alt.Axis(tickMinStep=1),
                        ),
                        color=alt.Color('sector:N', title='업종', sort=latest_rising_sectors),
                        tooltip=[
                            alt.Tooltip('date_label:N', title='날짜'),
                            alt.Tooltip('sector:N', title='업종'),
                            alt.Tooltip('trading_rank:Q', title='순위', format='.0f'),
                            alt.Tooltip('trading_value_eok:Q', title='상승 거래대금(억)', format=',.0f'),
                            alt.Tooltip('stock_count:Q', title='상승 종목 수'),
                        ],
                    ).properties(height=420)
                    st.altair_chart(rising_line, use_container_width=True)

                    latest_rising_disp = latest_rising.head(trend_count)[
                        ['sector', 'trading_rank', 'trading_value_eok', 'stock_count', 'included_stocks']
                    ].rename(columns={
                        'sector': '업종',
                        'trading_rank': '순위',
                        'trading_value_eok': '상승 거래대금(억)',
                        'stock_count': '상승 종목 수',
                        'included_stocks': '상승 종목',
                    })
                    display_wrapped_table(latest_rising_disp)

    # 탭 7: 직전 시간 대비 변화
    with tab7:
        st.header("⚡ 시간별 거래대금·등락률 변화")
        comparison_sessions = sorted(
            df_raw[
                (df_raw['date'] == selected_date)
                & (df_raw['category'] == 'VOLUME_TOP_60')
            ]['session'].dropna().unique().tolist(),
            key=session_sort_key,
        )

        if len(comparison_sessions) < 2:
            st.info("비교하려면 선택한 날짜에 거래대금 TOP60 데이터가 두 시간 이상 필요합니다.")
        else:
            reference_options = comparison_sessions[1:]
            reference_default = (
                reference_options.index(selected_session)
                if selected_session in reference_options
                else len(reference_options) - 1
            )
            time_col1, time_col2 = st.columns(2)
            with time_col1:
                reference_session = st.selectbox(
                    "기준 시간",
                    reference_options,
                    index=reference_default,
                    format_func=display_session_name,
                    key="change_reference_session",
                )
            reference_index = comparison_sessions.index(reference_session)
            previous_options = comparison_sessions[:reference_index]
            with time_col2:
                previous_session = st.selectbox(
                    "비교 시간",
                    previous_options,
                    index=len(previous_options) - 1,
                    format_func=display_session_name,
                    key="change_previous_session",
                )

            df_curr = df_raw[
                (df_raw['date'] == selected_date)
                & (df_raw['session'] == reference_session)
                & (df_raw['category'] == 'VOLUME_TOP_60')
            ].drop_duplicates('ticker').copy()
            df_prev = df_raw[
                (df_raw['date'] == selected_date)
                & (df_raw['session'] == previous_session)
                & (df_raw['category'] == 'VOLUME_TOP_60')
            ].drop_duplicates('ticker').copy()

            for frame in [df_curr, df_prev]:
                frame['trading_value'] = pd.to_numeric(frame['trading_value'], errors='coerce').fillna(0)
                frame['fluctuation_rate'] = pd.to_numeric(frame['fluctuation_rate'], errors='coerce').fillna(0)
                frame['trading_rank'] = frame['trading_value'].rank(method='min', ascending=False).astype(int)

            merged = df_curr[[
                'ticker', 'name', 'sector', 'trading_value', 'fluctuation_rate', 'trading_rank'
            ]].merge(
                df_prev[['ticker', 'trading_value', 'fluctuation_rate', 'trading_rank']],
                on='ticker',
                how='inner',
                suffixes=('_기준', '_비교'),
            )

            merged['순위 상승'] = merged['trading_rank_비교'] - merged['trading_rank_기준']
            merged['등락률 변화(%p)'] = (
                merged['fluctuation_rate_기준'] - merged['fluctuation_rate_비교']
            ).round(2)

            st.subheader("🚀 거래대금 순위 상승 종목")
            rank_risers = merged[merged['순위 상승'] > 0].sort_values(
                ['순위 상승', 'trading_rank_기준'], ascending=[False, True]
            ).copy()
            if rank_risers.empty:
                st.info("선택한 두 시간 사이에 거래대금 순위가 상승한 공통 종목이 없습니다.")
            else:
                rank_display = rank_risers[[
                    'name', 'sector', 'trading_rank_기준', 'trading_rank_비교',
                    '순위 상승', 'trading_value_기준', 'trading_value_비교'
                ]].copy()
                rank_display['trading_value_비교'] = rank_display['trading_value_비교'].apply(format_won_to_eok)
                rank_display['trading_value_기준'] = rank_display['trading_value_기준'].apply(format_won_to_eok)
                rank_display = rank_display.rename(columns={
                    'name': '종목명',
                    'sector': '업종',
                    'trading_rank_비교': '비교 시간 순위',
                    'trading_rank_기준': '기준 시간 순위',
                    'trading_value_비교': '비교 거래대금(억)',
                    'trading_value_기준': '기준 거래대금(억)',
                })
                st.dataframe(rank_display, use_container_width=True, hide_index=True)

                rank_risers['sector'] = rank_risers['sector'].fillna('기타').replace('', '기타')
                rank_sector = rank_risers.groupby('sector').agg(
                    rising_stock_count=('ticker', 'nunique'),
                    average_rank_rise=('순위 상승', 'mean'),
                    current_trading_value=('trading_value_기준', 'sum'),
                    included_stocks=('name', lambda names: ', '.join(dict.fromkeys(names.astype(str)))),
                ).reset_index()
                rank_sector = rank_sector.sort_values(
                    ['rising_stock_count', 'average_rank_rise', 'current_trading_value'],
                    ascending=[False, False, False],
                )
                rank_sector['average_rank_rise'] = rank_sector['average_rank_rise'].round(1)
                rank_sector['current_trading_value'] = rank_sector['current_trading_value'].apply(format_won_to_eok)
                rank_sector = rank_sector.rename(columns={
                    'sector': '업종',
                    'rising_stock_count': '상승 종목 수',
                    'average_rank_rise': '평균 순위 상승',
                    'current_trading_value': '현재 거래대금(억)',
                    'included_stocks': '포함 종목',
                })
                st.markdown("**거래대금 순위 상승 업종 요약**")
                display_wrapped_table(rank_sector)

            st.subheader("📈 거래대금 순위·등락률 동시 상승 종목")
            joint_risers = merged[
                (merged['순위 상승'] > 0)
                & (merged['fluctuation_rate_기준'] > merged['fluctuation_rate_비교'])
            ].sort_values(
                ['순위 상승', '등락률 변화(%p)'], ascending=False
            ).copy()
            if joint_risers.empty:
                st.info("선택한 두 시간 사이에 거래대금 순위와 등락률이 함께 상승한 공통 종목이 없습니다.")
            else:
                joint_display = joint_risers[[
                    'name', 'sector', 'trading_rank_기준', 'trading_rank_비교',
                    '순위 상승', 'fluctuation_rate_기준',
                    'fluctuation_rate_비교', '등락률 변화(%p)'
                ]].copy()
                joint_display = joint_display.rename(columns={
                    'name': '종목명',
                    'sector': '업종',
                    'trading_rank_기준': '기준 시간 순위',
                    'trading_rank_비교': '비교 시간 순위',
                    'fluctuation_rate_비교': '비교 등락률(%)',
                    'fluctuation_rate_기준': '기준 등락률(%)',
                })
                st.dataframe(joint_display, use_container_width=True, hide_index=True)

                joint_risers['sector'] = joint_risers['sector'].fillna('기타').replace('', '기타')
                joint_sector = joint_risers.groupby('sector').agg(
                    rising_stock_count=('ticker', 'nunique'),
                    average_rank_rise=('순위 상승', 'mean'),
                    average_rate_change=('등락률 변화(%p)', 'mean'),
                    current_trading_value=('trading_value_기준', 'sum'),
                    included_stocks=('name', lambda names: ', '.join(dict.fromkeys(names.astype(str)))),
                ).reset_index()
                joint_sector = joint_sector.sort_values(
                    ['rising_stock_count', 'average_rate_change', 'average_rank_rise'],
                    ascending=[False, False, False],
                )
                joint_sector['average_rank_rise'] = joint_sector['average_rank_rise'].round(1)
                joint_sector['average_rate_change'] = joint_sector['average_rate_change'].round(2)
                joint_sector['current_trading_value'] = joint_sector['current_trading_value'].apply(format_won_to_eok)
                joint_sector = joint_sector.rename(columns={
                    'sector': '업종',
                    'rising_stock_count': '동시 상승 종목 수',
                    'average_rank_rise': '평균 순위 상승',
                    'average_rate_change': '평균 등락률 변화(%p)',
                    'current_trading_value': '현재 거래대금(억)',
                    'included_stocks': '포함 종목',
                })
                st.markdown("**거래대금 순위·등락률 동시 상승 업종 요약**")
                display_wrapped_table(joint_sector)

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
            requested_at = datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="minutes")
            with st.spinner("GitHub Actions 시장강도 분석 요청 중..."):
                ok, message = trigger_github_workflow(
                    run_mode="market_strength_only",
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
                all_strength_groups = strength_selected.copy()
                group_options = (
                    strength_selected[['analysis_type', 'analysis_label']]
                    .drop_duplicates()
                )
                group_order = {'morning': 1, 'afternoon': 2, 'closing': 3, 'manual': 4}
                group_options['group_order'] = group_options['analysis_type'].map(group_order).fillna(99)
                group_options = group_options.sort_values('group_order')

                selected_minutes = session_sort_key(selected_session)
                if selected_minutes >= 15 * 60 + 30:
                    preferred_group_type = 'closing'
                elif selected_minutes >= 12 * 60:
                    preferred_group_type = 'afternoon'
                else:
                    preferred_group_type = 'morning'
                preferred_rows = group_options[
                    group_options['analysis_type'] == preferred_group_type
                ]
                preferred_label = (
                    preferred_rows.iloc[0]['analysis_label']
                    if not preferred_rows.empty else group_options.iloc[-1]['analysis_label']
                )
                label_options = group_options['analysis_label'].tolist()

                if selected_session == '정규장(16:00)':
                    st.subheader("오늘 시장강도 종합")
                    summary_types = [
                        ('morning', '오전'),
                        ('afternoon', '오후'),
                        ('closing', '종가'),
                    ]
                    summary_columns = st.columns(3)
                    for column, (analysis_type, label) in zip(summary_columns, summary_types):
                        rows = all_strength_groups[
                            all_strength_groups['analysis_type'] == analysis_type
                        ].sort_values('snapshot_time')
                        if rows.empty:
                            column.metric(label, "데이터 없음")
                            continue
                        score = pd.to_numeric(
                            rows.iloc[-1].get('market_strength_score'), errors='coerce'
                        )
                        score_text = f"{int(score)}점" if pd.notna(score) else "-"
                        column.metric(label, score_text, market_strength_status(score))

                selected_group_label = st.selectbox(
                    "시장강도 흐름 선택",
                    label_options,
                    index=label_options.index(preferred_label),
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
                        previous_non_arbitrage = pd.to_numeric(prev['non_arbitrage_net'], errors='coerce')
                        current_non_arbitrage = pd.to_numeric(curr['non_arbitrage_net'], errors='coerce')
                        if previous_non_arbitrage <= 0 < current_non_arbitrage:
                            directions.append('비차익 순매수 전환')
                        elif previous_non_arbitrage >= 0 > current_non_arbitrage:
                            directions.append('비차익 순매도 전환')
                        elif current_non_arbitrage > previous_non_arbitrage:
                            directions.append('비차익 개선')
                        elif current_non_arbitrage < previous_non_arbitrage:
                            directions.append('비차익 약화')
                        if pd.to_numeric(curr['kospi200_futures_price'], errors='coerce') > pd.to_numeric(prev['kospi200_futures_price'], errors='coerce'):
                            directions.append('선물 상승')
                        elif pd.to_numeric(curr['kospi200_futures_price'], errors='coerce') < pd.to_numeric(prev['kospi200_futures_price'], errors='coerce'):
                            directions.append('선물 하락')
                        table_df.iloc[idx, table_df.columns.get_loc('변화 방향')] = ', '.join(directions) if directions else '보합'

                display_df = table_df[[
                    'snapshot_time',
                    'basis',
                    'program_net',
                    'non_arbitrage_net',
                    'kospi200_futures_price',
                    '변화 방향'
                ]].rename(columns={
                    'snapshot_time': '시간',
                    'basis': '베이시스',
                    'program_net': '프로그램 순매수',
                    'non_arbitrage_net': '비차익 순매수',
                    'kospi200_futures_price': '코스피200 선물'
                })
                for col in ['프로그램 순매수', '비차익 순매수']:
                    display_df[col] = pd.to_numeric(display_df[col], errors='coerce').round(0).astype('Int64')
                for col in ['베이시스', '코스피200 선물']:
                    display_df[col] = pd.to_numeric(display_df[col], errors='coerce').round(2)
                st.subheader("시간별 시장강도 흐름")
                st.dataframe(display_df, use_container_width=True)

    with tab11:
        st.header(f"🎯 종가베팅 스캐너 ({selected_session_label})")
        st.caption("시가총액 5,000억 원 이상 종목을 대상으로 일봉 추세·모멘텀·거래량·OBV 조건을 검사합니다.")

        selected_run = df_close_bet_runs[
            (df_close_bet_runs['trade_date'].astype(str) == str(selected_date))
            & (df_close_bet_runs['session'].astype(str) == str(selected_session))
        ] if not df_close_bet_runs.empty else pd.DataFrame()

        if selected_run.empty:
            st.info("아직 이 날짜와 시간의 종가베팅 스캔 기록이 없습니다. 다음 전체 분석 실행 후 결과가 표시됩니다.")
        else:
            run = selected_run.iloc[-1]
            metric1, metric2, metric3 = st.columns(3)
            metric1.metric("검사 종목", f"{int(run['scanned_count'])}개")
            metric2.metric("포착 종목", f"{int(run['selected_count'])}개")
            metric3.metric("조회 실패", f"{int(run['failed_count'])}개")
            selected_scan = df_close_bet[
                (df_close_bet['trade_date'].astype(str) == str(selected_date))
                & (df_close_bet['session'].astype(str) == str(selected_session))
            ].copy() if not df_close_bet.empty else pd.DataFrame()
            if selected_scan.empty:
                st.info("선택한 날짜와 시간에는 조건을 충족한 종목이 없습니다.")
            else:
                grade_order = {
                    'S급(최적타점)': 0,
                    'A급(매복)': 1,
                    'S급(과열주의)': 2,
                }
                selected_scan['grade_order'] = selected_scan['grade'].map(grade_order).fillna(9)
                selected_scan = selected_scan.sort_values(
                    ['grade_order', 'market_cap'], ascending=[True, False]
                )
                display_scan = selected_scan[[
                    'grade', 'name', 'ticker', 'market_cap', 'current_price',
                    'fluctuation_rate', 'volume_ratio', 'rsi', 'williams_r'
                ]].copy()
                display_scan['market_cap'] = display_scan['market_cap'].apply(format_won_to_eok)
                display_scan = display_scan.rename(columns={
                    'grade': '등급',
                    'name': '종목명',
                    'ticker': '종목코드',
                    'market_cap': '시가총액(억)',
                    'current_price': '현재가',
                    'fluctuation_rate': '등락률(%)',
                    'volume_ratio': '거래비율(%)',
                    'rsi': 'RSI',
                    'williams_r': 'W%R',
                })
                st.dataframe(display_scan, use_container_width=True, hide_index=True)
                latest_scan_time = selected_scan['scanned_at_kst'].dropna().max()
                if latest_scan_time:
                    st.caption(f"스캔 완료 시각: {latest_scan_time} KST")

    with tab10:
        st.header(f"🧭 오늘 섹터 자금 이동 ({selected_date})")
        today_top60 = df_raw[
            (df_raw['date'] == selected_date)
            & (df_raw['category'] == 'VOLUME_TOP_60')
            & (~df_raw['session'].astype(str).str.contains('시간외', na=False))
        ].copy()
        included_stocks_by_sector = {}
        if not today_top60.empty:
            latest_top60_session = max(
                today_top60['session'].dropna().unique().tolist(),
                key=session_sort_key,
            )
            latest_top60 = today_top60[
                today_top60['session'] == latest_top60_session
            ].drop_duplicates('ticker')
            included_stocks_by_sector = (
                latest_top60.groupby('sector')['name']
                .apply(lambda names: ', '.join(dict.fromkeys(
                    name for name in names.dropna().astype(str) if name.strip()
                )))
                .to_dict()
            )
        minute_flow = df_sector_flow[
            df_sector_flow['trade_date'] == selected_date
        ].copy() if not df_sector_flow.empty else pd.DataFrame()
        if not minute_flow.empty:
            minute_flow['signed_flow'] = pd.to_numeric(minute_flow['signed_flow'], errors='coerce').fillna(0)
            minute_flow['gross_turnover'] = pd.to_numeric(minute_flow['gross_turnover'], errors='coerce').fillna(0)
            minute_flow['signed_flow_per_minute'] = pd.to_numeric(
                minute_flow.get('signed_flow_per_minute', minute_flow['signed_flow']), errors='coerce'
            ).fillna(0)
            minute_flow['gross_turnover_per_minute'] = pd.to_numeric(
                minute_flow.get('gross_turnover_per_minute', minute_flow['gross_turnover']), errors='coerce'
            ).fillna(0)
            minute_flow['sector_return'] = pd.to_numeric(minute_flow['sector_return'], errors='coerce').fillna(0)
            minute_flow['normalized_flow'] = pd.to_numeric(minute_flow['normalized_flow'], errors='coerce').fillna(0.5)
            minute_flow['signed_flow_eok'] = minute_flow['signed_flow'].apply(format_won_to_eok)
            minute_flow['window_label'] = minute_flow['window_start'] + '~' + minute_flow['window_end']
            window_order = (
                minute_flow[['window_key', 'window_label']]
                .drop_duplicates()
                .sort_values('window_key')['window_label']
                .tolist()
            )
            heatmap_tab, crossover_tab = st.tabs([
                "스케일 보정 자금 흐름 지도",
                "섹터 모멘텀 크로스오버",
            ])

            with heatmap_tab:
                heatmap_sector_order = (
                    minute_flow.groupby('sector')['gross_turnover']
                    .sum()
                    .sort_values(ascending=False)
                    .index
                    .tolist()
                )
                normalized_pivot = minute_flow.pivot(
                    index='sector', columns='window_label', values='normalized_flow'
                ).reindex(index=heatmap_sector_order, columns=window_order)
                signed_pivot = minute_flow.pivot(
                    index='sector', columns='window_label', values='signed_flow_eok'
                ).reindex(index=heatmap_sector_order, columns=window_order)
                hover_text = []
                for sector in normalized_pivot.index:
                    row = []
                    for window in normalized_pivot.columns:
                        normalized_value = normalized_pivot.loc[sector, window]
                        signed_value = signed_pivot.loc[sector, window]
                        row.append(
                            f"업종: {sector}<br>구간: {window}<br>자체 정규화: "
                            f"{normalized_value:.2f}<br>순유입 거래대금: {signed_value:,.0f}억"
                        )
                    hover_text.append(row)
                heatmap_figure = go.Figure(data=go.Heatmap(
                    z=normalized_pivot.values,
                    x=normalized_pivot.columns.tolist(),
                    y=normalized_pivot.index.tolist(),
                    zmin=0,
                    zmax=1,
                    colorscale=[
                        [0.0, '#173b57'],
                        [0.5, '#f3f4f4'],
                        [1.0, '#c43d31'],
                    ],
                    text=hover_text,
                    hovertemplate='%{text}<extra></extra>',
                    colorbar={'title': '섹터 자체 강도'},
                ))
                heatmap_figure.update_layout(
                    height=max(420, len(heatmap_sector_order) * 28),
                    margin={'l': 20, 'r': 20, 't': 20, 'b': 20},
                    xaxis_title='2시간 구간',
                    yaxis_title='업종',
                )
                st.plotly_chart(heatmap_figure, use_container_width=True)

            with crossover_tab:
                minute_flow = minute_flow.sort_values(['sector', 'window_key'])
                minute_flow['inflow_change'] = minute_flow.groupby('sector')['signed_flow_per_minute'].diff()
                change_std = minute_flow.groupby('sector')['inflow_change'].transform('std').replace(0, pd.NA)
                minute_flow['inflow_change_z'] = (
                    minute_flow['inflow_change'] / change_std
                ).fillna(0)
                latest_window = minute_flow['window_key'].max()
                crossover = minute_flow[minute_flow['window_key'] == latest_window].copy()
                crossover['gross_turnover_eok'] = crossover['gross_turnover_per_minute'].apply(format_won_to_eok).clip(lower=1)
                scatter = px.scatter(
                    crossover,
                    x='sector_return',
                    y='inflow_change_z',
                    color='sector',
                    size='gross_turnover_eok',
                    hover_name='sector',
                    hover_data={
                        'sector_return': ':.2f',
                        'inflow_change_z': ':.2f',
                        'signed_flow_eok': ':,.0f',
                        'gross_turnover_eok': ':,.0f',
                        'sector': False,
                    },
                    labels={
                        'sector_return': '구간 수익률(%)',
                        'inflow_change_z': '순유입 증가 Z-Score',
                        'signed_flow_eok': '순유입 거래대금(억)',
                        'gross_turnover_eok': '총 거래대금(억)',
                    },
                )
                scatter.add_hline(y=0, line_dash='dash', line_color='#777777')
                scatter.add_vline(x=0, line_dash='dash', line_color='#777777')
                scatter.update_layout(
                    height=520,
                    margin={'l': 20, 'r': 20, 't': 20, 'b': 20},
                    showlegend=False,
                )
                st.plotly_chart(scatter, use_container_width=True)
                latest_table = crossover.sort_values(
                    ['inflow_change_z', 'sector_return'], ascending=False
                )[[
                    'sector', 'sector_return', 'inflow_change_z',
                    'signed_flow_eok', 'normalized_flow'
                ]].rename(columns={
                    'sector': '업종',
                    'sector_return': '구간 수익률(%)',
                    'inflow_change_z': '순유입 증가 Z-Score',
                    'signed_flow_eok': '순유입 거래대금(억)',
                    'normalized_flow': '자체 정규화 강도',
                })
                latest_table['구간 수익률(%)'] = latest_table['구간 수익률(%)'].round(2)
                latest_table['순유입 증가 Z-Score'] = latest_table['순유입 증가 Z-Score'].round(2)
                latest_table['자체 정규화 강도'] = latest_table['자체 정규화 강도'].round(2)
                latest_table['포함 종목'] = latest_table['업종'].map(included_stocks_by_sector).fillna('')
                display_wrapped_table(latest_table)
            st.stop()

        intraday = df_raw[
            (df_raw['date'] == selected_date) &
            (df_raw['category'] == 'VOLUME_TOP_60') &
            (~df_raw['session'].astype(str).str.contains('시간외', na=False))
        ].drop_duplicates(subset=['session', 'ticker']).copy()
        intraday_sessions = sorted(
            intraday['session'].dropna().unique().tolist(),
            key=session_sort_key,
        )

        if len(intraday_sessions) < 2:
            st.info("같은 날 비교 가능한 스냅샷이 2개 이상 필요합니다. 현재 자동 데이터만으로는 09:30과 16:00 이후부터 확인할 수 있습니다.")
        else:
            intraday['trading_value'] = pd.to_numeric(intraday['trading_value'], errors='coerce').fillna(0)
            intraday['fluctuation_rate'] = pd.to_numeric(intraday['fluctuation_rate'], errors='coerce').fillna(0)
            strength_source = intraday[
                (intraday['sector'] != '기타') & (intraday['trading_value'] > 0)
            ].copy()
            strength_source['weighted_return_value'] = (
                strength_source['fluctuation_rate'] * strength_source['trading_value']
            )
            snapshot_strength = (
                strength_source.groupby(['session', 'sector'])
                .agg(
                    weighted_return_value=('weighted_return_value', 'sum'),
                    trading_value=('trading_value', 'sum'),
                    stock_count=('ticker', 'nunique'),
                    rising_count=('fluctuation_rate', lambda rates: int((rates > 0).sum())),
                    included_stocks=('name', lambda names: ', '.join(dict.fromkeys(
                        name for name in names.dropna().astype(str) if name.strip()
                    ))),
                )
                .reset_index()
            )
            snapshot_strength['sector_strength'] = (
                snapshot_strength['weighted_return_value'] / snapshot_strength['trading_value']
            )
            snapshot_strength['rising_ratio'] = (
                snapshot_strength['rising_count'] / snapshot_strength['stock_count'] * 100
            )
            session_order_map = {session: order for order, session in enumerate(intraday_sessions)}
            snapshot_strength['session_order'] = snapshot_strength['session'].map(session_order_map)
            snapshot_strength['session_label'] = snapshot_strength['session'].map(display_session_name)

            interval_rows = []
            for interval_order, (previous_session, current_session) in enumerate(
                zip(intraday_sessions, intraday_sessions[1:])
            ):
                previous = intraday[intraday['session'] == previous_session][
                    ['ticker', 'trading_value']
                ].rename(columns={'trading_value': 'previous_trading_value'})
                current = intraday[intraday['session'] == current_session][
                    ['ticker', 'name', 'sector', 'trading_value', 'fluctuation_rate']
                ].rename(columns={'trading_value': 'current_trading_value'})
                common = current.merge(previous, on='ticker', how='inner')
                if common.empty:
                    continue

                common['current_trading_value'] = pd.to_numeric(
                    common['current_trading_value'], errors='coerce'
                ).fillna(0)
                common['previous_trading_value'] = pd.to_numeric(
                    common['previous_trading_value'], errors='coerce'
                ).fillna(0)
                common['fluctuation_rate'] = pd.to_numeric(
                    common['fluctuation_rate'], errors='coerce'
                ).fillna(0)
                common['interval_value'] = (
                    common['current_trading_value'] - common['previous_trading_value']
                ).clip(lower=0)
                common = common[(common['interval_value'] > 0) & (common['sector'] != '기타')]
                if common.empty:
                    continue

                common = common.sort_values('interval_value', ascending=False)
                common['representative_label'] = (
                    common['name'].astype(str)
                    + common['fluctuation_rate'].map(lambda rate: f" ({rate:+.2f}%)")
                )
                sector_interval = (
                    common.groupby('sector')
                    .agg(
                        interval_value=('interval_value', 'sum'),
                        stock_count=('ticker', 'nunique'),
                        rising_count=('fluctuation_rate', lambda rates: int((rates > 0).sum())),
                        representative_stocks=(
                            'representative_label',
                            lambda labels: ', '.join(list(dict.fromkeys(labels.astype(str)))[:3]),
                        ),
                    )
                    .reset_index()
                )
                total_interval_value = sector_interval['interval_value'].sum()
                sector_interval['market_share'] = (
                    sector_interval['interval_value'] / total_interval_value * 100
                    if total_interval_value > 0 else 0
                )
                sector_interval['rising_ratio'] = (
                    sector_interval['rising_count'] / sector_interval['stock_count'] * 100
                )
                sector_interval['raw_flow_score'] = sector_interval['market_share'] * (
                    0.5 + 0.5 * sector_interval['rising_ratio'] / 100
                )
                max_score = sector_interval['raw_flow_score'].max()
                sector_interval['flow_score'] = (
                    sector_interval['raw_flow_score'] / max_score * 100
                    if max_score > 0 else 0
                )
                sector_interval['interval_label'] = (
                    f"{display_session_name(previous_session)} → {display_session_name(current_session)}"
                )
                sector_interval['interval_order'] = interval_order
                sector_interval['interval_value_eok'] = sector_interval['interval_value'].apply(format_won_to_eok)
                sector_interval['interval_rank'] = sector_interval['interval_value'].rank(
                    method='min', ascending=False
                ).astype(int)
                interval_rows.append(sector_interval)

            if not interval_rows:
                st.info("비교 가능한 공통 종목의 거래대금 증가분이 없습니다.")
            else:
                flow_df = pd.concat(interval_rows, ignore_index=True)
                tracked_sectors = (
                    snapshot_strength.groupby('sector')['sector_strength']
                    .max()
                    .sort_values(ascending=False)
                    .head(trend_count)
                    .index
                    .tolist()
                )
                strength_chart_data = snapshot_strength[
                    snapshot_strength['sector'].isin(tracked_sectors)
                ].copy()
                session_labels = [display_session_name(session) for session in intraday_sessions]
                latest_strength_session = intraday_sessions[-1]
                strength_legend_order = strength_chart_data[
                    strength_chart_data['session'] == latest_strength_session
                ].sort_values('sector_strength', ascending=False)['sector'].tolist()
                strength_legend_order.extend(
                    sector for sector in tracked_sectors if sector not in strength_legend_order
                )

                st.subheader("섹터 상승 강도 변화")
                strength_line = alt.Chart(strength_chart_data).mark_line(point=True).encode(
                    x=alt.X('session_label:N', title='시간', sort=session_labels),
                    y=alt.Y('sector_strength:Q', title='거래대금 가중 평균 등락률(%)'),
                    color=alt.Color('sector:N', title='업종', sort=strength_legend_order),
                    tooltip=[
                        alt.Tooltip('session_label:N', title='시간'),
                        alt.Tooltip('sector:N', title='업종'),
                        alt.Tooltip('sector_strength:Q', title='상승 강도(%)', format='+.2f'),
                        alt.Tooltip('rising_ratio:Q', title='상승 종목 비율(%)', format='.0f'),
                    ],
                ).properties(height=360)
                zero_rule = alt.Chart(pd.DataFrame({'zero': [0]})).mark_rule(
                    color='#777777', strokeDash=[4, 4]
                ).encode(y='zero:Q')
                st.altair_chart(strength_line + zero_rule, use_container_width=True)

                st.subheader("구간 신규 거래대금 비중")
                flow_chart_data = flow_df[flow_df['sector'].isin(tracked_sectors)].copy()
                interval_labels = flow_df.sort_values('interval_order')['interval_label'].drop_duplicates().tolist()
                latest_interval_order = flow_chart_data['interval_order'].max()
                flow_legend_order = flow_chart_data[
                    flow_chart_data['interval_order'] == latest_interval_order
                ].sort_values('market_share', ascending=False)['sector'].tolist()
                flow_legend_order.extend(
                    sector for sector in tracked_sectors if sector not in flow_legend_order
                )
                flow_line = alt.Chart(flow_chart_data).mark_line(point=True).encode(
                    x=alt.X('interval_label:N', title='비교 구간', sort=interval_labels),
                    y=alt.Y('market_share:Q', title='구간 신규 거래대금 비중(%)'),
                    color=alt.Color('sector:N', title='업종', sort=flow_legend_order),
                    tooltip=[
                        alt.Tooltip('interval_label:N', title='구간'),
                        alt.Tooltip('sector:N', title='업종'),
                        alt.Tooltip('market_share:Q', title='거래대금 비중(%)', format='.1f'),
                        alt.Tooltip('interval_value_eok:Q', title='신규 거래대금(억)', format=',.0f'),
                        alt.Tooltip('rising_ratio:Q', title='상승 종목 비율(%)', format='.0f'),
                    ],
                ).properties(height=320)
                st.altair_chart(flow_line, use_container_width=True)

                rotation_rows = []
                for interval_order, (previous_session, current_session) in enumerate(
                    zip(intraday_sessions, intraday_sessions[1:])
                ):
                    previous_strength = snapshot_strength[
                        snapshot_strength['session'] == previous_session
                    ][['sector', 'sector_strength']].rename(columns={'sector_strength': 'previous_strength'})
                    current_strength = snapshot_strength[
                        snapshot_strength['session'] == current_session
                    ][['sector', 'sector_strength']].rename(columns={'sector_strength': 'current_strength'})
                    changes = current_strength.merge(previous_strength, on='sector', how='inner')
                    if changes.empty:
                        continue
                    changes['strength_change'] = changes['current_strength'] - changes['previous_strength']
                    interval_flow = flow_df[flow_df['interval_order'] == interval_order][
                        ['sector', 'market_share']
                    ]
                    changes = changes.merge(interval_flow, on='sector', how='left').fillna({'market_share': 0})
                    stronger = changes.sort_values('strength_change', ascending=False).iloc[0]
                    weaker = changes.sort_values('strength_change').iloc[0]
                    current_session_stocks = intraday[
                        intraday['session'] == current_session
                    ].drop_duplicates('ticker')

                    def stocks_in_sector(sector_name):
                        names = current_session_stocks[
                            current_session_stocks['sector'] == sector_name
                        ]['name'].dropna().astype(str)
                        return ', '.join(dict.fromkeys(
                            name for name in names if name.strip()
                        ))

                    rotation_rows.append({
                        '구간': f"{display_session_name(previous_session)} → {display_session_name(current_session)}",
                        '강해진 섹터': (
                            f"{stronger['sector']} ({stronger['strength_change']:+.2f}%p, "
                            f"거래비중 {stronger['market_share']:.1f}%)"
                        ),
                        '강해진 섹터 종목': stocks_in_sector(stronger['sector']),
                        '약해진 섹터': (
                            f"{weaker['sector']} ({weaker['strength_change']:+.2f}%p, "
                            f"거래비중 {weaker['market_share']:.1f}%)"
                        ),
                        '약해진 섹터 종목': stocks_in_sector(weaker['sector']),
                    })

                st.subheader("순환매 변화 요약")
                rotation_df = pd.DataFrame(rotation_rows)
                if rotation_df.empty:
                    st.info("비교 가능한 섹터 강도 변화가 없습니다.")
                else:
                    display_wrapped_table(rotation_df)

                latest_session = intraday_sessions[-1]
                latest_strength = snapshot_strength[
                    snapshot_strength['session'] == latest_session
                ].sort_values('sector_strength', ascending=False).head(trend_count).copy()
                latest_strength['sector_strength'] = latest_strength['sector_strength'].round(2)
                latest_strength['rising_ratio'] = latest_strength['rising_ratio'].round(0).astype(int)
                latest_disp = latest_strength[
                    ['sector', 'sector_strength', 'rising_ratio', 'stock_count', 'included_stocks']
                ].rename(columns={
                    'sector': '업종',
                    'sector_strength': '현재 상승 강도(%)',
                    'rising_ratio': '상승 종목 비율(%)',
                    'stock_count': '종목 수',
                    'included_stocks': '포함 종목',
                })
                st.subheader(f"{display_session_name(latest_session)} 현재 강도")
                display_wrapped_table(latest_disp)
