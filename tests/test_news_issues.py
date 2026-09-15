import json
import sqlite3
import unittest
import tempfile
from pathlib import Path
from contextlib import closing, nullcontext
from unittest.mock import Mock, patch
from news_issues import record_issues, build_issue_display, prune_issues, classify


class NewsIssueTests(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(':memory:')
        self.stock = dict(ticker='005930', name='삼성전자', sector='반도체 메모리')

    def tearDown(self):
        self.c.close()

    def article(self, link='1', title='삼성전자 공급계약 체결', source='신문A', published='2026-09-15 09:00'):
        return self.stock, dict(title=title, link='https://example.org/'+link, source=source, published_at=published)

    def save(self, items, session='09:30', time='2026-09-15 09:30:00', **kw):
        record_issues(self.c, [self.stock], items, '20260915', session, time, **kw)

    def test_duplicate_and_snapshot_immutability(self):
        self.save([self.article()])
        first = dict(self.c.execute('SELECT * FROM news_issue_versions').fetchone())
        self.save([self.article(), self.article('2', source='신문B')], '11:30', '2026-09-15 11:30:00')
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM news_issue_articles').fetchone()[0], 2)
        self.assertEqual(self.c.execute('SELECT COUNT(DISTINCT event_id) FROM news_issue_articles').fetchone()[0], 1)
        old = self.c.execute("SELECT version_id FROM news_issue_snapshots WHERE session='09:30'").fetchone()[0]
        self.assertEqual(first['version_id'], old)
        self.assertEqual(len(json.loads(first['evidence_json'])), 1)
        self.save([self.article('3')])
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM news_issue_articles').fetchone()[0], 2)

    def test_future_news_and_failure_carry_forward(self):
        self.save([self.article(), self.article('future', published='2026-09-15 15:00')])
        self.save([], '11:30', '2026-09-15 11:30:00', failures=1)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM news_issue_articles').fetchone()[0], 1)
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM news_issue_snapshots WHERE session='11:30'").fetchone()[0], 1)
        self.assertEqual(self.c.execute("SELECT status FROM news_issue_runs WHERE session='11:30'").fetchone()[0], 'partial')

    def test_capped_spread_and_direction(self):
        self.save([self.article(str(i), source='신문'+str(i)) for i in range(12)])
        latest = self.c.execute('SELECT * FROM news_issue_versions ORDER BY rowid DESC LIMIT 1').fetchone()
        self.assertEqual(latest['spread_bonus'], .5)
        self.assertNotIn('공시 확인', latest['confidence'])
        self.assertEqual(classify('삼성전자 공급계약 해지')[1], '악재 가능')

    def test_distinct_numbers_not_merged(self):
        self.save([self.article('1', '삼성전자 100억원 공급계약 체결'), self.article('2', '삼성전자 900억원 공급계약 체결')])
        self.assertEqual(self.c.execute('SELECT COUNT(DISTINCT event_id) FROM news_issue_articles').fetchone()[0], 2)

    def test_compact_display_keeps_referenced_versions(self):
        self.save([self.article()])
        self.save([], '11:30', '2026-09-15 11:30:00')
        prune_issues(self.c)
        build_issue_display(self.c)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM web_news_issue_versions').fetchone()[0], 1)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM web_news_issue_snapshots').fetchone()[0], 2)

    def test_material_change_keeps_history(self):
        self.save([self.article('1', '삼성전자 대형 고객사 반도체 공급계약 체결')])
        self.save([self.article('2', '삼성전자 대형 고객사 반도체 공급계약 해지')], '14:00', '2026-09-15 14:00:00')
        self.assertEqual(self.c.execute("SELECT change_kind FROM news_issue_snapshots WHERE session='14:00'").fetchone()[0], '중요 내용 변경')
        self.assertEqual(self.c.execute("SELECT direction FROM news_issue_versions v JOIN news_issue_snapshots s USING(version_id) WHERE session='09:30'").fetchone()[0], '호재 가능')

    def test_overnight_existing_issue_carried(self):
        record_issues(self.c, [self.stock], [self.article(published='2026-09-14 16:01')], '20260914', '16:10', '2026-09-14 16:10:00')
        self.save([], window_start='2026-09-14 15:30')
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM news_issue_snapshots WHERE date='20260915'").fetchone()[0], 1)

    def test_late_old_article_does_not_reverse_newer_adverse_issue(self):
        self.save([self.article('1', '삼성전자 대형 고객사 반도체 공급계약 해지', published='2026-09-15 09:20')])
        self.save([self.article('2', '삼성전자 대형 고객사 반도체 공급계약 체결', published='2026-09-15 09:00')], '11:30', '2026-09-15 11:30:00')
        row = self.c.execute("SELECT direction FROM news_issue_versions v JOIN news_issue_snapshots s USING(version_id) WHERE session='11:30'").fetchone()
        self.assertEqual(row[0], '악재 가능')

    def test_out_of_order_run_does_not_use_future_version(self):
        self.save([self.article()], '14:00', '2026-09-15 14:00:00')
        self.save([], '09:30', '2026-09-15 09:30:00')
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM news_issue_snapshots WHERE session='09:30'").fetchone()[0], 0)

    def test_retention_keeps_only_bounded_referenced_history(self):
        from datetime import datetime, timedelta
        for offset in range(95):
            date = datetime(2026, 1, 1) + timedelta(days=offset)
            stamp = date.strftime('%Y-%m-%d') + ' 09:30:00'
            record_issues(self.c, [self.stock], [self.article(str(offset), published=stamp)], date.strftime('%Y%m%d'), '09:30', stamp)
        prune_issues(self.c)
        build_issue_display(self.c)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM news_issue_runs').fetchone()[0], 90)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM web_news_issue_runs').fetchone()[0], 15)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM web_news_issue_snapshots s LEFT JOIN web_news_issue_versions v USING(version_id) WHERE v.version_id IS NULL').fetchone()[0], 0)

    def test_collector_to_bounded_web_and_render(self):
        import importlib.util
        import sys
        import pandas as pd
        # This is an offline integration test; no HTTP request is executed.
        http_stub = {} if importlib.util.find_spec('requests') else {'requests': Mock()}
        with patch.dict(sys.modules, http_stub):
            from news_collector import NewsCollector
        from web_database import build_web_database
        from news_issues import render_issue_tab
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.db'
            web = Path(folder) / 'web.db'
            collector = NewsCollector(source)
            collector.collected_at_kst = '2026-09-15 09:30:00'
            with closing(sqlite3.connect(source)) as conn:
                conn.execute('CREATE TABLE daily_stocks (date TEXT, session TEXT, category TEXT, ticker TEXT, name TEXT, sector TEXT, trading_value REAL, fluctuation_rate REAL, collected_at_kst TEXT)')
                conn.commit()
            stocks = pd.DataFrame([self.stock])
            with patch.object(collector, '_get_latest_stock_universe', return_value=('20260915', '09:30', stocks)), patch.object(collector, '_fetch_google_news', return_value=[self.article()[1]]), patch.object(collector.run.__globals__['time'], 'sleep'):
                self.assertEqual(collector.run(), 1)
            build_web_database(source, web)
            with closing(sqlite3.connect(web)) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='news_issue_articles'").fetchone()[0], 0)
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM web_news_issue_versions').fetchone()[0], 1)
            st = Mock()
            st.expander.side_effect = lambda *a, **k: nullcontext()
            st.selectbox.return_value = 0
            self.assertTrue(render_issue_tab(st, web, '20260915', '09:30'))
            self.assertEqual(st.dataframe.call_count, 3)
            self.assertFalse(render_issue_tab(st, web, '20260914', '09:30'))
