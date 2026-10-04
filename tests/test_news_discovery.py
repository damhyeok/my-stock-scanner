import json
import sqlite3
from pathlib import Path
from unittest.mock import Mock

import pandas as pd

from news_discovery import (build_candidates, build_discovery_display, catalog,
    collect_discovery, headline_features, init_discovery, match_stocks,
    prune_discovery, render_discovery_tab)


def article(i='1', title='삼성전자 100억원 공급계약 체결', published='2026-10-06 09:00:00', **kw):
    return dict(article_id=i, ticker='005930', name='삼성전자', sector='반도체',
                title=title, source='신문'+i, link='https://example.com/'+i,
                published_at=published, first_seen='2026-10-06 09:30:00', **kw)


def make_db(path):
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE daily_stocks(date TEXT, session TEXT, ticker TEXT, name TEXT, sector TEXT, fluctuation_rate REAL)')
        c.execute("INSERT INTO daily_stocks VALUES ('20261002','정규장(16:00)','005930','삼성전자','반도체',2)")
        c.execute('CREATE TABLE model_universe_snapshots(ticker TEXT,name TEXT,market_cap REAL)')
        c.execute("INSERT INTO model_universe_snapshots VALUES ('123456','새로운기업',400000000000)")


def test_positive_uncertain_negative_and_commentary():
    assert headline_features('삼성전자 공급계약 체결')['group'] == '호재 후보'
    assert headline_features('삼성전자 수주 기대')['group'] == '기대·해설 보도'
    assert headline_features('삼성전자 공급계약 취소')['group'] == '악재·혼재 확인'
    assert headline_features('[특징주] 삼성전자 수주 급등')['group'] == '기대·해설 보도'
    assert headline_features('삼성전자 적자 축소 발표')['group'] == '호재 후보'
    assert '미검증' in headline_features('공시 공급계약 체결')['evidence']


def test_price_cannot_change_score():
    a = build_candidates([article(fluctuation_rate=-5)], '2026-10-06 09:30:00')[0]
    b = build_candidates([article(fluctuation_rate=25)], '2026-10-06 09:30:00')[0]
    assert a['score'] == b['score']


def test_repeated_reporting_does_not_refresh_old_news():
    old = article('1', published='2026-10-02 16:00:00')
    new = article('2')
    events = build_candidates([old,new], '2026-10-06 09:30:00')
    assert len(events) == 1
    assert events[0]['freshness_score'] == 0
    assert events[0]['source_count'] == 2


def test_source_bonus_capped_and_numbers_separate():
    events = build_candidates([article(str(i)) for i in range(15)], '2026-10-06 09:30:00')
    assert events[0]['spread_bonus'] == .5
    assert len(events[0]['articles']) == 6
    assert len(build_candidates([article(),article('2',title='삼성전자 900억원 공급계약 체결')], '2026-10-06 09:30:00')) == 2


def test_future_news_excluded_and_recent_sorted_first():
    items = [article('1'), article('2', title='삼성전자 200억원 공급계약 체결', published='2026-10-06 09:10:00'), article('3', published='2026-10-06 11:00:00')]
    events = build_candidates(items,'2026-10-06 09:30:00')
    assert len(events) == 2
    assert events[0]['article_id'] == '2'


def test_catalog_includes_outside_top60_and_boundary(tmp_path):
    path = tmp_path/'a.db'
    make_db(path)
    with sqlite3.connect(path) as c:
        stocks = catalog(c)
    assert any(s['name'] == '새로운기업' for s in stocks)
    assert match_stocks('새로운기업은 공급계약 체결',stocks)
    assert not match_stocks('삼성전자우 공급계약 체결',stocks)
    assert not match_stocks('KT&G 공급계약', [dict(ticker='030200',name='KT',sector='통신')])


def test_collector_idempotent_partial_and_old_date(tmp_path):
    path = tmp_path/'a.db'
    make_db(path)
    item = dict(title='새로운기업 공급계약 체결',link='https://example.org/new',source='A',published_at='2026-10-06 09:00:00')
    fetch = Mock(side_effect=[[item],RuntimeError('offline'),[],[],[]])
    result = collect_discovery(path,'20261006','09:30','2026-10-06 09:30:00',fetch)
    assert result['status'] == 'partial' and result['events'] == 1
    assert fetch.call_count == 5
    assert collect_discovery(path,'20261006','09:30','2026-10-06 09:30:00',fetch)['status'] == 'already_saved'
    assert collect_discovery(path,'20261002','16:00','2026-10-06 09:30:00',fetch)['status'] == 'skipped_old_date'
    assert fetch.call_count == 5


def test_snapshot_preserved_and_compact_web_export(tmp_path):
    path = tmp_path/'a.db'
    make_db(path)
    item = {k:v for k,v in article().items() if k in ('title','link','source','published_at')}
    collect_discovery(path,'20261006','09:30','2026-10-06 09:30:00',lambda q:[item])
    with sqlite3.connect(path) as c:
        first = c.execute('SELECT version_id FROM news_discovery_snapshots').fetchone()[0]
    collect_discovery(path,'20261006','14:00','2026-10-06 14:00:00',lambda q:[])
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT version_id FROM news_discovery_snapshots WHERE session='09:30'").fetchone()[0] == first
        build_discovery_display(c)
        assert c.execute('SELECT COUNT(*) FROM web_news_discovery_snapshots').fetchone()[0] == 2
        assert c.execute('SELECT COUNT(*) FROM web_news_discovery_versions').fetchone()[0] == 1


def test_retention_bounded():
    c = sqlite3.connect(':memory:')
    init_discovery(c)
    from datetime import datetime,timedelta
    for i in range(100):
        date = (datetime(2026,1,1)+timedelta(days=i)).strftime('%Y%m%d')
        stamp = (datetime(2026,1,1)+timedelta(days=i)).strftime('%Y-%m-%d 09:30:00')
        c.execute('INSERT INTO news_discovery_runs VALUES (?,?,?,?,?,?,?)',(date,'09:30',stamp,5,0,1,1))
        c.execute('INSERT INTO news_discovery_snapshots VALUES (?,?,?,?)',(date,'09:30',0,'{}'))
    prune_discovery(c)
    build_discovery_display(c)
    assert c.execute('SELECT COUNT(*) FROM news_discovery_runs').fetchone()[0] == 90
    assert c.execute('SELECT COUNT(*) FROM web_news_discovery_runs').fetchone()[0] == 15
    c.close()


def test_ui_empty_and_missing_prices_keeps_candidate(tmp_path):
    path = tmp_path/'a.db'
    make_db(path)
    st = Mock()
    render_discovery_tab(st,path,'20261006','09:30')
    assert st.info.called
    item = {k:v for k,v in article().items() if k in ('title','link','source','published_at')}
    collect_discovery(path,'20261006','09:30','2026-10-06 09:30:00',lambda q:[item])
    with sqlite3.connect(path) as c:
        build_discovery_display(c)
    st = Mock()
    st.expander.return_value.__enter__ = Mock()
    st.expander.return_value.__exit__ = Mock(return_value=False)
    st.selectbox.return_value = 0
    render_discovery_tab(st,path,'20261006','09:30',pd.DataFrame())
    assert st.markdown.called
    assert '삼성전자' in st.markdown.call_args_list[0].args[0]


def test_existing_news_comparison_is_labeled_and_asof(tmp_path):
    from news_issues import record_issues, build_issue_display
    path = tmp_path/'a.db'
    make_db(path)
    with sqlite3.connect(path) as c:
        a = article()
        stock = {k:a[k] for k in ('ticker','name','sector')}
        record_issues(c,[stock],[(stock,a)],'20261006','09:30','2026-10-06 09:30:00')
        build_issue_display(c)
        c.commit()
    st = Mock()
    st.expander.return_value.__enter__ = Mock()
    st.expander.return_value.__exit__ = Mock(return_value=False)
    st.selectbox.return_value = 0
    render_discovery_tab(st,path,'20261006','09:30')
    assert '기존 수집 기사' in st.info.call_args.args[0]
    assert st.markdown.called
