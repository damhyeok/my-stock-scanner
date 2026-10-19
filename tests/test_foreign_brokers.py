from contextlib import closing
from datetime import datetime
import sqlite3
from types import SimpleNamespace
import pandas as pd
import pytest

from foreign_broker_flow import (parse_members, parse_daily, init_tables, collect_universe,
                                 ForeignBrokerCollector, publish_broker_snapshot)
from foreign_broker_ui import aggregate_period, rank_reference_amount
from foreign_broker_history import build_broker_streaks, build_stock_net_history


def test_reference_ranking_uses_money_not_shares_and_excludes_missing():
    frame = pd.DataFrame([
        dict(ticker='A',net_qty=100000,reference_net_amount=100000000,complete=True),
        dict(ticker='B',net_qty=5000,reference_net_amount=500000000,complete=True),
        dict(ticker='C',net_qty=900000,reference_net_amount=float('nan'),complete=True),
        dict(ticker='D',net_qty=900000,reference_net_amount=900000000,complete=False),
        dict(ticker='E',net_qty=100,reference_net_amount=-300000000,complete=True),
        dict(ticker='F',net_qty=-1000,reference_net_amount=-100000000,complete=True),
        dict(ticker='G',net_qty=-100,reference_net_amount=200000000,complete=True),
        dict(ticker='H',net_qty=0,reference_net_amount=0,complete=True),
    ])
    assert rank_reference_amount(frame).ticker.tolist() == ['B','G','A']
    assert rank_reference_amount(frame,buying=False).ticker.tolist() == ['E','F']
    assert rank_reference_amount(frame,limit=1).ticker.tolist() == ['B']


def test_reference_ranking_empty_when_closes_missing():
    with closing(sqlite3.connect(':memory:')) as conn:
        fixture(conn)
        conn.execute('DELETE FROM foreign_broker_closes')
        details, totals, _ = aggregate_period(data(conn),['20261015','20261016'])
        assert rank_reference_amount(totals).empty
        assert rank_reference_amount(details,buying=False).empty
from market_calendar import KST


def fixture(conn):
    init_tables(conn)
    conn.executemany('INSERT INTO foreign_broker_members VALUES (?,?,?)',
                     [('00033','JP모간','2026-10-19 00:00:00'),('00045','골드만','2026-10-19 00:00:00')])
    conn.executemany('INSERT INTO foreign_broker_universe VALUES (?,?,?)',
                     [(d,t,n) for d in ['20261015','20261016'] for t,n in [('005930','삼성전자'),('000660','SK하이닉스')]])
    conn.executemany('INSERT INTO foreign_broker_daily VALUES (?,?,?,?,?,?,?,?)',
                     [(d,t,b,buy,sell,buy-sell,1,'2026-10-19 00:00:00')
                      for d in ['20261015','20261016'] for t,buy,sell in [('005930',100,20),('000660',10,50)]
                      for b in ['00033','00045']])
    conn.executemany('INSERT INTO foreign_broker_closes VALUES (?,?,?,?)',
                     [(d,t,p,'2026-10-19 00:00:00') for d,p in [('20261015',100),('20261016',200)] for t in ['005930','000660']])
    conn.commit()


def data(conn):
    return {name:pd.read_sql_query('SELECT * FROM foreign_broker_'+name,conn)
            for name in ['members','universe','daily','closes','runs']}


def test_master_excludes_aggregate_and_deduplicates():
    text = '99999외국계합    1\r\n00033제이피모간 1\r\n00003한국증권 0\r\n00033제이피모간 1\r\n'
    assert parse_members(text) == {'00033':'제이피모간'}


def test_empty_and_wrong_day_not_zero():
    assert parse_daily({'output':[]},'20261016') is None
    assert parse_daily({'output':[{'stck_bsop_date':'20261015'}]},'20261016') is None
    row = dict(stck_bsop_date='20261016',total_shnu_qty='0',total_seln_qty='0',ntby_qty='0')
    assert parse_daily({'output':[row]},'20261016') == (0,0,0)
    row['ntby_qty'] = '100'
    with pytest.raises(ValueError):
        parse_daily({'output':[row]},'20261016')


def test_period_and_reference_are_daywise_not_final_price_times_total():
    with closing(sqlite3.connect(':memory:')) as conn:
        fixture(conn)
        details, totals, raw = aggregate_period(data(conn),['20261015','20261016'])
        stock = totals[totals.ticker=='005930'].iloc[0]
        assert stock.net_qty == 320
        assert stock.reference_net_amount == 80*100*2 + 80*200*2
        assert stock.complete
        assert details[details.ticker=='005930'].net_qty.tolist() == [160,160]


def test_missing_is_not_zero_and_incomplete_stock_excluded_from_complete_rank():
    with closing(sqlite3.connect(':memory:')) as conn:
        fixture(conn)
        conn.execute("DELETE FROM foreign_broker_daily WHERE ticker='005930' AND broker_code='00033'")
        details, totals, raw = aggregate_period(data(conn),['20261015','20261016'])
        missing = details[(details.ticker=='005930') & (details.broker_code=='00033')].iloc[0]
        assert pd.isna(missing.net_qty)
        assert not totals[totals.ticker=='005930'].iloc[0].complete
        assert totals[totals.complete].ticker.tolist() == ['000660']


def test_missing_close_not_replaced_by_current_price():
    with closing(sqlite3.connect(':memory:')) as conn:
        fixture(conn)
        conn.execute("DELETE FROM foreign_broker_closes WHERE trade_date='20261015'")
        details, totals, raw = aggregate_period(data(conn),['20261015','20261016'])
        assert totals.complete.all()
        assert totals.reference_net_amount.isna().all()


def test_universe_union_and_dedup():
    with closing(sqlite3.connect(':memory:')) as conn:
        conn.execute('CREATE TABLE daily_stocks (date TEXT,ticker TEXT,name TEXT)')
        conn.executemany('INSERT INTO daily_stocks VALUES (?,?,?)',[('20261016','005930','삼성전자')]*3)
        conn.execute('CREATE TABLE watchlist_items (ticker TEXT,name TEXT,added_date TEXT)')
        conn.execute("INSERT INTO watchlist_items VALUES ('000660','SK하이닉스','20261015')")
        assert collect_universe(conn,'20261016') == {'005930':'삼성전자','000660':'SK하이닉스'}


def test_collector_stores_only_requested_date_and_resume_final(tmp_path):
    path = tmp_path/'source.db'
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('CREATE TABLE daily_stocks (date TEXT,ticker TEXT,name TEXT)')
        conn.execute("INSERT INTO daily_stocks VALUES ('20261016','005930','삼성전자')")
        init_tables(conn)
        conn.execute("INSERT INTO foreign_broker_members VALUES ('00033','JP모간','2026-10-19 00:00:00')")
        conn.commit()
    crawler = SimpleNamespace(db_path=path,_get_kis_access_token=lambda:'test')
    collector = ForeignBrokerCollector(crawler, now=datetime(2026,10,19,1,tzinfo=KST))
    collector.fetch = lambda item: 200 if item[2]=='__CLOSE__' else (100,20,80)
    result = collector.run()
    assert result['completed_pairs'] == 1
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute('SELECT net_qty,is_final FROM foreign_broker_daily').fetchone() == (80,1)
    collector.fetch = lambda item: (_ for _ in ()).throw(AssertionError('final pair must not refetch'))
    assert collector.run()['requests_done'] == 0


def test_lightweight_publisher_preserves_existing_tables(tmp_path, monkeypatch):
    import foreign_broker_flow as flow
    source, target = tmp_path/'source.db',tmp_path/'web.db'
    with closing(sqlite3.connect(source)) as conn:
        fixture(conn)
    with closing(sqlite3.connect(target)) as conn:
        conn.execute('CREATE TABLE untouched (value TEXT)')
        conn.execute("INSERT INTO untouched VALUES ('preserved')")
        conn.commit()
    original = flow.check_web_snapshot
    def check_unlocked(conn):
        assert 'broker_source' not in [r[1] for r in conn.execute('PRAGMA database_list')]
        with closing(sqlite3.connect(source,timeout=.1)) as writer:
            writer.execute("UPDATE foreign_broker_members SET broker_name=broker_name")
            writer.commit()
        original(conn)
    monkeypatch.setattr(flow,'check_web_snapshot',check_unlocked)
    publish_broker_snapshot(source,target)
    with closing(sqlite3.connect(target)) as conn:
        assert conn.execute('SELECT * FROM untouched').fetchone() == ('preserved',)
        assert conn.execute('SELECT count(*) FROM foreign_broker_daily').fetchone()[0] == 8
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='foreign_broker_attempts'").fetchone()
    assert (tmp_path/'web.db.gz').is_file()


def test_fragment_ui_search_stays_local_and_safe(tmp_path):
    from streamlit.testing.v1 import AppTest
    path = tmp_path/'view.db'
    with closing(sqlite3.connect(path)) as conn:
        fixture(conn)
    app = AppTest.from_string('from foreign_broker_ui import render_foreign_broker_tab\n'
                             +f"render_foreign_broker_tab({str(path)!r},'v','20261016')").run(timeout=30)
    assert not app.exception
    assert app.selectbox[0].value is None
    before=len(app.dataframe)
    app.selectbox[0].select('005930').run()
    assert not app.exception
    assert len(app.dataframe)>before
    assert any('최근 10거래일' in str(m.value) for m in app.markdown)
    app.radio[0].set_value('최근 5거래일').run()
    assert not app.exception
    assert app.selectbox[0].value == '005930'


def test_broker_streaks_use_trading_dates_not_calendar_days(tmp_path):
    with closing(sqlite3.connect(':memory:')) as conn:
        fixture(conn)
        conn.execute("UPDATE foreign_broker_daily SET trade_date='20261019' WHERE trade_date='20261016'")
        conn.execute("UPDATE foreign_broker_closes SET trade_date='20261019' WHERE trade_date='20261016'")
        conn.execute("UPDATE foreign_broker_universe SET trade_date='20261019' WHERE trade_date='20261016'")
        frames=data(conn)
        frames['calendar']=pd.DataFrame({'date':['20261015','20261019']})
        combined=build_broker_streaks(frames,'20261019',combined=True)
        assert combined.ticker.tolist()==['005930']
        assert combined.iloc[0].streak_days==2
        assert combined.iloc[0].streak_net_qty==320
        assert combined.iloc[0].lower_bound
        individual=build_broker_streaks(frames,'20261019')
        assert len(individual)==2
        assert individual.streak_net_qty.tolist()==[160,160]
        frames['calendar']=pd.DataFrame({'date':['20261015','20261016','20261019']})
        # A real trading day missing in the middle cannot be skipped.
        assert build_broker_streaks(frames,'20261019',combined=True).empty
        assert build_broker_streaks(frames,'20261019').empty


def test_combined_requires_all_members_and_zero_breaks_streak():
    with closing(sqlite3.connect(':memory:')) as conn:
        fixture(conn)
        conn.execute("DELETE FROM foreign_broker_daily WHERE ticker='005930' AND broker_code='00033' AND trade_date='20261015'")
        frames=data(conn)
        assert build_broker_streaks(frames,'20261016',combined=True).empty
        assert build_broker_streaks(frames,'20261016').broker_code.tolist()==['00045']
        history=build_stock_net_history(frames,'005930','20261016')
        assert pd.isna(history.loc['외국계 합산','20261015'])
        assert history.loc['외국계 합산','20261016']==160
        conn.execute("UPDATE foreign_broker_daily SET net_qty=0 WHERE ticker='005930' AND trade_date='20261016'")
        assert build_broker_streaks(data(conn),'20261016').empty


def test_ten_day_history_missing_and_provisional_streak():
    with closing(sqlite3.connect(':memory:')) as conn:
        fixture(conn)
        conn.execute("UPDATE foreign_broker_daily SET is_final=0 WHERE trade_date='20261016'")
        frames=data(conn)
        frames['calendar']=pd.DataFrame({'date':pd.bdate_range('2026-10-01','2026-10-16').strftime('%Y%m%d')})
        history=build_stock_net_history(frames,'005930','20261016')
        assert len(history.columns)==10
        assert history.columns[0]=='20261016'
        assert history.loc['JP모간','20261016']==80
        assert pd.isna(history.loc['JP모간','20261014'])
        streak=build_broker_streaks(frames,'20261016',combined=True).iloc[0]
        assert streak.provisional and streak.lower_bound
        assert streak.reference_net_amount==48000
        # The as-of date is authoritative; do not fall back to an earlier day.
        frames['calendar']=pd.DataFrame({'date':['20261015','20261016','20261019']})
        assert build_broker_streaks(frames,'20261019',combined=True).empty
