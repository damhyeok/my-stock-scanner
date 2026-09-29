import unittest

import pandas as pd

from rise_rankings import RISE_LARGE_CAP_CATEGORY, build_rise_rank_tables


class RiseRankingsTest(unittest.TestCase):
    def test_top60_overlap_includes_ranks_31_through_60(self):
        rows = [dict(ticker=str(i), name=str(i), category=RISE_LARGE_CAP_CATEGORY,
                     fluctuation_rate=100-i, trading_value=i, market_cap=300_000_000_000)
                for i in range(65)]
        rows += [dict(ticker=str(i), category='VOLUME_TOP_60', trading_value=100-i)
                 for i in (0, 29, 30, 59, 60)]
        # A historical-category row must not contaminate the new snapshot.
        rows.append(dict(ticker='old', category='RISE_TOP_30', fluctuation_rate=999))
        top, overlap = build_rise_rank_tables(pd.DataFrame(rows))
        self.assertEqual(len(top), 60)
        self.assertEqual(top['rise_rank'].tolist(), list(range(1, 61)))
        self.assertEqual(overlap['rise_rank'].tolist(), [1, 30, 31, 60])

    def test_old_top30_is_not_misrepresented_as_large_cap_ranking(self):
        rows = []
        for index in range(35):
            rows.append({
                "ticker": f"{index:06d}",
                "name": f"상승{index}",
                "category": "RISE_TOP_30",
                "fluctuation_rate": 35 - index,
                "previous_day_rate": index / 10,
                "market_cap": (index + 1) * 100_000_000,
                "trading_value": (index + 1) * 10_000_000,
                "sector": "테스트",
            })
        for index in (10, 0, 20):
            rows.append({
                "ticker": f"{index:06d}",
                "name": f"거래{index}",
                "category": "VOLUME_TOP_60",
                "fluctuation_rate": 0,
                "market_cap": 0,
                "trading_value": (100 - index) * 100_000_000,
                "sector": "테스트",
            })

        top30, overlap = build_rise_rank_tables(pd.DataFrame(rows))

        self.assertTrue(top30.empty)
        self.assertTrue(overlap.empty)

    def test_safety_filter_keeps_only_verified_large_caps(self):
        rows = [
            dict(ticker="small", name="소형주", category=RISE_LARGE_CAP_CATEGORY,
                 fluctuation_rate=20, market_cap=299_999_999_999, trading_value=1),
            dict(ticker="large", name="대형주", category=RISE_LARGE_CAP_CATEGORY,
                 fluctuation_rate=10, market_cap=300_000_000_000, trading_value=2),
            dict(ticker="large", category="VOLUME_TOP_60", trading_value=2),
        ]
        top, overlap = build_rise_rank_tables(pd.DataFrame(rows))
        self.assertEqual(top["name"].tolist(), ["대형주"])
        self.assertEqual(top["rise_rank"].tolist(), [1])
        self.assertEqual(overlap["name"].tolist(), ["대형주"])

    def test_returns_empty_tables_when_new_category_is_not_available(self):
        frame = pd.DataFrame([
            {"ticker": "005930", "category": "VOLUME_TOP_60", "trading_value": 1}
        ])

        top30, overlap = build_rise_rank_tables(frame)

        self.assertTrue(top30.empty)
        self.assertTrue(overlap.empty)


if __name__ == "__main__":
    unittest.main()
