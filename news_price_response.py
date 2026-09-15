"""Persist as-observed news/price context; no forecast or extra API requests."""
import json
import sqlite3
from datetime import datetime, timedelta

import pandas as pd


def columns(conn, table):
    return {r[1] for r in conn.execute('PRAGMA table_info("' + table + '")')}


def pct(current, base):
    return round((float(current) / float(base) - 1) * 100, 2) if current and base and float(base) > 0 else None


def assess(direction, importance, confidence, reaction, prior, flow, adverse=False):
    if adverse or direction in ('악재 가능', '혼재'):
        return '상충·악재 확인', '동일 종목의 악재·혼재 이슈를 먼저 확인하세요.'
    if direction != '호재 가능' or importance < 2 or confidence == '기대·검토 보도':
        return '뉴스 근거 확인', '제목만으로 실질적인 새 호재를 판단하기 어렵습니다.'
    if reaction is None or prior is None:
        return '가격 자료 부족', '뉴스 전후 가격 또는 사전 5거래일 자료가 부족합니다.'
    if reaction >= 5 or prior >= 10:
        return '상승 진행·주의', '관찰용 기준상 뉴스 전후 상승이 이미 큽니다.'
    if reaction <= -3:
        return '부정적 반응·주의', '호재 가능 보도와 달리 가격이 하락했습니다.'
    if flow is not None and flow > 0 and reaction >= 0:
        return '반응 시작·검토', '상승은 제한적이고 프로그램 순매수 변화는 양수입니다.'
    return '반응 제한·관찰', '상승은 제한적입니다. 시장의 무관심·약한 재료일 가능성도 있습니다.'


def save_price_context(conn, date, session, observed_at):
    conn.execute('''CREATE TABLE IF NOT EXISTS news_issue_price_context (
        date TEXT, session TEXT, event_id TEXT, current_price REAL, today_rate REAL,
        reaction_rate REAL, baseline_kind TEXT, baseline_at TEXT, price_at TEXT,
        pre_news_5d REAL, sector_excess_today REAL, program_change_eok REAL,
        flow_interval TEXT, verdict TEXT, review_reason TEXT,
        PRIMARY KEY(date,session,event_id))''')
    conn.row_factory = sqlite3.Row
    if conn.execute('SELECT 1 FROM news_issue_price_context WHERE date=? AND session=?', (date, session)).fetchone():
        return
    events = [dict(r) for r in conn.execute('''SELECT v.* FROM news_issue_snapshots s
        JOIN news_issue_versions v USING(version_id) WHERE s.date=? AND s.session=?''', (date, session))]
    required = {'ticker','close','date','session','category','collected_at_kst','fluctuation_rate','sector'}
    stock_rows = []
    if required <= columns(conn, 'daily_stocks'):
        stock_rows = [dict(r) for r in conn.execute('''SELECT * FROM daily_stocks
            WHERE date=? AND session=? AND category='VOLUME_TOP_60'
            AND collected_at_kst<=? ORDER BY collected_at_kst''', (date, session, observed_at))]
    stocks = {str(r['ticker']): r for r in stock_rows}
    adverse = {e['ticker'] for e in events if e['direction'] in ('악재 가능','혼재')}
    for event in events:
        stock = stocks.get(event['ticker'], {})
        current, price_at = stock.get('close'), stock.get('collected_at_kst')
        published = min(a['published_at'] for a in json.loads(event['evidence_json']))
        base, baseline_at, kind = None, None, '자료 없음'
        prior = None
        if required <= columns(conn, 'daily_stocks'):
            # Six consecutive saved market dates, all before the news date.
            dates = [r[0] for r in conn.execute("SELECT DISTINCT date FROM daily_stocks WHERE date<? AND session='정규장(16:00)' ORDER BY date DESC LIMIT 6", (published[:10].replace('-',''),))]
            history = {r['date']: dict(r) for r in conn.execute("SELECT date,close,collected_at_kst FROM daily_stocks WHERE ticker=? AND date<? AND session='정규장(16:00)' AND category='VOLUME_TOP_60' AND collected_at_kst<=? ORDER BY collected_at_kst", (event['ticker'], published[:10].replace('-',''), observed_at))}
            if len(dates) == 6 and all(d in history for d in dates):
                prior = pct(history[dates[0]]['close'], history[dates[-1]]['close'])
            if published[11:16] < '09:00' and dates and dates[0] in history:
                base = history[dates[0]]['close']
                baseline_at = dates[0] + ' 15:30 (종가)'
                kind = '장전 보도·전일 종가'
        if {'ticker','trade_date','bar_time','close','collected_at_kst'} <= columns(conn, 'intraday_stock_bars') and price_at:
            for stamp, fallback in [(published, False), (event['first_seen'], True)]:
                if base is not None:
                    break
                dt = datetime.fromisoformat(stamp)
                bar = conn.execute('''SELECT bar_time,close FROM intraday_stock_bars
                    WHERE ticker=? AND trade_date=? AND bar_time < ? AND bar_time >= ?
                    AND collected_at_kst<=? AND close>0 ORDER BY bar_time DESC LIMIT 1''',
                    (event['ticker'], dt.strftime('%Y%m%d'), dt.strftime('%H:%M'),
                     (dt-timedelta(minutes=5)).strftime('%H:%M'), observed_at)).fetchone()
                # Fallback measures from a completed bar around first observation,
                # not from original publication; the distinction is visible.
                if bar:
                    baseline_at = dt.strftime('%Y-%m-%d') + ' ' + bar['bar_time']
                    if baseline_at <= price_at and (not fallback or baseline_at >= published):
                        base = bar['close']
                        kind = '최초 수집 시점 부근' if fallback else '보도 직전 분봉'
        reaction = pct(current, base) if price_at and published <= price_at else None
        peers = [r['fluctuation_rate'] for r in stock_rows if r['sector'] == event['sector'] and r['fluctuation_rate'] is not None]
        sector_excess = round(stock['fluctuation_rate'] - float(pd.Series(peers).median()), 2) if len(peers) >= 3 and stock.get('fluctuation_rate') is not None else None
        flow, interval = None, '자료 없음'
        if {'ticker','trade_date','program_net_buy','collected_at_kst'} <= columns(conn, 'stock_program_net_snapshots'):
            rows = conn.execute('''SELECT program_net_buy,collected_at_kst FROM stock_program_net_snapshots
                WHERE trade_date=? AND ticker=? AND collected_at_kst<=? ORDER BY collected_at_kst DESC''', (date, event['ticker'], observed_at)).fetchall()
            if rows:
                cutoff = (datetime.fromisoformat(rows[0]['collected_at_kst']) - timedelta(minutes=20)).strftime('%Y-%m-%d %H:%M:%S')
                previous = next((r for r in rows[1:] if r['collected_at_kst'] <= cutoff), None)
                if previous and rows[0]['program_net_buy'] is not None and previous['program_net_buy'] is not None:
                    flow = round((rows[0]['program_net_buy']-previous['program_net_buy'])/1e8, 2)
                    interval = previous['collected_at_kst'][11:16] + ' → ' + rows[0]['collected_at_kst'][11:16]
        verdict, reason = assess(event['direction'], event['importance'], event['confidence'], reaction, prior, flow, event['ticker'] in adverse)
        conn.execute('INSERT INTO news_issue_price_context VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                     (date,session,event['event_id'],current,stock.get('fluctuation_rate'),reaction,kind,baseline_at,price_at,prior,sector_excess,flow,interval,verdict,reason))


def build_price_display(conn):
    if not columns(conn, 'news_issue_price_context'):
        return
    conn.executescript('''DROP TABLE IF EXISTS web_news_issue_price_context;
        CREATE TABLE web_news_issue_price_context AS SELECT p.* FROM news_issue_price_context p
        JOIN web_news_issue_snapshots s USING(date,session,event_id);
        CREATE INDEX idx_web_news_price_session ON web_news_issue_price_context(date,session);''')


def render_price_context(st, conn, date, session, events):
    if not columns(conn, 'web_news_issue_price_context'):
        return
    prices = pd.read_sql_query('SELECT * FROM web_news_issue_price_context WHERE date=? AND session=?', conn, params=(date,session))
    if prices.empty:
        return
    frame = events.merge(prices, on='event_id')
    st.subheader('뉴스·주가 반응 관심 후보')
    st.caption('매수 추천·저평가 판정이 아닌 관찰용 분류입니다. —는 자료 부족이며 0%가 아닙니다.')
    labels = {'name':'종목','title':'핵심 이슈','confidence':'확인 상태','verdict':'구분','today_rate':'오늘 등락률(%)','reaction_rate':'기준 이후 변화(%)','baseline_kind':'비교 기준','pre_news_5d':'뉴스 전 5거래일(%)','sector_excess_today':'오늘 업종표본 대비(%p)','program_change_eok':'프로그램 변화(억)','flow_interval':'수급 비교 구간','review_reason':'검토 이유','baseline_at':'기준 가격 시각','price_at':'현재 가격 시각','current_price':'현재가'}
    candidate = frame[frame.verdict.isin(['반응 시작·검토','반응 제한·관찰'])]
    if candidate.empty:
        st.info('현재 자료로 조건을 확인한 관심 후보가 없습니다. 아래에서 자료 부족·주의 사유를 볼 수 있습니다.')
    else:
        st.dataframe(candidate[list(labels)].rename(columns=labels), hide_index=True, use_container_width=True)
    with st.expander('전체 이슈의 가격 반응·자료 부족 사유'):
        st.dataframe(frame[list(labels)].rename(columns=labels), hide_index=True, use_container_width=True)
    with st.expander('뉴스·주가 반응 계산 설명'):
        st.write('오늘 등락률은 전일 종가 대비입니다. 기준 이후 변화는 표시된 기준 가격 대비이며, 최초 수집 시점 부근 기준은 뉴스 발표 이후 전체 반응과 다릅니다. 뉴스 전 5거래일은 뉴스 날짜 이전의 6개 종가로 계산하며 중간 자료가 없으면 비워 둡니다. 업종 대비는 같은 회차 거래대금 TOP60 내 같은 업종(최소 3종목)의 등락률 중앙값 대비이며 정식 업종지수가 아닙니다. 프로그램 변화는 최소 20분 간격의 최근 두 관측치 차이입니다.')
        st.write('임시 관찰 기준: 기준 이후 +5% 또는 사전 5거래일 +10% 이상은 상승 진행·주의, 기준 이후 −3% 이하는 부정적 반응·주의입니다. 그 외 호재 가능·내용 중요도 2점 이상이고 가격 자료가 있는 이슈만 관찰 후보로 분류합니다. 기대·검토 보도와 동일 종목의 악재·혼재는 우선 제외합니다. 임계값은 수익성 검증 전이며 적정 상승률이나 남은 상승 여력을 뜻하지 않습니다.')
