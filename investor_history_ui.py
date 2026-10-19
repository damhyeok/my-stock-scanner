"""Eight-date ranking comparison from existing bounded web stock snapshots."""
import html
import pandas as pd
import streamlit as st


def build_investor_history(raw, selected_date, session, kind, trading_dates=()):
    category, amount = {'foreign':('FOREIGN_TOP_30','foreign_net'),
                        'institution':('INST_TOP_30','inst_net')}[kind]
    known = trading_dates if len(trading_dates) else raw['date'].astype(str).unique()
    dates = sorted({str(d) for d in known if str(d) <= str(selected_date)},reverse=True)[:8]
    history = raw[(raw['date'].astype(str).isin(dates)) & (raw['session']==session)
                  & (raw['category']==category)].copy()
    history[amount] = pd.to_numeric(history[amount],errors='coerce')
    history = history.sort_values(['date',amount,'ticker'],ascending=[False,False,True])
    rows = []
    for rank in range(30):
        row = {'순위':rank+1}
        for day in dates:
            stocks = history[history['date'].astype(str)==day].drop_duplicates('ticker').head(30)
            if rank >= len(stocks):
                row[day] = '미수집 / 순위 없음'
                continue
            stock = stocks.iloc[rank]
            value = stock[amount]
            # daily_stocks investor amounts are KIS million-KRW units, not won.
            money = f'{value/100:+,.1f}억' if pd.notna(value) else '금액 미수집'
            rate = pd.to_numeric(stock.get('fluctuation_rate'),errors='coerce')
            change = f' · {rate:+.1f}%' if pd.notna(rate) else ''
            row[day] = f'{stock["name"]}\n{money}{change}'
        rows.append(row)
    return pd.DataFrame(rows) if dates else pd.DataFrame()


def render_investor_history(raw, selected_date, session, kind, trading_dates=()):
    table = build_investor_history(raw,selected_date,session,kind,trading_dates)
    st.caption('최근 8거래일 · 각 날짜의 같은 분석시간 TOP30 비교 · 순매수금액(억) / 당일 등락률. 날짜별 순위이며 같은 행이 같은 종목을 뜻하지 않습니다. 휴장일은 제외하고, 미수집 날짜는 과거 값으로 대체하지 않습니다. 모바일에서는 표를 좌우로 밀어 확인하세요.')
    if table.empty:
        st.info('비교할 거래일 데이터가 없습니다.')
        return
    def show(frame):
        headers = ''.join('<th>'+html.escape(str(c) if c=='순위' else c[4:6]+'/'+c[6:8])+'</th>' for c in frame.columns)
        body = ''.join('<tr>'+''.join('<td>'+html.escape(str(v)).replace('\n','<br>')+'</td>' for v in row)+'</tr>' for row in frame.itertuples(index=False,name=None))
        st.markdown('<style>.investor-history{overflow-x:auto;width:100%;-webkit-overflow-scrolling:touch}.investor-history table{border-collapse:collapse;font-size:13px}.investor-history th,.investor-history td{min-width:145px;padding:7px;border-bottom:1px solid #ddd;white-space:nowrap;vertical-align:top}.investor-history th:first-child,.investor-history td:first-child{min-width:40px;position:sticky;left:0;background:var(--background-color,#fff);z-index:1}</style>'
                    +'<div class="investor-history"><table><thead><tr>'+headers+'</tr></thead><tbody>'+body+'</tbody></table></div>',unsafe_allow_html=True)
    show(table.head(10))
    with st.expander('11~30위 펼쳐보기'):
        show(table.iloc[10:])
