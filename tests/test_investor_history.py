import pandas as pd
from investor_history_ui import build_investor_history


def test_eight_trading_dates_and_separate_investor_rankings():
    dates = ['20261005','20261006','20261007','20261008','20261012','20261013','20261014','20261015','20261016','20261019']
    raw = pd.DataFrame([dict(date=d,session=s,category=c,ticker=t,name=n,
                             foreign_net=f,inst_net=i,fluctuation_rate=2.5)
        for d in dates for s in ['장중(14:00)','정규장(16:00)']
        for c in ['FOREIGN_TOP_30','INST_TOP_30']
        for t,n,f,i in [('000001','A',1000,500),('000002','B',500,2000)]])
    foreign = build_investor_history(raw,'20261019','정규장(16:00)','foreign',dates)
    inst = build_investor_history(raw,'20261019','정규장(16:00)','institution',dates)
    assert len(foreign.columns)==9
    assert foreign.columns.tolist()[1:]==list(reversed(dates[-8:]))
    assert foreign.iloc[0]['20261019']=='A\n+10.0억 · +2.5%'
    assert inst.iloc[0]['20261019']=='B\n+20.0억 · +2.5%'
    missing = build_investor_history(raw[raw.date!='20261016'],'20261019','정규장(16:00)','foreign',dates)
    assert missing.iloc[0]['20261016']=='미수집 / 순위 없음'
    before = build_investor_history(raw,'20261015','정규장(16:00)','foreign',dates)
    assert '20261019' not in before.columns


def test_history_is_session_specific_and_deduplicated():
    raw=pd.DataFrame([dict(date='20261019',session='장중(14:00)',category='FOREIGN_TOP_30',
                          ticker='000001',name='<A>',foreign_net=500,inst_net=0)]*2)
    table=build_investor_history(raw,'20261019','장중(14:00)','foreign')
    assert table.iloc[0]['20261019'].startswith('<A>')
    assert table.iloc[1]['20261019']=='미수집 / 순위 없음'
    assert build_investor_history(raw,'20261019','정규장(16:00)','foreign').iloc[0]['20261019']=='미수집 / 순위 없음'
