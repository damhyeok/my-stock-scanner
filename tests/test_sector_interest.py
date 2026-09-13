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
        self.assertEqual(rows.iloc[0]['sector'], 'A')
        self.assertEqual(rows.iloc[0]['status'], '관심 증가 중')
        self.assertAlmostEqual(rows.iloc[0]['change'], 20)
        self.assertAlmostEqual(rows.iloc[0]['activity'], 3)
        self.assertEqual(len(path), 8)

    def test_missing_history_is_not_zero(self):
        frame = self.fixture()
        frame = frame[~(frame['sector'].eq('A') & frame['date'].eq('20260907'))]
        rows, path = build_interest(frame)
        self.assertEqual(rows.set_index('sector').loc['A', 'status'], '판단 자료 부족')
        self.assertTrue(path.loc[path['sector'].eq('A') & path['date'].eq('20260907'), 'share'].isna().all())

    def test_empty_and_single_member(self):
        frame = self.fixture()
        self.assertTrue(build_interest(frame.iloc[:0])[0].empty)
        frame.loc[frame['sector'].eq('A'), 'stock_count'] = 1
        rows, _ = build_interest(frame)
        self.assertEqual(rows.set_index('sector').loc['A', 'status'], '일부 종목에 집중')
