from datetime import datetime,timedelta
import json
import sqlite3
from types import SimpleNamespace
import pandas as pd
import pytest
from market_calendar import KST
from etf_strength import (init_strength_tables,parse_minute_rows,summarize_etf,
                          build_etf_strength_display,collect_etf_strength)
from etf_strength_ui import build_overview


def snapshot(session='정규장(16:00)'):
    return dict(trade_date='20261019',session=session,ticker='091160',sector='반도체',
                name='ETF',price=110,change_rate=5.,trading_value=1e8,kospi_rate=2.,
                collected_at_kst='2026-10-19 16:01:00')


def minute_frame():
    times=pd.date_range('2026-10-19 14:20','2026-10-19 15:19',freq='min')
    return pd.DataFrame([dict(trade_date='20261019',ticker='091160',bar_time=t.strftime('%H:%M'),
                             open=100+i*.05,high=100.1+i*.05,low=99.9+i*.05,close=100.05+i*.05,
                             volume=100,collected_at_kst='2026-10-19 16:01:00') for i,t in enumerate(times)])


def test_full_minute_path_and_auction_is_separate():
    raw=minute_frame()
    result=summarize_etf(snapshot(),raw,100)
    assert result['hourly']['status']=='COMPLETE'
    assert result['hourly']['observed_minutes']==60
    assert result['hourly']['window_start'].startswith('2026-10-19T14:20')
    assert result['hourly_label']=='상승 흐름 유지'
    assert result['since_open']==pytest.approx(10)
    assert result['market_excess']==3
    extra=pd.DataFrame([dict(raw.iloc[-1],bar_time='15:30',close=999,high=1000)])
    assert summarize_etf(snapshot(),pd.concat([raw,extra]),100)['hourly']==result['hourly']
    assert 'relative_return' not in result['hourly']


def test_incomplete_and_early_windows_not_false_full_hour():
    raw=minute_frame().iloc[10:]
    result=summarize_etf(snapshot(),raw)
    assert result['hourly']['status']=='INSUFFICIENT'
    assert result['since_open'] is None
    result=summarize_etf(dict(snapshot('장중(09:30)'),collected_at_kst='2026-10-19 09:30:15'),minute_frame())
    assert result['hourly']['window_end'].startswith('2026-10-19T09:29')


def test_wrong_day_and_future_minutes_not_accepted():
    row=dict(stck_bsop_date='20261019',stck_cntg_hour='151900',stck_oprc='100',
             stck_hgpr='102',stck_lwpr='99',stck_prpr='101',cntg_vol='100')
    end=datetime(2026,10,19,15,19,tzinfo=KST)
    assert list(parse_minute_rows([row], '20261019',end))==['15:19']
    assert not parse_minute_rows([dict(row,stck_bsop_date='20261016')],'20261019',end)
    assert not parse_minute_rows([dict(row,stck_cntg_hour='152000')],'20261019',end)
    assert not parse_minute_rows([dict(row,stck_prpr='nan')],'20261019',end)


def test_materialized_summary_survives_raw_expiry_and_snapshot_selection():
    with sqlite3.connect(':memory:') as conn:
        init_strength_tables(conn)
        minute_frame().to_sql('etf_strength_minutes',conn,if_exists='append',index=False)
        pd.DataFrame([snapshot()]).to_sql('etf_sector_snapshots',conn,index=False)
        conn.execute('INSERT INTO etf_strength_opens VALUES (?,?,?)',('20261019','091160',100))
        build_etf_strength_display(conn)
        payload=conn.execute('SELECT payload FROM etf_strength_display').fetchone()[0]
        assert json.loads(payload)['hourly']['status']=='COMPLETE'
        conn.execute('DELETE FROM etf_strength_minutes')
        build_etf_strength_display(conn)
        assert conn.execute('SELECT payload FROM etf_strength_display').fetchone()[0]==payload
        derived=pd.read_sql_query('SELECT * FROM etf_strength_display',conn)
        snaps=pd.read_sql_query('SELECT * FROM etf_sector_snapshots',conn)
        assert build_overview(snaps,derived,'20261019','정규장(16:00)').iloc[0].sector=='반도체'
        past=build_overview(snaps,derived,'20261019','장중(14:00)')
        assert past.change_rate.isna().all()
        assert past.hourly_return.isna().all()


def test_bounded_collection_makes_no_requests_after_deadline(tmp_path):
    path=tmp_path/'source.db'
    collector=SimpleNamespace(crawler=SimpleNamespace(db_path=str(path)),universe=[('반도체','091160','ETF','')])
    def forbidden(*args,**kwargs):
        raise AssertionError('must not query after deadline')
    collector.request=forbidden
    # A non-current day must also never query the current-day-only endpoint.
    collect_etf_strength(collector,'20000101','정규장(16:00)',{},0)


def test_collector_fetches_all_minutes_and_skips_already_stored(tmp_path,monkeypatch):
    import time
    import etf_strength as module
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):
            return cls(2026,10,19,16,5,tzinfo=KST)
    monkeypatch.setattr(module,'datetime',Clock)
    path=tmp_path/'source.db'
    with sqlite3.connect(path) as conn:
        pd.DataFrame([snapshot()]).to_sql('etf_sector_snapshots',conn,index=False)
    collector=SimpleNamespace(crawler=SimpleNamespace(db_path=str(path)),universe=[('반도체','091160','ETF','')])
    calls=[]
    def request(path,tr,params):
        calls.append(params)
        limit=params['FID_INPUT_HOUR_1']
        rows=[dict(stck_bsop_date='20261019',stck_cntg_hour=r.bar_time.replace(':','')+'00',
                   stck_oprc=r.open,stck_hgpr=r.high,stck_lwpr=r.low,stck_prpr=r.close,cntg_vol=r.volume)
              for r in minute_frame().itertuples() if r.bar_time.replace(':','')+'00'<=limit]
        return {'output2':list(reversed(rows))[:30]}
    collector.request=request
    collect_etf_strength(collector,'20261019','정규장(16:00)',{'091160':100},time.monotonic()+100)
    assert len(calls)==2
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM etf_strength_minutes').fetchone()[0]==60
        assert json.loads(conn.execute('SELECT payload FROM etf_strength_display').fetchone()[0])['hourly']['status']=='COMPLETE'
    collect_etf_strength(collector,'20261019','정규장(16:00)',{'091160':100},time.monotonic()+100)
    assert len(calls)==2
    collect_etf_strength(collector,'20261019','정규장(16:00)',{'091160':100},0)
    assert len(calls)==2


def test_ui_with_missing_minutes_and_existing_quotes(tmp_path):
    from streamlit.testing.v1 import AppTest
    path=tmp_path/'view.db'
    with sqlite3.connect(path) as conn:
        pd.DataFrame([snapshot()]).to_sql('etf_sector_snapshots',conn,index=False)
    app=AppTest.from_string('from etf_strength_ui import render_today_sector_flow\n'
                           +f"render_today_sector_flow({str(path)!r},'20261019','정규장(16:00)')").run(timeout=30)
    assert not app.exception
    assert any('18' in str(c.value) for c in app.caption)
    assert any('분봉 자료 부족' in str(m.value) for m in app.markdown)


def test_ui_complete_summary_and_web_export_drops_raw(tmp_path):
    from streamlit.testing.v1 import AppTest
    from web_database import build_web_database
    source,target=tmp_path/'source.db',tmp_path/'web.db'
    with sqlite3.connect(source) as conn:
        init_strength_tables(conn)
        pd.DataFrame([snapshot()]).to_sql('etf_sector_snapshots',conn,index=False)
        minute_frame().to_sql('etf_strength_minutes',conn,if_exists='append',index=False)
        conn.execute('INSERT INTO etf_strength_opens VALUES (?,?,?)',('20261019','091160',100))
        conn.commit()
    build_web_database(source,target)
    with sqlite3.connect(target) as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='etf_strength_minutes'").fetchone()
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='etf_strength_opens'").fetchone()
        assert json.loads(conn.execute('SELECT payload FROM etf_strength_display').fetchone()[0])['hourly']['status']=='COMPLETE'
    app=AppTest.from_string('from etf_strength_ui import render_today_sector_flow\n'
                           +f"render_today_sector_flow({str(target)!r},'20261019','정규장(16:00)')").run(timeout=30)
    assert not app.exception
    assert any('상승 흐름 유지' in str(m.value) for m in app.markdown)
