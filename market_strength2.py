"""Bounded, descriptive regular-session evidence; no overnight prediction score."""
import json
import math
from contextlib import closing
import pandas as pd


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def summarize_index(frame):
    frame = frame.copy()
    frame['bar_time'] = frame['bar_time'].astype(str).str[:5]
    frame = frame[(frame.bar_time >= '09:00') & (frame.bar_time <= '15:30')]
    frame['close'] = pd.to_numeric(frame['close'], errors='coerce')
    frame = frame[frame.close.gt(0)].sort_values('bar_time').drop_duplicates('bar_time', keep='last')
    expected = pd.date_range('2000-01-01 14:30', '2000-01-01 15:19', freq='min').strftime('%H:%M').tolist()
    late = frame[frame.bar_time.isin(expected)]
    prices = dict(zip(frame.bar_time, frame.close))
    complete = all(t in prices for t in expected) and '15:30' in prices
    result = {'minutes': len(late), 'expected_minutes': 50, 'complete': complete,
              'late_return': None, 'auction_return': None, 'clv': None,
              'flow': '분석 불충분', 'points': []}
    result['points'] = [{'시간': r.bar_time, '지수': float(r.close)} for r in frame.itertuples()
                        if r.bar_time >= '14:30']
    # Require a continuous-day envelope as well; never call a partial range the day's range.
    day_expected = pd.date_range('2000-01-01 09:00', '2000-01-01 15:19', freq='min').strftime('%H:%M')
    if complete and all(t in prices for t in day_expected):
        high = pd.to_numeric(frame['high'], errors='coerce').max()
        low = pd.to_numeric(frame['low'], errors='coerce').min()
        if pd.notna(high) and pd.notna(low) and high > low and low <= prices['15:30'] <= high:
            result['clv'] = (prices['15:30'] - low) / (high - low) * 100
    if complete:
        result['late_return'] = (prices['15:30'] / prices['14:30'] - 1) * 100
        # 15:19 is the last continuous-session minute; use 15:20 when provided.
        baseline = prices.get('15:20', prices['15:19'])
        result['auction_baseline'] = '15:20' if '15:20' in prices else '15:19'
        result['auction_return'] = (prices['15:30'] / baseline - 1) * 100
        first, last = late.close.iloc[:15], late.close.iloc[-15:]
        result['flow'] = ('개선' if last.mean() > first.mean() and last.min() >= first.min()
                          else '약화' if last.mean() < first.mean() and last.min() < first.min()
                          else '혼조')
        result['first_mean'] = float(first.mean())
        result['last_mean'] = float(last.mean())
        result['first_low'] = float(first.min())
        result['last_low'] = float(last.min())
    return result


def summarize_supply(frame):
    frame = frame.sort_values('snapshot_time').drop_duplicates('snapshot_time', keep='last')
    records = {str(r.snapshot_time)[:5]: r for r in frame.itertuples()}
    result = {'program_delta': None, 'non_arbitrage_delta': None, 'basis_delta': None,
              'basis_last': None, 'points': []}
    for row in frame.itertuples():
        result['points'].append({'시간': str(row.snapshot_time)[:5],
                                 '프로그램 누적': number(row.program_net),
                                 '비차익 누적': number(row.non_arbitrage_net)})
    if '14:30' in records and '15:30' in records:
        start, end = records['14:30'], records['15:30']
        for output, column in [('program_delta', 'program_net'), ('non_arbitrage_delta', 'non_arbitrage_net'),
                               ('basis_delta', 'basis')]:
            a, b = number(getattr(start, column)), number(getattr(end, column))
            if a is not None and b is not None:
                result[output] = b - a
        result['basis_last'] = number(end.basis)
    return result


def build_market_strength2_display(conn):
    """Materialize before raw minutes are removed from the web snapshot."""
    exists = lambda table: conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    conn.execute('CREATE TABLE IF NOT EXISTS web_market_strength2 (trade_date TEXT PRIMARY KEY, payload TEXT NOT NULL)')
    if not exists('market_strength_snapshots'):
        return
    dates = [r[0] for r in conn.execute("SELECT DISTINCT trade_date FROM market_strength_snapshots WHERE analysis_type='closing' ORDER BY trade_date DESC LIMIT 30")]
    for date in dates:
        strength = pd.read_sql_query("SELECT * FROM market_strength_snapshots WHERE trade_date=? AND analysis_type='closing' ORDER BY snapshot_time", conn, params=(date,))
        payload = {'supply': summarize_supply(strength), 'indices': {}}
        if exists('intraday_index_bars'):
            bars = pd.read_sql_query('SELECT * FROM intraday_index_bars WHERE trade_date=? AND bar_time<=?', conn, params=(date, '15:30'))
            for name, group in bars.groupby('index_name'):
                if name in ('KOSPI', 'KOSDAQ', 'KOSPI200'):
                    payload['indices'][name] = summarize_index(group)
        # Keep older materialized history when its raw bars have already expired.
        old = conn.execute('SELECT payload FROM web_market_strength2 WHERE trade_date=?', (date,)).fetchone()
        if not payload['indices'] and old:
            payload['indices'] = json.loads(old[0]).get('indices', {})
        conn.execute('INSERT OR REPLACE INTO web_market_strength2 VALUES (?,?)', (date, json.dumps(payload, ensure_ascii=False, allow_nan=False)))
    conn.execute('DELETE FROM web_market_strength2 WHERE trade_date NOT IN (SELECT trade_date FROM web_market_strength2 ORDER BY trade_date DESC LIMIT 30)')


def interpret_market_environment(payload):
    """Explain agreement/conflict in existing evidence, not a buy signal."""
    supply = payload.get('supply', {})
    close = next((p for p in reversed(supply.get('points', [])) if p.get('시간') == '15:30'), {})
    cumulative = [number(close.get(k)) for k in ['프로그램 누적', '비차익 누적']]
    changes = [number(supply.get(k)) for k in ['program_delta', 'non_arbitrage_delta']]
    indices = payload.get('indices', {})
    missing = []
    if any(v is None for v in cumulative + changes):
        missing.append('마감 누적 수급 또는 마지막 60분 수급')
    for name in ['KOSPI', 'KOSDAQ']:
        item = indices.get(name, {})
        if (not item.get('complete') or number(item.get('clv')) is None
                or number(item.get('late_return')) is None or number(item.get('auction_return')) is None
                or item.get('flow') not in ['개선', '혼조', '약화']):
            missing.append(f'{name}의 장 후반 흐름·마감 위치')
    if missing:
        return {'label': '데이터 부족 · 종합 판단 보류', 'level': 'info',
                'reason': ' / '.join(missing) + '가 부족해 긍정·부정으로 분류하지 않습니다.',
                'details': []}
    up_supply = all(v > 0 for v in cumulative)
    down_supply = all(v < 0 for v in cumulative)
    improving = all(v > 0 for v in changes)
    weakening = all(v < 0 for v in changes)
    items = [indices[n] for n in ['KOSPI', 'KOSDAQ']]
    price_good = all(i['flow'] == '개선' and i['clv'] >= 80 and i['late_return'] > 0
                     and i['auction_return'] >= 0 for i in items)
    price_bad = all(i['flow'] == '약화' and i['clv'] <= 20 and i['late_return'] < 0 for i in items)
    if up_supply and improving and price_good:
        label, level = '긍정적 근거 우세', 'success'
        reason = '하루 누적 순매수, 장 후반 수급 개선, 두 지수의 개선·상단 마감이 함께 확인됩니다.'
    elif weakening and price_bad:
        label, level = '부정적 근거 우세', 'error'
        reason = '장 후반 프로그램·비차익 수급이 악화되고, 두 지수도 약화·하단 마감해 마감 환경이 부정적입니다.'
    else:
        label, level = '혼조 · 신중 검토', 'warning'
        if down_supply and improving:
            reason = '마감 전 수급은 개선됐지만 하루 누적 순매도가 남아 있습니다. 매도 압력 감소이지 누적 순매수 전환은 아닙니다.'
        elif up_supply and not improving:
            reason = '하루 누적 순매수는 있지만 장 후반 수급 개선이 함께 확인되지는 않습니다.'
        else:
            reason = '수급·지수 흐름·마감 위치가 일관되게 같은 방향을 가리키지 않아 우호적인 환경으로 단정하지 않습니다.'
    daily = '둘 다 순매수' if up_supply else '둘 다 순매도' if down_supply else '혼조·중립'
    late = '둘 다 개선' if improving else '둘 다 악화' if weakening else '혼조·중립'
    details = [f'하루 누적 수급: 프로그램·비차익 {daily}.', f'마지막 60분 수급: 프로그램·비차익 {late}.']
    for name in ['KOSPI', 'KOSDAQ']:
        i = indices[name]
        position = '상단' if i['clv'] >= 80 else '하단' if i['clv'] <= 20 else '중단'
        details.append(f"{name}: 장 후반 {i['flow']} · {position} 마감({i['clv']:.1f}%).")
    if any(i['auction_return'] < 0 for i in items):
        details.append('주의: 종가 동시호가에서 하락한 지수가 있습니다.')
    return {'label': label, 'level': level, 'reason': reason, 'details': details}


def render_market_strength2(db_path, selected_date, selected_session):
    import sqlite3
    import streamlit as st
    import altair as alt
    def plot(frame, value_columns):
        values = frame.melt('시간', value_vars=value_columns, var_name='항목', value_name='값')
        values['시각'] = pd.to_datetime('2000-01-01 ' + values['시간'])
        chart = alt.Chart(values).mark_line(point=True).encode(
            x=alt.X('시각:T', title='시간', axis=alt.Axis(format='%H:%M')),
            y=alt.Y('값:Q', scale=alt.Scale(zero=False)),
            color=alt.Color('항목:N'), tooltip=['시간:N', '항목:N', alt.Tooltip('값:Q', format=',.2f')],
        ).properties(height=230)
        st.altair_chart(chart, width='stretch')
    st.subheader('시장강도분석2 · 정규장 마감 환경')
    st.caption('16시 확인용 · 15:30까지의 정규장 데이터만 사용합니다. NXT·해외 변수·다음 날 상승 예측은 포함하지 않습니다.')
    if '16:00' not in str(selected_session):
        st.info('정규장(16:00)을 선택하면 해당 날짜의 마감 환경이 표시됩니다. 장중 선택에는 미래 마감 결과를 보여주지 않습니다.')
        return
    try:
        with closing(sqlite3.connect(db_path)) as conn:
            row = conn.execute('SELECT payload FROM web_market_strength2 WHERE trade_date=?', (str(selected_date),)).fetchone()
    except sqlite3.OperationalError:
        row = None
    if not row:
        st.info('해당 날짜의 마감 요약이 없습니다. 새 웹 데이터 생성 시 저장 분봉으로 계산됩니다. 다른 날짜 결과로 대체하지 않습니다.')
        return
    payload = json.loads(row[0])
    interpretation = interpret_market_environment(payload)
    getattr(st, interpretation['level'])(f"**정규장 마감 해석: {interpretation['label']}**\n\n{interpretation['reason']}")
    for detail in interpretation['details']:
        st.write('• ' + detail)
    st.caption('종가베팅 참고용 정성적 해석입니다. 매수 추천·다음 날 상승 확률이 아니며, 판정 기준은 수익률로 검증된 매매 규칙이 아닙니다. 프로그램·비차익은 서로 겹치는 수급이므로 독립된 두 신호로 중복 가점하지 않습니다.')
    supply = payload['supply']
    st.markdown('#### 하루 누적 수급 / 장 후반 수급')
    points = pd.DataFrame(supply['points'])
    if not points.empty:
        last = points.iloc[-1]
        for column in ['프로그램 누적', '비차익 누적']:
            value = number(last[column])
            state = '순매수' if value is not None and value > 0 else '순매도' if value is not None and value < 0 else '중립' if value == 0 else '데이터 부족'
            st.write(f'마감 {column}: {state}')
        plot(points, ['프로그램 누적', '비차익 누적'])
        with st.expander('시간별 누적 수급 수치'):
            st.dataframe(points, hide_index=True, width='stretch')
    st.caption('프로그램 수급은 코스피 기준이며 코스닥 전체 수급을 뜻하지 않습니다. 확보된 관측 시점의 누적 금액으로, 1분 연속 수급이 아닙니다. 금액은 제공기관 원자료 단위이며 거래대금 대비 정규화 점수는 산출하지 않습니다.')
    for label, key in [('14:30→15:30 프로그램 순매수 증가분', 'program_delta'), ('14:30→15:30 비차익 순매수 증가분', 'non_arbitrage_delta')]:
        value = supply[key]
        st.write(f'{label}: {value:,.0f}' if value is not None else f'{label}: 데이터 부족')
    st.write(f"베이시스 변화: {supply['basis_delta']:+.2f}p" if supply['basis_delta'] is not None else '베이시스 변화: 데이터 부족')
    st.caption('베이시스는 변화만 참고합니다. 절대 양수·음수에 강세/약세 점수를 부여하지 않습니다.')
    st.markdown('#### 지수별 장 후반 흐름과 마감 위치')
    rows = []
    for name, data in payload['indices'].items():
        clv = data['clv']
        location = '상단' if clv is not None and clv >= 80 else '하단' if clv is not None and clv <= 20 else '중단' if clv is not None else '데이터 부족'
        rows.append({'지수': name, '장 후반': data['flow'], '마감 위치': location,
                     '마감 위치(%)': round(clv, 1) if clv is not None else None,
                     '14:30→15:30(%)': data['late_return'], '동시호가 변화(%)': data['auction_return'],
                     '연속매매 분봉': f"{data['minutes']}/50"})
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')
        name = st.selectbox('장 후반 지수 흐름', list(payload['indices']), key='market_strength2_index')
        data = payload['indices'][name]
        chart = pd.DataFrame(data['points'])
        if not chart.empty:
            chart = chart[(chart['시간'] <= '15:20') | (chart['시간'] == '15:30')]
            plot(chart, ['지수'])
        st.caption(f"동시호가 비교 기준: {data.get('auction_baseline', '미확보')} → 15:30. 동시호가 구간은 연속매매 분봉으로 채우거나 보간하지 않습니다.")
    else:
        st.warning('지수 분봉이 없어 가격 흐름과 마감 위치를 판단할 수 없습니다.')
    with st.expander('계산 방식 · 간단 설명'):
        st.write('장 후반: 14:30~15:19의 1분봉 전체에서 처음 15분과 마지막 15분 평균·저점을 비교합니다. 평균과 저점이 함께 높아지면 개선, 함께 낮아지면 약화, 나머지는 혼조입니다. 분봉 누락 또는 15:30 값 누락 시 분석 불충분입니다.')
        st.write('마감 위치 = (종가−당일 저가)/(당일 고가−당일 저가)×100. 80% 이상 상단, 20% 이하 하단은 설명용 기준이며 검증된 매수 기준이 아닙니다. 당일 전체 분봉이 부족하거나 고가=저가이면 계산하지 않습니다.')
        st.write('상단 마감은 상승분 유지의 참고 신호이지 다음 날 상승 보장이 아닙니다. 전일 대비 하락한 날도 상단 마감할 수 있습니다. 코스피·코스닥 지수 비교는 상승 종목 비율과 다르며 시장 전체 상승 확산을 확정하지 않습니다.')
        st.write('종합 해석: 프로그램·비차익 누적 순매수와 마지막 60분 개선, 코스피·코스닥 개선·상단 마감 및 동시호가 유지가 모두 일치하면 긍정적 근거 우세입니다. 장 후반 수급이 모두 악화하고 두 지수가 약화·하단 마감하면 부정적 근거 우세입니다. 나머지는 혼조·신중 검토이며 필수 데이터가 없으면 판단 보류입니다. 베이시스와 코스피200은 종합 분류의 필수 조건이 아닌 보조자료입니다.')
        st.write('임의의 종합점수나 매수 합격·탈락 판정은 넣지 않습니다. 누적 수급, 장 후반 흐름, 마감 위치를 함께 보고 사용자가 판단합니다.')
