"""Fast fragment-local views of stored quantities; zero web-side quote requests."""
from contextlib import closing
from datetime import datetime
from pathlib import Path
import sqlite3
import pandas as pd
import streamlit as st
from streamlit_layout import layout_width


@st.cache_data(ttl=60)
def load_broker_data(path, version, modified):
    frames = {}
    with closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True)) as conn:
        for table in ['members','universe','daily','closes','runs']:
            name = 'foreign_broker_' + table
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
            frames[table] = pd.read_sql_query(f'SELECT * FROM {name}', conn) if exists else pd.DataFrame()
    return frames


def aggregate_period(data, dates):
    """Unknown observations stay null; rankings use fully observed stock-periods."""
    universe = data['universe']
    members = data['members']
    empty = (pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
    if universe.empty or members.empty:
        return empty
    scope = universe[universe.trade_date.isin(dates)].sort_values('trade_date').drop_duplicates('ticker', keep='last')[['ticker','name']]
    if scope.empty:
        return empty
    matrix = scope.assign(_key=1).merge(members[['broker_code','broker_name']].assign(_key=1), on='_key').drop(columns='_key')
    raw = data['daily']
    raw = raw[raw.trade_date.isin(dates) & raw.ticker.isin(scope.ticker)].copy() if not raw.empty else pd.DataFrame()
    if raw.empty:
        matrix['observed_days'] = 0
        for col in ['buy_qty','sell_qty','net_qty','reference_net_amount','collected_at','provisional_days']:
            matrix[col] = None
    else:
        prices = data['closes']
        raw = raw.merge(prices[['trade_date','ticker','close']], on=['trade_date','ticker'], how='left') if not prices.empty else raw.assign(close=float('nan'))
        raw['reference_net_amount'] = raw.net_qty * raw.close
        grouped = raw.groupby(['ticker','broker_code']).agg(
            buy_qty=('buy_qty','sum'),sell_qty=('sell_qty','sum'),net_qty=('net_qty','sum'),
            observed_days=('trade_date','nunique'),price_days=('close','count'),
            provisional_days=('is_final',lambda s:int((s==0).sum())),
            reference_net_amount=('reference_net_amount',lambda s:s.sum(min_count=1)),collected_at=('collected_at','max'),
        ).reset_index()
        grouped.loc[grouped.price_days < grouped.observed_days, 'reference_net_amount'] = float('nan')
        matrix = matrix.merge(grouped, on=['ticker','broker_code'], how='left')
        matrix['observed_days'] = matrix.observed_days.fillna(0).astype(int)
    matrix['expected_days'] = len(dates)
    matrix['complete'] = matrix.observed_days.eq(len(dates))
    totals = []
    for ticker, frame in matrix.groupby('ticker', sort=False):
        complete = bool(frame.complete.all())
        amount = frame.reference_net_amount.sum(min_count=len(frame)) if complete else float('nan')
        totals.append(dict(ticker=ticker,name=frame.name.iloc[0],complete=complete,
                           buy_qty=frame.buy_qty.sum(min_count=1),sell_qty=frame.sell_qty.sum(min_count=1),
                           net_qty=frame.net_qty.sum(min_count=1), reference_net_amount=amount,
                           observed_pairs=int(frame.observed_days.sum()),expected_pairs=len(frame)*len(dates),
                           collected_at=frame.collected_at.dropna().max() if frame.collected_at.notna().any() else None))
    return matrix, pd.DataFrame(totals), raw


def show_quantities(frame, identity='broker_name'):
    columns = [identity,'buy_qty','sell_qty','net_qty','reference_net_amount']
    shown = frame[columns].copy().rename(columns={identity:'거래원' if identity=='broker_name' else '종목명',
        'buy_qty':'매수수량(주)','sell_qty':'매도수량(주)','net_qty':'순매수수량(주)',
        'reference_net_amount':'종가 환산 참고금액(억)'})
    shown['종가 환산 참고금액(억)'] = pd.to_numeric(shown['종가 환산 참고금액(억)'], errors='coerce') / 1e8
    if 'observed_days' in frame:
        shown['확보일수'] = frame.observed_days.astype(int).astype(str) + '/' + frame.expected_days.astype(int).astype(str)
    if 'provisional_days' in frame:
        shown['잠정일수'] = frame.provisional_days
    if 'collected_at' in frame:
        shown['최근 조회'] = frame.collected_at
    st.dataframe(shown.style.format({'매수수량(주)':'{:,.0f}','매도수량(주)':'{:,.0f}',
                                      '순매수수량(주)':'{:+,.0f}','종가 환산 참고금액(억)':'{:+,.2f}'},na_rep='미수집'),
                 hide_index=True, **layout_width())


@st.fragment
def render_foreign_broker_tab(path, version, day):
    st.header('🏦 외국계 거래원')
    st.caption('KRX 회원사 기준 · 순위는 순매수/순매도 수량 기준입니다. 외국계 창구는 외국인 투자자와 다릅니다. 검색·기간 변경은 이 탭 안에서만 갱신합니다.')
    data = load_broker_data(str(path), str(version), Path(path).stat().st_mtime_ns)
    if data['universe'].empty or data['members'].empty:
        st.info('외국계 거래원 수집 준비 중입니다. 독립 수집 작업이 데이터를 확보하면 표시됩니다. 과거 미수집 자료는 0주로 표시하지 않습니다.')
        return
    available = sorted(data['universe'].loc[data['universe'].trade_date <= str(day),'trade_date'].unique())
    if not available:
        st.info('선택한 날짜까지의 수집 대상이 없습니다.')
        return
    mode = st.radio('조회 기간', ['선택한 날짜','최근 5거래일','최근 20거래일','직접 지정'], horizontal=True, key='foreign_period')
    if mode == '직접 지정':
        first, last = [datetime.strptime(d,'%Y%m%d').date() for d in [available[0],available[-1]]]
        selected = st.date_input('시작일 · 종료일',value=(first,last),min_value=first,max_value=last,key='foreign_dates')
        if len(selected) != 2:
            st.info('시작일과 종료일을 선택하세요.')
            return
        lo, hi = [d.strftime('%Y%m%d') for d in selected]
        dates = [d for d in available if lo <= d <= hi]
    else:
        dates = [str(day)] if mode == '선택한 날짜' else available[-(5 if mode=='최근 5거래일' else 20):]
    details, totals, raw = aggregate_period(data, dates)
    if totals.empty:
        st.info('이 기간의 수집 대상·자료가 없습니다. 수집 시작일 이전의 자료는 제공하지 않습니다.')
        return
    st.caption(f'{dates[0]}~{dates[-1]} · {len(dates)}거래일 · 대상 {len(totals)}종목 · 외국계 {len(data["members"])}개사')
    complete = totals[totals.complete].copy()
    count = int(totals.observed_pairs.sum())
    expected = int(totals.expected_pairs.sum())
    st.write(f'자료 확보: {count:,}/{expected:,}건 · 기간 전체 확보 종목: {len(complete)}/{len(totals)}')
    if len(complete) < len(totals):
        st.warning('아직 수집 중이거나 일부 날짜가 미수집입니다. 전체 TOP10은 기간 내 모든 거래원·거래일 자료가 확보된 종목만 비교합니다. 미수집 종목이 빠져 순위가 달라질 수 있습니다.')
    if not data['daily'].empty:
        st.caption('최근 저장 시각: ' + str(data['daily'].collected_at.max()) + ' · 당일 자료는 잠정치이며 다음 거래일 재조회로 보완합니다. 회원사별 조회시각이 다를 수 있고, 상단 분석 시간 선택과 별도로 마지막 수집값을 보여줍니다.')
    st.info('참고금액 = Σ(각 날짜 순매수수량 × 해당 날짜 KRX 종가). 실제 거래금액·매입단가·보유잔고가 아닙니다. 확정 종가가 없으면 미수집이며, 순위 산정에는 사용하지 않습니다.')
    st.subheader('① 종목별 외국계 거래원')
    options = totals.sort_values('name').ticker.tolist()
    names = dict(zip(totals.ticker,totals.name))
    chosen = st.selectbox('종목 검색',options,index=None,format_func=lambda t:f'{names[t]} ({t})',
                          placeholder='종목명 또는 코드로 검색',key='foreign_stock')
    if chosen:
        stock = details[details.ticker==chosen].sort_values('net_qty',ascending=False,na_position='last')
        show_quantities(stock)
        st.caption('일부 날짜만 확보된 행의 수량은 확보된 날짜의 소계입니다. 확보일수가 전체 기간보다 작으면 기간 전체 누적으로 해석하지 마세요.')
        if not raw.empty:
            with st.expander('날짜별 상세 내역'):
                daily = raw[raw.ticker==chosen].merge(data['members'][['broker_code','broker_name']],on='broker_code',how='left').sort_values(['trade_date','broker_name'],ascending=[False,True])
                st.dataframe(daily[['trade_date','broker_name','buy_qty','sell_qty','net_qty','close','collected_at']].rename(columns={'trade_date':'날짜','broker_name':'거래원','buy_qty':'매수수량','sell_qty':'매도수량','net_qty':'순매수수량','close':'환산 기준 종가','collected_at':'수집 시각'}),hide_index=True,**layout_width())
    st.subheader('② 외국계 합산 순매수 TOP10')
    buy = complete[complete.net_qty > 0].sort_values(['net_qty','ticker'],ascending=[False,True]).head(10)
    if buy.empty:
        st.info('자료가 확보된 순매수 종목이 없습니다.')
    else:
        show_quantities(buy, 'name')
    st.subheader('③ 외국계 합산 순매도 TOP10')
    sell = complete[complete.net_qty < 0].sort_values(['net_qty','ticker']).head(10)
    if sell.empty:
        st.info('자료가 확보된 순매도 종목이 없습니다.')
    else:
        show_quantities(sell, 'name')
    st.subheader('④ 거래원별 순매수 · 순매도 상위 종목')
    for member in data['members'].sort_values('broker_name').itertuples():
        with st.expander(member.broker_name):
            broker = details[details.broker_code==member.broker_code]
            subset = broker[broker.complete]
            st.caption(f'기간 전체 확보 {len(subset)}/{len(broker)}종목. 미수집은 0주가 아닙니다.')
            st.write('순매수 TOP10')
            buys = subset[subset.net_qty>0].sort_values(['net_qty','ticker'],ascending=[False,True]).head(10)
            if buys.empty:
                st.info('확보된 순매수 종목 없음')
            else:
                show_quantities(buys,'name')
            st.write('순매도 TOP10')
            sells = subset[subset.net_qty<0].sort_values(['net_qty','ticker']).head(10)
            if sells.empty:
                st.info('확보된 순매도 종목 없음')
            else:
                show_quantities(sells,'name')
    with st.expander('수집 방식·주의사항'):
        st.write('대상은 웹 조회에 사용하는 순위 종목·모델 분석 대상·ETF·관심종목의 합집합입니다. 같은 종목은 한 번만 수집합니다. 전체 시장 순위가 아닙니다.')
        st.write('개별 외국계 회원사를 조회하며 외국계합(99999) 집계코드를 다시 더하지 않습니다. 수량은 API 일별 자료이고, 금액은 각 날짜 종가 환산 참고치입니다.')
        st.write('처음 수집한 날짜부터 기록합니다. 기간 중 수집 대상에서 빠진 날은 미수집으로 남으며, 그 종목은 기간 전체 확보 TOP10에서 제외됩니다. 수집 대상 기록·일별 수량은 서버 최대 260거래일, 웹 조회는 최근 최대 60거래일입니다.')
