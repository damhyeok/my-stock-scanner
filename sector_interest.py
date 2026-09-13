"""Display-only sector interest comparisons from saved daily summaries."""

import pandas as pd


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
        recent = history.tail(3)
        prior = history.iloc[:-3].tail(5)
        enough = recent['share'].notna().all() and len(prior) >= 3 and prior['share'].notna().all()
        change = recent['share'].mean() - prior['share'].mean() if enough else float('nan')
        baseline = prior['trading_value'].mean()
        activity = recent['trading_value'].mean() / baseline if enough and baseline > 0 else float('nan')
        broad_change = recent['breadth'].mean() - prior['breadth'].mean() if enough else float('nan')
        broad = latest['stock_count'] >= 3 and latest['breadth'] >= 60
        if not enough:
            status, reason = '판단 자료 부족', '비교 가능한 최근 3일·이전 3~5일 기록이 부족합니다.'
        elif latest['stock_count'] < 3:
            status, reason = '일부 종목에 집중', '표본이 1~2종목이라 업종 전반의 강세로 보기 어렵습니다.'
        elif change >= 1 and broad and broad_change >= 0:
            status, reason = '관심 증가 중', '거래 비중이 늘고, 상승 종목 비율도 유지되거나 확대됐습니다.'
        elif change <= -1 and broad_change < 0:
            status, reason = '관심 약화 중', '거래 비중과 상승 종목 비율이 함께 줄었습니다.'
        elif broad:
            status, reason = '동반 상승 관찰', '표본 종목의 60% 이상이 상승했습니다. 관심 증가 여부는 더 확인합니다.'
        else:
            status, reason = '혼조·추가 확인', '거래 관심과 동반 상승 방향이 아직 뚜렷하지 않습니다.'
        records.append(dict(sector=latest['sector'], status=status, reason=reason,
                            share=latest['share'], change=change, activity=activity,
                            breadth=latest['breadth'], broad_change=broad_change,
                            rising_count=latest['rising_count'], stock_count=latest['stock_count'],
                            included_stocks=latest['included_stocks']))
    result = pd.DataFrame(records)
    result['priority'] = result['status'].map({'관심 증가 중': 0, '동반 상승 관찰': 1,
        '혼조·추가 확인': 2, '일부 종목에 집중': 3, '관심 약화 중': 4, '판단 자료 부족': 5})
    result = result.sort_values(['priority', 'change', 'share', 'sector'], ascending=[True, False, False, True]).head(count)
    sectors = result['sector'].tolist()
    path = source.set_index(['date', 'sector']).reindex(pd.MultiIndex.from_product([dates, sectors], names=['date', 'sector'])).reset_index()
    return result, path


def render_interest(st, summary, count):
    import altair as alt

    st.subheader('최근 관심이 커지는 섹터')
    st.caption('정규장 마감 기준 최근 10거래일 · 기본 8개, 왼쪽 설정으로 변경 가능. 거래대금 TOP60 중 업종이 확인된 종목의 거래 비중입니다. 순매수 금액이나 급등 예측이 아닙니다.')
    rows, path = build_interest(summary, count)
    if rows.empty:
        st.info('저장된 섹터 데이터가 없습니다.')
        return
    st.caption('관심 증가·동반 상승 후보부터 표시합니다. 아래 기존 거래대금 순위 그래프와 선정 섹터가 다를 수 있습니다.')
    display = pd.DataFrame({
        '섹터': rows['sector'], '현재 상태': rows['status'], '한 줄 이유': rows['reason'],
        '최근 거래 비중 변화': rows['change'].map(lambda x: '자료 부족' if pd.isna(x) else f'{x:+.1f}%p'),
        '상승 종목 / 표본': [f'{int(a)} / {int(b)}' for a, b in zip(rows['rising_count'], rows['stock_count'])],
    })
    st.dataframe(display, hide_index=True, use_container_width=True)
    st.markdown('**어느 섹터의 거래 비중이 커지고 있나요?**')
    mode = st.radio('그래프 기준', ['비중 변화폭', '실제 비중'], horizontal=True,
                    key='interest_chart_mode')
    first_date = path['date'].min()
    baseline = path[path['date'].eq(first_date)].set_index('sector')['share']
    path['share_change'] = path['share'] - path['sector'].map(baseline)
    change_mode = mode == '비중 변화폭'
    field = 'share_change' if change_mode else 'share'
    axis_title = '첫날 대비 거래 비중 변화(%p)' if change_mode else '분석 표본 내 거래대금 비중(%)'
    if change_mode:
        st.caption(f'비교 시작일 {first_date}의 비중을 0으로 맞췄습니다. +4%p는 예를 들어 5%에서 9%로 늘었다는 뜻입니다. 위 요약표의 최근 평균 비교와는 기준이 다릅니다.')
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
    st.altair_chart(chart, use_container_width=True)
    st.caption('선이 올라갈수록 표본 내 거래 관심이 커집니다. 전체 거래가 줄어도 비중은 오를 수 있습니다. 선이 끊긴 날은 TOP60 표본에서 관측되지 않은 날이며, 업종 거래가 0이라는 뜻은 아닙니다.')
    with st.expander('섹터별 자세히 보기 · 계산 기준'):
        sector = st.selectbox('확인할 섹터', rows['sector'].tolist(), key='interest_sector')
        row = rows[rows['sector'].eq(sector)].iloc[0]
        st.write(row['reason'])
        if pd.notna(row['activity']):
            st.write(f"최근 3일 평균 거래대금은 이전 최대 5일 평균의 {row['activity']:.2f}배입니다.")
        st.write(f"최근 마감 표본: {int(row['stock_count'])}종목 중 {int(row['rising_count'])}종목 상승")
        st.write('포함 종목: ' + str(row['included_stocks']))
        st.markdown('거래 비중 변화는 **최근 3거래일 평균 − 그 이전 최대 5거래일 평균**입니다. 최소 6거래일의 연속 표본이 필요합니다. 관심 증가는 비중 +1%p 이상, 표본 3종목 이상, 당일 상승 비율 60% 이상, 최근 상승 비율 유지·확대를 함께 확인합니다. 관심 약화는 비중 −1%p 이하와 상승 비율 하락을 함께 확인합니다. 이 기준은 초기 관찰 규칙입니다.')
        st.caption('매일 TOP60 구성 종목이 달라집니다. 시장 대비 수익률·누적 업종 수익률·과열 여부는 이 요약만으로 정확히 계산할 수 없어 판정에 포함하지 않습니다. 전체 업종보다 거래가 활발한 종목에 편향될 수 있습니다.')
