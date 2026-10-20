"""Fast fragment-local views of stored quantities; zero web-side quote requests."""
from contextlib import closing
from datetime import datetime
from pathlib import Path
import sqlite3
import html
import pandas as pd
import streamlit as st
from streamlit_layout import layout_width
from foreign_broker_history import build_broker_streaks, build_stock_net_history, build_stock_trade_details


@st.cache_data(ttl=60)
def load_broker_data(path, version, modified):
    frames = {}
    with closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True)) as conn:
        for table in ['members','universe','daily','closes','runs']:
            name = 'foreign_broker_' + table
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
            frames[table] = pd.read_sql_query(f'SELECT * FROM {name}', conn) if exists else pd.DataFrame()
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='web_trading_dates'").fetchone()
        frames['calendar'] = pd.read_sql_query('SELECT date FROM web_trading_dates ORDER BY date',conn) if exists else pd.DataFrame()
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


def rank_reference_amount(frame, *, buying=True, limit=10):
    """Compare complete periods only; missing closes never fall back to shares."""
    eligible = frame[frame.complete & frame.reference_net_amount.notna()]
    amount = eligible.reference_net_amount
    eligible = eligible[amount.gt(0) if buying else amount.lt(0)]
    return eligible.sort_values(['reference_net_amount','ticker'],
                                ascending=[not buying,True]).head(limit)


def show_quantities(frame, identity='broker_name'):
    columns = [identity,'buy_qty','sell_qty','net_qty','reference_net_amount']
    shown = frame[columns].copy().rename(columns={identity:'거래원' if identity=='broker_name' else '종목명',
        'buy_qty':'매수수량(주)','sell_qty':'매도수량(주)','net_qty':'순매수수량(주)',
        'reference_net_amount':'종가 환산 순매수 참고금액(억)'})
    shown['종가 환산 순매수 참고금액(억)'] = pd.to_numeric(shown['종가 환산 순매수 참고금액(억)'], errors='coerce') / 1e8
    if 'observed_days' in frame:
        shown['확보일수'] = frame.observed_days.astype(int).astype(str) + '/' + frame.expected_days.astype(int).astype(str)
    if 'provisional_days' in frame:
        shown['잠정일수'] = frame.provisional_days
    if 'collected_at' in frame:
        shown['최근 조회'] = frame.collected_at
    st.dataframe(shown.style.format({'매수수량(주)':'{:,.0f}','매도수량(주)':'{:,.0f}',
                                      '순매수수량(주)':'{:+,.0f}','종가 환산 순매수 참고금액(억)':'{:+,.2f}'},na_rep='미수집'),
                 hide_index=True, **layout_width())


def show_streaks(frame):
    if frame.empty:
        st.info('선택한 마지막 거래일까지 2거래일 이상 연속 순매수가 확인된 종목이 없습니다. 자료 부족은 순매도 또는 0주를 뜻하지 않습니다.')
        return
    shown=frame[['name','streak_start','latest_net_qty','streak_net_qty','reference_net_amount']].copy()
    shown.insert(1,'확인된 연속',frame.apply(lambda r:f"{'최소 ' if r.lower_bound else ''}{r.streak_days}거래일",axis=1))
    shown['자료 상태']=frame.provisional.map({True:'잠정 포함',False:'확정'})
    shown['reference_net_amount']=shown.reference_net_amount/1e8
    shown=shown.rename(columns={'name':'종목명','streak_start':'확인 시작일','latest_net_qty':'마지막 날 순매수(주)',
                                'streak_net_qty':'연속기간 순매수(주)','reference_net_amount':'종가 환산 참고금액(억)'})
    st.dataframe(shown.style.format({'마지막 날 순매수(주)':'{:+,.0f}','연속기간 순매수(주)':'{:+,.0f}',
                                     '종가 환산 참고금액(억)':'{:+,.2f}'},na_rep='종가 미확보'),hide_index=True,**layout_width())


def render_stock_net_history(data,ticker,cutoff):
    frame=build_stock_net_history(data,ticker,cutoff)
    st.markdown('#### 최근 10거래일 · 거래원별 순매수수량(주)')
    st.caption('조회 기간과 별도로 마지막 선택 거래일까지 최근 10거래일을 표시합니다. +는 순매수, −는 순매도입니다. 합산은 해당 날짜의 모든 외국계 거래원 자료가 있을 때만 계산합니다. 미수집은 0주가 아닙니다. 모바일에서는 좌우로 밀어 확인하세요.')
    headers='<th>거래원</th>'+''.join('<th>'+html.escape(d[4:6]+'/'+d[6:8])+'</th>' for d in frame.columns)
    rows=[]
    for name,row in frame.iterrows():
        cells=[]
        for value in row:
            text='미수집' if pd.isna(value) else f'{value:+,.0f}' if value else '0'
            color='#777' if pd.isna(value) or value==0 else '#177a48' if value>0 else '#c43b40'
            cells.append(f'<td style="color:{color}">{text}</td>')
        rows.append('<tr><th>'+html.escape(str(name))+'</th>'+''.join(cells)+'</tr>')
    st.markdown('<style>.broker-history{overflow-x:auto;width:100%;-webkit-overflow-scrolling:touch}.broker-history table{border-collapse:collapse;font-size:13px}.broker-history th,.broker-history td{white-space:nowrap;min-width:90px;padding:7px;border-bottom:1px solid #ddd;text-align:right}.broker-history th:first-child{position:sticky;left:0;background:var(--background-color,#fff);z-index:1;min-width:110px;text-align:left}</style>'
                +'<div class="broker-history"><table><thead><tr>'+headers+'</tr></thead><tbody>'+''.join(rows)+'</tbody></table></div>',unsafe_allow_html=True)


def render_broker_stock_search(details, totals, raw, members, data, cutoff):
    st.subheader('① 종목별 외국계 거래원')
    catalog=data['universe'][data['universe'].trade_date.astype(str)<=str(cutoff)].sort_values('trade_date').drop_duplicates('ticker',keep='last').sort_values('name')
    options = catalog.ticker.tolist()
    names = dict(zip(catalog.ticker,catalog.name))
    if st.session_state.get('foreign_stock') not in options:
        st.session_state['foreign_stock']=None
    chosen = st.selectbox('종목 검색',options,index=None,format_func=lambda t:f'{names[t]} ({t})',
                          placeholder='종목명 또는 코드로 검색',key='foreign_stock')
    if chosen:
        st.markdown(f'**{names[chosen]} ({chosen}) · 거래원별 매수·매도 이력**')
        period=st.radio('종목 상세 조회 기간',['최근 10거래일','최근 20거래일','수집 이후 전체(웹 보관분)'],
                        horizontal=True,key='foreign_stock_history_period')
        days=10 if period=='최근 10거래일' else 20 if period=='최근 20거래일' else None
        summary,history,history_dates=build_stock_trade_details(data,chosen,cutoff,days)
        if history_dates:
            st.caption(f'{history_dates[0][:4]}/{history_dates[0][4:6]}/{history_dates[0][6:]} ~ '
                       f'{history_dates[-1][:4]}/{history_dates[-1][4:6]}/{history_dates[-1][6:]} · {len(history_dates)}거래일. '
                       '상단 전체 순위 조회 기간과 별도입니다. 수집 시작 전은 제외하며 웹은 최근 최대 60거래일을 보관합니다.')
            st.caption('누적 순매수 = 이 기간 매수−매도 합계이며 실제 보유수량이 아닙니다. 누락이 있으면 확보분의 부분합으로 표시합니다. 합산은 모든 외국계 거래원 자료가 있는 날짜만 포함합니다. 완비된 거래원을 먼저, 그 안에서 누적 순매수수량 큰 순으로 표시합니다.')
            display=summary[['broker_name','buy_qty','sell_qty','net_qty']].rename(columns={
                'broker_name':'거래원','buy_qty':'매수 합계(주)','sell_qty':'매도 합계(주)','net_qty':'누적 순매수(주)'})
            display['확보일수']=summary.observed_days.astype(str)+'/'+summary.expected_days.astype(str)
            display['합계 구분']=summary.complete.map({True:'기간 합계',False:'확보분 부분합'})
            display['자료 상태']=summary.provisional.map({True:'잠정 포함',False:'확정분'})
            display.loc[summary.observed_days.eq(0),'자료 상태']='미수집'
            st.dataframe(display.style.format({'매수 합계(주)':'{:,.0f}','매도 합계(주)':'{:,.0f}',
                                              '누적 순매수(주)':'{:+,.0f}'},na_rep='미수집'),hide_index=True,**layout_width())
            st.markdown('#### 거래원별 일자 내역 · 클릭해서 펼치기')
            ordered=pd.concat([summary[summary.broker_code=='__ALL__'],summary[summary.broker_code!='__ALL__']])
            for member in ordered.itertuples():
                quantity='미수집' if pd.isna(member.net_qty) else f'{member.net_qty:+,.0f}주'
                label=('누적 ' if member.complete else '확보분 ')+quantity
                if member.provisional:
                    label+=' · 잠정 포함'
                with st.expander(f'{member.broker_name} · {label}'):
                    days_frame=history[history.broker_code==member.broker_code].sort_values('trade_date',ascending=False).copy()
                    shown=pd.DataFrame({'날짜':pd.to_datetime(days_frame.trade_date,format='%Y%m%d').dt.strftime('%Y/%m/%d'),
                                        '매수(주)':days_frame.buy_qty,'매도(주)':days_frame.sell_qty,
                                        '순매수(주)':days_frame.net_qty,'자료 상태':days_frame.status})
                    def cumulative(row):
                        value=row.cumulative_observed_net_qty
                        if pd.isna(value): return '미수집'
                        text=f'{value:+,.0f}'
                        if not row.cumulative_complete: text+=' (부분합)'
                        if row.provisional_to_date: text+=' · 잠정 포함'
                        return text
                    shown['기간 시작일부터 누적(주)']=days_frame.apply(cumulative,axis=1)
                    shown=shown[['날짜','매수(주)','매도(주)','순매수(주)','기간 시작일부터 누적(주)','자료 상태']]
                    st.caption('최신 날짜부터 표시합니다. 누적값은 위 조회 기간의 첫 거래일부터 해당 날짜까지 계산합니다.')
                    st.dataframe(shown.style.format({'매수(주)':'{:,.0f}','매도(주)':'{:,.0f}','순매수(주)':'{:+,.0f}'},na_rep='미수집'),hide_index=True,**layout_width())
        else:
            st.info('이 기간에는 조회 가능한 거래일이 없습니다.')
        stock = details[details.ticker==chosen].copy()
        stock.loc[~stock.complete, 'reference_net_amount'] = float('nan')
        stock = stock.sort_values(['reference_net_amount','broker_code'],ascending=[False,True],na_position='last')
        with st.expander('상단 조회 기간 기준 · 종가 환산 참고금액'):
            if stock.empty:
                st.info('상단 조회 기간의 수집 대상에 없습니다.')
            else:
                show_quantities(stock)
        with st.expander('최근 10거래일 순매수수량 비교표'):
            render_stock_net_history(data,chosen,cutoff)
    else:
        st.caption('수집 이력이 있는 종목명·6자리 코드로 검색 후 검색 결과를 선택하세요. 결과가 없는 종목은 아직 수집 이력이 없는 경우입니다.')


@st.fragment
def render_foreign_broker_tab(path, version, day):
    st.header('🏦 외국계 거래원')
    st.caption('KRX 회원사 기준 · 순위는 종가 환산 순매수 참고금액 기준입니다. 매수는 양수 큰 순, 매도는 음수 절댓값 큰 순입니다. 외국계 창구는 외국인 투자자와 다릅니다. 검색·기간 변경은 이 탭 안에서만 갱신합니다.')
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
    st.info('참고금액 = Σ(각 날짜 순매수수량 × 해당 날짜 KRX 종가). 실제 거래금액·매입단가·보유잔고가 아닙니다. 기간 전체 수량과 모든 날짜 종가가 있는 자료만 금액 순위에 포함합니다. 장중에는 당일 종가가 없어 순위가 비어 있을 수 있으며, 종가 수집 후 표시됩니다. 수량 순위로 대신 채우지 않습니다.')
    ranked = complete[complete.reference_net_amount.notna()]
    st.caption(f'전체 금액 순위 비교 가능: {len(ranked)}/{len(totals)}종목 · 수량 완비이나 종가 미확보: {len(complete)-len(ranked)}종목. 기간 누적 금액과 수량의 부호는 날짜별 가격 차이로 다를 수 있습니다.')
    render_broker_stock_search(details, totals, raw, data['members'], data, dates[-1])
    st.subheader('② 외국계 합산 순매수 TOP10')
    buy = rank_reference_amount(totals)
    if buy.empty:
        st.info('기간 전체 수량·종가가 확보된 양수 참고금액 종목이 없습니다.')
    else:
        show_quantities(buy, 'name')
    st.markdown('#### 외국계 합산 · 연속 순매수 종목')
    st.caption('선택한 마지막 거래일까지 매일 순매수수량 > 0인 종목 · 2거래일 이상 · 연속일수 우선, 같으면 참고금액순. 조회 기간 시작일과는 무관하게 저장 이력을 확인합니다. ‘최소’는 그 이전 자료가 없어 더 긴 연속 여부를 확인할 수 없다는 뜻입니다. 당일 잠정 자료가 포함되면 표시합니다.')
    show_streaks(build_broker_streaks(data,dates[-1],combined=True))
    st.subheader('③ 외국계 합산 순매도 TOP10')
    sell = rank_reference_amount(totals, buying=False)
    if sell.empty:
        st.info('기간 전체 수량·종가가 확보된 음수 참고금액 종목이 없습니다.')
    else:
        show_quantities(sell, 'name')
    st.subheader('④ 거래원별 순매수 · 순매도 상위 종목')
    broker_streaks=build_broker_streaks(data,dates[-1])
    for member in data['members'].sort_values('broker_name').itertuples():
        with st.expander(member.broker_name):
            st.write('2거래일 이상 연속 순매수 종목')
            show_streaks(broker_streaks[broker_streaks.broker_code==member.broker_code])
            broker = details[details.broker_code==member.broker_code]
            subset = broker[broker.complete]
            st.caption(f'기간 전체 수량 확보 {len(subset)}/{len(broker)}종목 · 금액 비교 가능 {int(subset.reference_net_amount.notna().sum())}종목. 미수집은 0주가 아닙니다.')
            st.write('순매수 TOP10')
            buys = rank_reference_amount(broker)
            if buys.empty:
                st.info('수량·종가가 확보된 양수 참고금액 종목 없음')
            else:
                show_quantities(buys,'name')
            st.write('순매도 TOP10')
            sells = rank_reference_amount(broker, buying=False)
            if sells.empty:
                st.info('수량·종가가 확보된 음수 참고금액 종목 없음')
            else:
                show_quantities(sells,'name')
    with st.expander('수집 방식·주의사항'):
        st.write('대상은 웹 조회에 사용하는 순위 종목·모델 분석 대상·ETF·관심종목의 합집합입니다. 같은 종목은 한 번만 수집합니다. 전체 시장 순위가 아닙니다.')
        st.write('개별 외국계 회원사를 조회하며 외국계합(99999) 집계코드를 다시 더하지 않습니다. 수량은 API 일별 자료이고, 금액은 각 날짜 종가 환산 참고치입니다.')
        st.write('처음 수집한 날짜부터 기록합니다. 기간 중 수집 대상에서 빠진 날은 미수집으로 남으며, 그 종목은 기간 전체 확보 TOP10에서 제외됩니다. 수집 대상 기록·일별 수량은 서버 최대 260거래일, 웹 조회는 최근 최대 60거래일입니다.')
