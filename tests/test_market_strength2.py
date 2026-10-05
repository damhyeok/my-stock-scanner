import json
import sqlite3
import unittest
import tempfile
from pathlib import Path
import pandas as pd
from market_strength2 import summarize_index, summarize_supply, build_market_strength2_display


def bars():
    times = pd.date_range('2000-01-01 09:00', '2000-01-01 15:19', freq='min').strftime('%H:%M').tolist() + ['15:30']
    return pd.DataFrame([dict(bar_time=t, close=100+i*.01, high=100+i*.01+.01,
                              low=100+i*.01-.01, trade_date='20261002', index_name='KOSPI')
                         for i, t in enumerate(times)])


class Strength2Tests(unittest.TestCase):
    def test_full_flow_and_clv(self):
        result = summarize_index(bars())
        self.assertTrue(result['complete'])
        self.assertEqual(result['flow'], '개선')
        self.assertGreater(result['clv'], 80)
        self.assertEqual(result['auction_baseline'], '15:19')
        self.assertEqual(result['minutes'], 50)

    def test_gap_not_replaced_with_two_endpoint_return(self):
        data = bars()
        data = data[data.bar_time != '14:55']
        result = summarize_index(data)
        self.assertFalse(result['complete'])
        self.assertIsNone(result['late_return'])
        self.assertIsNone(result['clv'])

    def test_missing_morning_cannot_produce_day_clv(self):
        data = bars()
        result = summarize_index(data[data.bar_time >= '14:30'])
        self.assertTrue(result['complete'])
        self.assertIsNone(result['clv'])

    def test_aftermarket_excluded(self):
        data = bars()
        data.loc[len(data)] = dict(bar_time='16:00', close=9999, high=9999, low=9999, trade_date='20261002', index_name='KOSPI')
        self.assertEqual(summarize_index(data), summarize_index(bars()))

    def test_near_zero_flow_uses_amount_not_explosive_percentage(self):
        frame = pd.DataFrame([dict(snapshot_time='14:30', program_net=.001, non_arbitrage_net=0, basis=1),
                              dict(snapshot_time='15:30', program_net=1, non_arbitrage_net=2, basis=1.5)])
        result = summarize_supply(frame)
        self.assertAlmostEqual(result['program_delta'], .999)
        self.assertEqual(result['basis_delta'], .5)

    def test_missing_supply_endpoint(self):
        frame = pd.DataFrame([dict(snapshot_time='15:30', program_net=1, non_arbitrage_net=2, basis=None)])
        self.assertIsNone(summarize_supply(frame)['program_delta'])

    def test_materialization_survives_raw_expiration(self):
        with sqlite3.connect(':memory:') as conn:
            frame = pd.DataFrame([dict(trade_date='20261002', analysis_type='closing', snapshot_time=t,
                                       program_net=i, non_arbitrage_net=i, basis=i)
                                  for i, t in enumerate(['14:30', '15:00', '15:20', '15:30'])])
            frame.to_sql('market_strength_snapshots', conn, index=False)
            bars().to_sql('intraday_index_bars', conn, index=False)
            build_market_strength2_display(conn)
            before = conn.execute('SELECT payload FROM web_market_strength2').fetchone()[0]
            self.assertTrue(json.loads(before)['indices']['KOSPI']['complete'])
            conn.execute('DELETE FROM intraday_index_bars')
            build_market_strength2_display(conn)
            self.assertEqual(before, conn.execute('SELECT payload FROM web_market_strength2').fetchone()[0])

    def test_absent_tables_safe(self):
        with sqlite3.connect(':memory:') as conn:
            build_market_strength2_display(conn)
            self.assertEqual(conn.execute('SELECT count(*) FROM web_market_strength2').fetchone()[0], 0)

    def test_streamlit_render_and_no_future_display(self):
        from streamlit.testing.v1 import AppTest
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'view.db')
            with sqlite3.connect(path) as conn:
                data = pd.DataFrame([dict(trade_date='20261002', analysis_type='closing', snapshot_time=t,
                                         program_net=i, non_arbitrage_net=i, basis=i)
                                    for i, t in enumerate(['14:30', '15:00', '15:20', '15:30'])])
                data.to_sql('market_strength_snapshots', conn, index=False)
                bars().to_sql('intraday_index_bars', conn, index=False)
                build_market_strength2_display(conn)
            conn.close()
            source = "from market_strength2 import render_market_strength2\nrender_market_strength2(%r, '20261002', %r)"
            app = AppTest.from_string(source % (path, '정규장(16:00)')).run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.dataframe), 2)
            app = AppTest.from_string(source % (path, '오후(14:00)')).run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.dataframe), 0)


if __name__ == '__main__':
    unittest.main()
