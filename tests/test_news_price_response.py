import sqlite3
import unittest

from news_issues import record_issues, build_issue_display
from news_price_response import save_price_context, assess


class PriceResponseTests(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(':memory:')
        self.c.executescript('''
            CREATE TABLE daily_stocks(date TEXT,session TEXT,category TEXT,ticker TEXT,close REAL,collected_at_kst TEXT,fluctuation_rate REAL,sector TEXT);
            CREATE TABLE intraday_stock_bars(ticker TEXT,trade_date TEXT,bar_time TEXT,close REAL,collected_at_kst TEXT);
            CREATE TABLE stock_program_net_snapshots(ticker TEXT,trade_date TEXT,program_net_buy REAL,collected_at_kst TEXT);
        ''')
        stock = dict(ticker='005930',name='삼성전자',sector='반도체')
        news = dict(title='삼성전자 공급계약 체결',source='매체A',link='https://example.org/news',published_at='2026-09-15 10:00:00')
        record_issues(self.c,[stock],[(stock,news)],'20260915','11:30','2026-09-15 11:30:00')
        for day in ['07','08','09','10','11','14']:
            self.c.execute('INSERT INTO daily_stocks VALUES (?,?,?,?,?,?,?,?)',('202609'+day,'정규장(16:00)','VOLUME_TOP_60','005930',100,'2026-09-'+day+' 16:00:00',0,'반도체'))
        self.c.execute("INSERT INTO daily_stocks VALUES ('20260915','11:30','VOLUME_TOP_60','005930',102,'2026-09-15 11:29:00',2,'반도체')")
        self.c.execute("INSERT INTO intraday_stock_bars VALUES ('005930','20260915','09:59',100,'2026-09-15 10:10:00')")
        self.c.executemany('INSERT INTO stock_program_net_snapshots VALUES (?,?,?,?)', [('005930','20260915',1e8,'2026-09-15 10:00:00'),('005930','20260915',2e8,'2026-09-15 11:00:00'),('005930','20260915',99e8,'2026-09-15 14:00:00')])

    def tearDown(self):
        self.c.close()

    def result(self):
        save_price_context(self.c,'20260915','11:30','2026-09-15 11:30:00')
        return self.c.execute('SELECT * FROM news_issue_price_context').fetchone()

    def test_measured_reaction_flow_and_snapshot(self):
        row = self.result()
        self.assertEqual(row['reaction_rate'],2)
        self.assertEqual(row['pre_news_5d'],0)
        self.assertEqual(row['program_change_eok'],1)
        self.assertEqual(row['verdict'],'반응 시작·검토')
        self.c.execute('UPDATE daily_stocks SET close=999')
        self.assertEqual(self.result()['reaction_rate'],2)
        build_issue_display(self.c)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM web_news_issue_price_context').fetchone()[0],1)

    def test_future_observed_bar_is_not_used(self):
        self.c.execute("UPDATE intraday_stock_bars SET collected_at_kst='2026-09-15 14:00:00'")
        row = self.result()
        self.assertIsNone(row['reaction_rate'])
        self.assertEqual(row['verdict'],'가격 자료 부족')

    def test_missing_history_not_zero(self):
        self.c.execute("DELETE FROM daily_stocks WHERE date='20260909'")
        self.assertIsNone(self.result()['pre_news_5d'])

    def test_adverse_and_expectations_not_candidates(self):
        self.assertEqual(assess('호재 가능',3,'기대·검토 보도',1,0,1)[0],'뉴스 근거 확인')
        self.assertEqual(assess('호재 가능',3,'제목 보도',1,0,1,True)[0],'상충·악재 확인')
        self.assertEqual(assess('호재 가능',3,'제목 보도',1,12,1)[0],'상승 진행·주의')

    def test_price_before_publication_not_reaction(self):
        self.c.execute("UPDATE daily_stocks SET collected_at_kst='2026-09-15 09:30:00' WHERE date='20260915'")
        self.assertIsNone(self.result()['reaction_rate'])
