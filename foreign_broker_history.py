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


def build_stock_trade_details(data,ticker,cutoff,days=10):
    """Period totals are observed subtotals; gaps explicitly taint running totals."""
    universe=data['universe']
    observed=universe[universe.trade_date.astype(str)<=str(cutoff)]
    dates=trading_dates(data,cutoff)
    if days is not None:
        dates=dates[:days]
    if observed.empty:
        return pd.DataFrame(),pd.DataFrame(),[]
    first=str(observed.trade_date.min())
    dates=sorted(d for d in dates if d>=first)
    if not dates:
        return pd.DataFrame(),pd.DataFrame(),[]
    codes=data['members'][['broker_code','broker_name']].drop_duplicates('broker_code')
    matrix=pd.MultiIndex.from_product([codes.broker_code,dates],names=['broker_code','trade_date']).to_frame(index=False)
    daily=data['daily']
    selected=daily[(daily.ticker==ticker)&daily.trade_date.astype(str).isin(dates)].drop_duplicates(['broker_code','trade_date'],keep='last') if not daily.empty else pd.DataFrame(columns=['broker_code','trade_date','buy_qty','sell_qty','net_qty','is_final'])
    matrix=matrix.merge(selected[['broker_code','trade_date','buy_qty','sell_qty','net_qty','is_final']],on=['broker_code','trade_date'],how='left')
    matrix=matrix.merge(codes,on='broker_code',how='left')
    # A day-level aggregate is valid only with every foreign member observed.
    totals=[]
    for date,frame in matrix.groupby('trade_date'):
        complete=frame[['buy_qty','sell_qty','net_qty']].notna().all().all()
        totals.append(dict(broker_code='__ALL__',broker_name='외국계 합산',trade_date=date,
                           **{k:frame[k].sum() if complete else float('nan') for k in ['buy_qty','sell_qty','net_qty']},
                           is_final=frame.is_final.min() if complete else float('nan')))
    matrix=pd.concat([matrix,pd.DataFrame(totals)],ignore_index=True)
    summaries=[]
    parts=[]
    for code,frame in matrix.groupby('broker_code',sort=False):
        frame=frame.sort_values('trade_date').copy()
        known=frame[['buy_qty','sell_qty','net_qty']].notna().all(axis=1)
        frame['cumulative_net_qty']=frame.net_qty.cumsum(skipna=False)
        # This is labelled "observed subtotal", never an inferred zero observation.
        frame['cumulative_observed_net_qty']=frame.net_qty.fillna(0).cumsum()
        frame.loc[known.cumsum().eq(0),'cumulative_observed_net_qty']=float('nan')
        frame['cumulative_complete']=known.cummin()
        frame['provisional_to_date']=frame.is_final.eq(0).cummax()
        frame['status']=['미수집' if not ok else '잠정' if final==0 else '확정' for ok,final in zip(known,frame.is_final)]
        summaries.append(dict(broker_code=code,broker_name=frame.broker_name.iloc[0],
                              **{k:frame[k].sum(min_count=1) for k in ['buy_qty','sell_qty','net_qty']},
                              observed_days=int(known.sum()),expected_days=len(dates),complete=bool(known.all()),
                              provisional=bool(frame.is_final.eq(0).any())))
        parts.append(frame)
    summary=pd.DataFrame(summaries).sort_values(['complete','net_qty','broker_code'],ascending=[False,False,True],na_position='last')
    return summary,pd.concat(parts,ignore_index=True),dates
