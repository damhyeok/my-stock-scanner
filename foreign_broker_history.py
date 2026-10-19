"""Quantity-based broker histories; missing trading days always break evidence."""
import pandas as pd


def trading_dates(data, cutoff):
    calendar = data.get('calendar', pd.DataFrame())
    dates = calendar['date'] if not calendar.empty else data['universe']['trade_date']
    return sorted({str(d) for d in dates if str(d) <= str(cutoff)}, reverse=True)


def daily_net_observations(data):
    daily = data['daily'].copy()
    codes = set(data['members']['broker_code'])
    if daily.empty or not codes:
        return daily, pd.DataFrame(columns=['trade_date','ticker','net_qty','is_final'])
    daily = daily[daily.broker_code.isin(codes)].drop_duplicates(['trade_date','ticker','broker_code'],keep='last')
    totals = daily.groupby(['trade_date','ticker']).agg(
        net_qty=('net_qty','sum'), count=('broker_code','nunique'),
        is_final=('is_final','min')).reset_index()
    totals.loc[totals['count'] != len(codes), 'net_qty'] = float('nan')
    return daily, totals


def build_broker_streaks(data, cutoff, *, combined=False):
    dates = trading_dates(data,cutoff)
    daily, totals = daily_net_observations(data)
    columns = ['ticker','name','broker_code','broker_name','streak_days','streak_start',
               'latest_net_qty','streak_net_qty','reference_net_amount','provisional','lower_bound']
    if not dates or daily.empty:
        return pd.DataFrame(columns=columns)
    frame = totals if combined else daily
    groups = ['ticker'] if combined else ['ticker','broker_code']
    catalog = data['universe'][data['universe'].trade_date.astype(str)<=str(cutoff)].sort_values('trade_date').drop_duplicates('ticker',keep='last')
    names = dict(zip(catalog.ticker,catalog.name))
    members = dict(zip(data['members'].broker_code,data['members'].broker_name))
    closes = data['closes']
    prices = {(str(r.trade_date),r.ticker):r.close for r in closes.itertuples()}
    rows = []
    for key, group in frame.groupby(groups,sort=False):
        if not isinstance(key,tuple):
            key=(key,)
        ticker=key[0]
        records={str(r.trade_date):r for r in group.itertuples()}
        streak=[]
        unknown=False
        for date in dates:
            record=records.get(date)
            if record is None or pd.isna(record.net_qty):
                unknown=True
                break
            if record.net_qty <= 0:
                break
            streak.append((date,record))
        else:
            unknown=True  # Earlier history lies outside the retained calendar.
        if len(streak)<2:
            continue
        money=[r.net_qty*prices.get((d,ticker),float('nan')) for d,r in streak]
        rows.append(dict(ticker=ticker,name=names.get(ticker,ticker),
                         broker_code='' if combined else key[1],
                         broker_name='외국계 합산' if combined else members.get(key[1],key[1]),
                         streak_days=len(streak),streak_start=streak[-1][0],
                         latest_net_qty=streak[0][1].net_qty,
                         streak_net_qty=sum(r.net_qty for _,r in streak),
                         reference_net_amount=sum(money) if all(pd.notna(v) for v in money) else float('nan'),
                         provisional=any(r.is_final==0 for _,r in streak),lower_bound=unknown))
    return pd.DataFrame(rows,columns=columns).sort_values(
        ['streak_days','reference_net_amount','ticker','broker_code'],ascending=[False,False,True,True],na_position='last')


def build_stock_net_history(data, ticker, cutoff, days=10):
    dates=trading_dates(data,cutoff)[:days]
    daily, totals=daily_net_observations(data)
    frame=pd.DataFrame(index=pd.Index(data['members'].broker_code,name='broker_code'),columns=dates,dtype=float)
    if not daily.empty:
        selected=daily[(daily.ticker==ticker)&daily.trade_date.astype(str).isin(dates)]
        values=selected.pivot(index='broker_code',columns='trade_date',values='net_qty')
        frame=values.reindex(index=frame.index,columns=dates)
    labels=dict(zip(data['members'].broker_code,data['members'].broker_name))
    frame.index=[labels[c] for c in frame.index]
    total=totals[totals.ticker==ticker].set_index('trade_date')['net_qty'] if not totals.empty else pd.Series(dtype=float)
    frame.loc['외국계 합산']=[total.get(d,float('nan')) for d in dates]
    return frame.loc[['외국계 합산']+[n for n in frame.index if n!='외국계 합산']]
