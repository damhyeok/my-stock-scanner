import unittest
import pandas as pd
from sector_interest import build_interest


class SectorInterestTest(unittest.TestCase):
    def fixture(self):
        rows = []
        for i in range(8):
            date = f'202609{i+1:02d}'
            for sector, value in [('A', 10 if i < 5 else 30), ('B', 90 if i < 5 else 70)]:
                for kind, count in [('ALL', 5), ('RISING', 2 if i < 5 else 4)]:
                    rows.append(dict(date=date, sector=sector, trend_kind=kind,
                                     trading_value=value, stock_count=count, included_stocks='sample'))
        return pd.DataFrame(rows)

    def test_increasing_share_and_breadth(self):
        rows, path = build_interest(self.fixture(), 1)
        self.assertEqual(rows.iloc[0]['sector'], 'B')
        self.assertEqual(rows.iloc[0]['status'], '직전일 대비 동일')
        self.assertAlmostEqual(rows.iloc[0]['change'], 0)
        self.assertAlmostEqual(rows.iloc[0]['share'], 70)
        self.assertEqual(len(path), 8)

    def test_missing_history_is_not_zero(self):
        frame = self.fixture()
        frame = frame[~(frame['sector'].eq('A') & frame['date'].eq('20260907'))]
        rows, path = build_interest(frame)
        self.assertEqual(rows.set_index('sector').loc['A', 'status'], '비교 자료 부족')
        self.assertTrue(path.loc[path['sector'].eq('A') & path['date'].eq('20260907'), 'share'].isna().all())

    def test_empty_and_single_member(self):
        frame = self.fixture()
        self.assertTrue(build_interest(frame.iloc[:0])[0].empty)
        frame.loc[frame['sector'].eq('A'), 'stock_count'] = 1
        rows, _ = build_interest(frame)
        self.assertEqual(rows.set_index('sector').loc['A', 'status'], '직전일 대비 동일')

    def test_change_matches_last_two_graph_points(self):
        frame = self.fixture()
        frame.loc[frame['date'].eq('20260908') & frame['sector'].eq('A'), 'trading_value'] = 70
        rows, path = build_interest(frame)
        a = rows.set_index('sector').loc['A']
        points = path[path['sector'].eq('A')].sort_values('date')['share']
        self.assertAlmostEqual(a['share'], points.iloc[-1])
        self.assertAlmostEqual(a['change'], points.iloc[-1] - points.iloc[-2])
        self.assertEqual(a['status'], '직전일 대비 비중 증가')
