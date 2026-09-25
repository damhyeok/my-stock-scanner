"""Display-only sector interest comparisons from saved daily summaries."""

from streamlit_layout import layout_width
import pandas as pd
import sqlite3
import html
from contextlib import closing


def build_interest(summary, count=8):
    source = summary[summary['trend_kind'].eq('ALL')].copy()
    if source.empty:
        return pd.DataFrame(), pd.DataFrame()
    dates = sorted(source['date'].astype(str).unique())[-10:]
    source['date'] = source['date'].astype(str)
    source = source[source['date'].isin(dates)]
    for col in ['trading_value', 'stock_count']:
        source[col] = pd.to_numeric(source[col], errors='coerce').fillna(0)
    rising = summary[summary['trend_kind'].eq('RISING')][['date', 'sector', 'stock_count']].copy()
    rising['date'] = rising['date'].astype(str)
    rising = rising.rename(columns={'stock_count': 'rising_count'})
    source = source.merge(rising, on=['date', 'sector'], how='left', validate='one_to_one')
    source['rising_count'] = source['rising_count'].fillna(0)
    totals = source.groupby('date')['trading_value'].sum()
    source['share'] = source['trading_value'] / source['date'].map(totals).replace(0, float('nan')) * 100
    source['breadth'] = source['rising_count'] / source['stock_count'].replace(0, float('nan')) * 100
    records = []
    for _, latest in source[source['date'].eq(dates[-1])].iterrows():
        history = source[source['sector'].eq(latest['sector'])].set_index('date').reindex(dates)
        # Absence means outside this TOP60 sample, not zero sector turnover.
        previous = history.iloc[-2]['share'] if len(history) >= 2 else float('nan')
        change = latest['share'] - previous
        if pd.isna(change):
            status, reason = '비교 자료 부족', '직전 거래일 또는 최근 마감 비중 자료가 없습니다.'
        elif change > 0:
            status, reason = '직전일 대비 비중 증가', '직전 거래일보다 표본 내 거래 비중이 높아졌습니다.'
        elif change < 0:
            status, reason = '직전일 대비 비중 감소', '직전 거래일보다 표본 내 거래 비중이 낮아졌습니다.'
        else:
            status, reason = '직전일 대비 동일', '직전 거래일과 표본 내 거래 비중이 같습니다.'
        records.append(dict(sector=latest['sector'], status=status, reason=reason,
                            share=latest['share'], change=change,
                            breadth=latest['breadth'],
                            rising_count=latest['rising_count'], stock_count=latest['stock_count'],
                            included_stocks=latest['included_stocks']))
    result = pd.DataFrame(records)
    result = result.sort_values(['share', 'sector'], ascending=[False, True]).head(count)
    sectors = result['sector'].tolist()
    path = source.set_index(['date', 'sector']).reindex(pd.MultiIndex.from_product([dates, sectors], names=['date', 'sector'])).reset_index()
    return result, path


def sector_member_table(db_path, date, sectors):
    with closing(sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)) as connection:
        members = pd.read_sql_query(
            "SELECT ticker, name, sector, fluctuation_rate FROM daily_stocks "
            "WHERE date=? AND session='정규장(16:00)' AND category='VOLUME_TOP_60'",
            connection, params=(str(date),))
    members = members.drop_duplicates('ticker').copy()
    members['rate'] = pd.to_numeric(members['fluctuation_rate'], errors='coerce')
    rows = []
    for sector in sectors:
        selected = members[members['sector'].eq(sector)].sort_values('rate', ascending=False)
        rising, other = [], []
        for _, member in selected.iterrows():
            rate = member['rate']
            label = html.escape(str(member['name']))
            if pd.isna(rate):
                other.append(f'{label} (등락률 자료 없음)')
            else:
                color = '#c62828' if rate > 0 else '#1565c0' if rate < 0 else '#666666'
                text = f'{label} <span style="color:{color}">{rate:+.2f}%</span>'
                (rising if rate > 0 else other).append(text)
        heading = html.escape(str(sector)) + f'<br><small>포함 {len(selected)} · 상승 {len(rising)}</small>'
        rows.append('<tr>' + ''.join(f'<td>{cell}</td>' for cell in [heading, '<br>'.join(rising) or '—', '<br>'.join(other) or '—']) + '</tr>')
    return '<div class="sector-members"><style>.sector-members table{width:100%;table-layout:fixed;border-collapse:collapse}.sector-members td,.sector-members th{padding:8px;border-bottom:1px solid #ddd;text-align:left;vertical-align:top;overflow-wrap:anywhere}.sector-members th:first-child{width:24%}</style><table><thead><tr><th>섹터</th><th>상승 종목</th><th>보합·하락·자료 없음</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>'


def render_interest(st, summary, count, db_path=None):
    import altair as alt

    st.subheader('최근 섹터 거래 비중과 포함 종목')
    st.caption('정규장 마감 기준 최근 10거래일 · 기본 8개, 왼쪽 설정으로 변경 가능. 거래대금 TOP60 중 업종이 확인된 종목의 거래 비중입니다. 순매수 금액이나 급등 예측이 아닙니다.')
    rows, path = build_interest(summary, count)
    if rows.empty:
        st.info('저장된 섹터 데이터가 없습니다.')
        return
    st.caption('최근 마감 거래 비중이 큰 섹터부터 표시합니다. 지속적인 관심 증가는 10거래일 흐름과 포함 종목을 함께 확인하세요.')
    display = pd.DataFrame({
        '섹터': rows['sector'], '현재 상태': rows['status'], '한 줄 이유': rows['reason'],
        '최근 마감 비중(%)': rows['share'].map(lambda x: '자료 부족' if pd.isna(x) else f'{x:.2f}%'),
        '직전 거래일 대비(%p)': rows['change'].map(lambda x: '자료 부족' if pd.isna(x) else f'{x:+.2f}%p'),
        '상승 종목 / 표본': [f'{int(a)} / {int(b)}' for a, b in zip(rows['rising_count'], rows['stock_count'])],
    })
    st.dataframe(display, hide_index=True, **layout_width())
    dates = sorted(path['date'].unique())
    st.caption(f'최근 마감 비중은 그래프 마지막 날({dates[-1]})의 실제 비중과 같습니다.')
    if len(dates) >= 2:
        st.caption(f'직전 거래일 대비 = {dates[-1]} 비중 − {dates[-2]} 비중. 예: 8% − 5% = +3%p. 해당 섹터의 직전일 자료가 없으면 비교하지 않습니다.')
    if db_path:
        st.markdown('**섹터별 포함 종목과 당일 등락률**')
        st.caption(f'{dates[-1]} 정규장 TOP60 표본 기준 · 전일 종가 대비 등락률 · 실시간값이 아닙니다. 반도체 메모리 숨기기는 아래 그래프에만 적용됩니다.')
        st.markdown(sector_member_table(db_path, dates[-1], rows['sector'].tolist()), unsafe_allow_html=True)
    st.markdown('**어느 섹터의 거래 비중이 커지고 있나요?**')
    mode = st.radio('그래프 기준', ['실제 비중', '비중 변화폭'], horizontal=True,
                    key='interest_chart_mode_v2')
    hide_memory = st.checkbox('반도체·메모리 숨기기', value=True,
                              key='interest_hide_memory')
    first_date = path['date'].min()
    baseline = path[path['date'].eq(first_date)].set_index('sector')['share']
    path['share_change'] = path['share'] - path['sector'].map(baseline)
    # Filter only the plotted rows, after shares and baselines are calculated.
    # The denominator, candidate selection and summary table remain intact.
    if hide_memory:
        path = path[~path['sector'].eq('반도체 메모리')].copy()
        baseline = baseline.drop('반도체 메모리', errors='ignore')
    st.caption('숨기기는 그래프의 선에만 적용됩니다. 반도체 메모리 거래대금은 비중 계산에 계속 포함되며, 요약표에도 남습니다.')
    change_mode = mode == '비중 변화폭'
    field = 'share_change' if change_mode else 'share'
    axis_title = '첫날 대비 거래 비중 변화(%p)' if change_mode else '분석 표본 내 거래대금 비중(%)'
    if change_mode:
        st.caption(f'비교 시작일 {first_date}의 비중을 0으로 맞췄습니다. +4%p는 예를 들어 5%에서 9%로 늘었다는 뜻입니다. 위 요약표의 직전 거래일 비교와는 기준이 다릅니다.')
        missing = baseline[baseline.isna()].index.tolist()
        if missing:
            st.info('시작일 자료가 없어 변화폭을 표시하지 않는 섹터: ' + ', '.join(missing) + '. 실제 비중에서 확인할 수 있습니다.')
    # Break lines at absent dates instead of connecting across missing samples.
    path['segment'] = path.groupby('sector')['share'].transform(lambda x: x.isna().cumsum())
    chart = alt.Chart(path).mark_line(point=True).encode(
        x=alt.X('date:O', title='거래일'),
        y=alt.Y(f'{field}:Q', title=axis_title),
        color=alt.Color('sector:N', title='섹터', sort=rows['sector'].tolist()),
        detail='segment:N',
        tooltip=[alt.Tooltip('date:O', title='날짜'), alt.Tooltip('sector:N', title='섹터'),
                 alt.Tooltip('share:Q', title='거래 비중(%)', format='.1f'),
                 alt.Tooltip('share_change:Q', title='첫날 대비 변화(%p)', format='+.1f'),
                 alt.Tooltip('stock_count:Q', title='표본 종목 수', format='.0f')],
    ).properties(height=380)
    if change_mode:
        zero = alt.Chart(pd.DataFrame({'zero': [0]})).mark_rule(color='#888888', strokeDash=[4, 4]).encode(y='zero:Q')
        chart = chart + zero
    st.altair_chart(chart, **layout_width())
    st.caption('선이 올라갈수록 표본 내 거래 관심이 커집니다. 전체 거래가 줄어도 비중은 오를 수 있습니다. 선이 끊긴 날은 TOP60 표본에서 관측되지 않은 날이며, 업종 거래가 0이라는 뜻은 아닙니다.')
    with st.expander('섹터별 자세히 보기 · 계산 기준'):
        sector = st.selectbox('확인할 섹터', rows['sector'].tolist(), key='interest_sector')
        row = rows[rows['sector'].eq(sector)].iloc[0]
        st.write(row['reason'])
        st.write(f"최근 마감 표본: {int(row['stock_count'])}종목 중 {int(row['rising_count'])}종목 상승")
        st.write('포함 종목: ' + str(row['included_stocks']))
        st.markdown('비중은 해당 업종 거래대금 ÷ 업종이 확인된 TOP60 표본의 전체 거래대금입니다. 상태는 직전 거래일 대비 비중의 증가·감소만 표시하며, 꾸준한 자금 유입이나 향후 상승을 판정하지 않습니다.')
        st.caption('매일 TOP60 구성 종목이 달라집니다. 시장 대비 수익률·누적 업종 수익률·과열 여부는 이 요약만으로 정확히 계산할 수 없어 판정에 포함하지 않습니다. 전체 업종보다 거래가 활발한 종목에 편향될 수 있습니다.')
