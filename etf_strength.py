"""Bounded ETF minute collection and versioned, price-only sector evidence."""
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timedelta
import json
import math
import sqlite3
import time

import pandas as pd
from market_calendar import KST
from market_betting_engine.features import NormalizedBar
from market_betting_engine.sector_hourly import derive_hourly_path


def init_strength_tables(conn):
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS etf_strength_minutes (
            trade_date TEXT,ticker TEXT,bar_time TEXT,open REAL,high REAL,low REAL,
            close REAL,volume REAL,collected_at_kst TEXT,
            PRIMARY KEY(trade_date,ticker,bar_time));
        CREATE TABLE IF NOT EXISTS etf_strength_opens (
            trade_date TEXT,ticker TEXT,open REAL,PRIMARY KEY(trade_date,ticker));
        CREATE TABLE IF NOT EXISTS etf_strength_display (
            trade_date TEXT,session TEXT,ticker TEXT,payload TEXT,
            PRIMARY KEY(trade_date,session,ticker));
    ''')


def cutoff_for_session(day, session):
    import re
    match=re.search(r'(\d{2}):(\d{2})',str(session))
    if not match:
        raise ValueError('ETF flow requires an explicit session time')
    hour,minute=map(int,match.groups())
    return datetime.strptime(day,'%Y%m%d').replace(hour=hour,minute=minute,tzinfo=KST)


def parse_minute_rows(rows,day,end):
    result={}
    for row in rows:
        if str(row.get('stck_bsop_date',''))!=day:
            continue
        stamp=str(row.get('stck_cntg_hour','')).zfill(6)
        try:
            dt=datetime.strptime(day+stamp,'%Y%m%d%H%M%S').replace(tzinfo=KST)
            vals=[float(row[k]) for k in ['stck_oprc','stck_hgpr','stck_lwpr','stck_prpr','cntg_vol']]
        except (ValueError,TypeError,KeyError):
            continue
        if (dt>end or dt.strftime('%H:%M')<'09:00' or not all(math.isfinite(v) for v in vals)
                or min(vals[:4])<=0 or vals[4]<0 or not vals[2]<=min(vals[0],vals[3])<=max(vals[0],vals[3])<=vals[1]):
            continue
        result[dt.strftime('%H:%M')]=vals
    return result


def evaluation_end(snapshot):
    as_of=cutoff_for_session(str(snapshot['trade_date']),snapshot['session'])
    collected=datetime.fromisoformat(str(snapshot['collected_at_kst'])).replace(tzinfo=KST)
    return min(as_of,collected.replace(second=0,microsecond=0)-timedelta(minutes=1),
               as_of.replace(hour=15,minute=19))


def summarize_etf(snapshot, minutes, open_price=None):
    snapshot={k:(None if isinstance(v,float) and not math.isfinite(v) else v) for k,v in snapshot.items()}
    day=str(snapshot['trade_date'])
    # Only completed continuous-market minutes: exclude current unfinished bar.
    as_of=evaluation_end(snapshot)
    bars=[]
    for row in minutes.itertuples():
        dt=datetime.strptime(day+' '+str(row.bar_time),'%Y%m%d %H:%M').replace(tzinfo=KST)
        if dt<=as_of:
            bars.append(NormalizedBar(dt,row.open,row.high,row.low,row.close,row.volume))
    # The self-reference is used only to reuse price-path/VWAP quality checks.
    # Relative strength is NOT derived from this path; use the quote benchmark.
    path=asdict(derive_hourly_path(bars,bars,as_of=as_of))
    for field in ['relative_return','relative_retention','outperforming']:
        path.pop(field,None)
    rate=snapshot.get('change_rate')
    benchmark=snapshot.get('kospi_rate')
    since_open=(snapshot['price']/open_price-1)*100 if open_price and open_price>0 else None
    if path['status']!='COMPLETE':
        label='60분 미충족·잠정' if path['status']=='PROVISIONAL' else '분봉 자료 부족'
    elif path['return_ratio']>0 and path['structure_confirming']:
        label='상승 흐름 유지'
    elif path['return_ratio']<0 and path['price_slope_per_minute']<0:
        label='하락 흐름'
    else:
        label='혼조'
    return dict(version='ETF_PRICE_PATH_V1',**{k:snapshot.get(k) for k in
                ['trade_date','session','ticker','sector','name','price','change_rate','trading_value','collected_at_kst']},
                open_price=open_price,since_open=since_open,
                market_excess=rate-benchmark if rate is not None and benchmark is not None else None,
                hourly=path,hourly_label=label)


def build_etf_strength_display(conn, day=None):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='etf_strength_minutes'").fetchone():
        return
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='etf_sector_snapshots'").fetchone():
        return
    # Preserve older materialized rows when source minutes have expired.
    dates=[day] if day else [r[0] for r in conn.execute('SELECT DISTINCT trade_date FROM etf_strength_minutes ORDER BY trade_date DESC LIMIT 5')]
    for date in dates:
        snaps=pd.read_sql_query('SELECT * FROM etf_sector_snapshots WHERE trade_date=?',conn,params=(date,))
        raw=pd.read_sql_query('SELECT * FROM etf_strength_minutes WHERE trade_date=? ORDER BY bar_time',conn,params=(date,))
        opens=dict(conn.execute('SELECT ticker,open FROM etf_strength_opens WHERE trade_date=?',(date,)))
        for snap in snaps.to_dict('records'):
            result=summarize_etf(snap,raw[raw.ticker==snap['ticker']],opens.get(snap['ticker']))
            conn.execute('INSERT OR REPLACE INTO etf_strength_display VALUES (?,?,?,?)',
                         (date,snap['session'],snap['ticker'],json.dumps(result,ensure_ascii=False,allow_nan=False)))


def collect_etf_strength(collector, day, session, opens, deadline):
    """Round-robin up to three 30-bar pages per ETF, within shared job budget."""
    now=datetime.now(KST)
    if day!=now.strftime('%Y%m%d'):
        return
    end=min(cutoff_for_session(day,session),now.replace(second=0,microsecond=0)-timedelta(minutes=1),
            now.replace(hour=15,minute=19,second=0,microsecond=0))
    start=max(end-timedelta(minutes=59),end.replace(hour=9,minute=0))
    if start>end:
        return
    db=collector.crawler.db_path
    with closing(sqlite3.connect(db)) as conn:
        init_strength_tables(conn)
        conn.executemany('INSERT OR REPLACE INTO etf_strength_opens VALUES (?,?,?)',
                         [(day,t,float(p)) for t,p in opens.items() if p and p>0])
        conn.commit()
        # Give the least recently covered ETFs priority, avoiding starvation.
        latest=dict(conn.execute('SELECT ticker,MAX(bar_time) FROM etf_strength_minutes WHERE trade_date=? GROUP BY ticker',(day,)))
        quote_times=dict(conn.execute('SELECT ticker,collected_at_kst FROM etf_sector_snapshots WHERE trade_date=? AND session=?',(day,session)))
        windows={}
        for _,ticker,_,_ in collector.universe:
            if ticker not in quote_times:
                continue
            finish=min(end,evaluation_end(dict(trade_date=day,session=session,collected_at_kst=quote_times[ticker])))
            windows[ticker]=(max(finish-timedelta(minutes=59),finish.replace(hour=9,minute=0)),finish)
        cursors={t:windows[t][1] for _,t,_,_ in sorted(collector.universe,key=lambda item:latest.get(item[1],'')) if t in windows}
        if windows:
            start=min(w[0] for w in windows.values())
        existing={}
        for ticker,stamp in conn.execute('SELECT ticker,bar_time FROM etf_strength_minutes WHERE trade_date=? AND bar_time>=? AND bar_time<=?',
                                         (day,start.strftime('%H:%M'),end.strftime('%H:%M'))):
            existing.setdefault(ticker,set()).add(stamp)
        for ticker in cursors:
            expected=pd.date_range(*windows[ticker],freq='min')
            missing=[t.to_pydatetime() for t in expected if t.strftime('%H:%M') not in existing.get(ticker,set())]
            cursors[ticker]=max(missing) if missing else windows[ticker][0]-timedelta(minutes=1)
        for _ in range(3):
            for ticker,cursor in list(cursors.items()):
                start,end=windows[ticker]
                if time.monotonic()+9>=deadline:
                    break
                if cursor<start:
                    continue
                try:
                    payload=collector.request('/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice','FHKST03010200',
                        {'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':ticker,'FID_INPUT_HOUR_1':cursor.strftime('%H%M%S'),
                         'FID_PW_DATA_INCU_YN':'Y','FID_ETC_CLS_CODE':''})
                    parsed=parse_minute_rows(payload.get('output2') or [],day,cursor)
                    if not parsed:
                        cursors[ticker]=start-timedelta(minutes=1)
                        continue
                    stamp=datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')
                    conn.executemany('INSERT OR REPLACE INTO etf_strength_minutes VALUES (?,?,?,?,?,?,?,?,?)',
                        [(day,ticker,t,*vals,stamp) for t,vals in parsed.items() if start.strftime('%H:%M')<=t<=end.strftime('%H:%M')])
                    cursors[ticker]=datetime.strptime(day+' '+min(parsed),'%Y%m%d %H:%M').replace(tzinfo=KST)-timedelta(minutes=1)
                    conn.commit()
                except Exception:
                    cursors[ticker]=start-timedelta(minutes=1)
        build_etf_strength_display(conn,day)
        conn.commit()
