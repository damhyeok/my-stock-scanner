import unittest

import pandas as pd

from rise_rankings import build_rise_rank_tables


class RiseRankingsTest(unittest.TestCase):
    def test_builds_top30_and_volume_intersection_with_independent_ranks(self):
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

        self.assertEqual(len(top30), 30)
        self.assertEqual(top30.iloc[0]["name"], "상승0")
        self.assertEqual(int(top30.iloc[0]["rise_rank"]), 1)
        self.assertEqual(top30.iloc[0]["previous_day_rate"], 0)
        self.assertEqual(overlap["name"].tolist(), ["상승0", "상승10", "상승20"])
        self.assertEqual(overlap["trading_rank"].astype(int).tolist(), [1, 2, 3])

    def test_returns_empty_tables_when_new_category_is_not_available(self):
        frame = pd.DataFrame([
            {"ticker": "005930", "category": "VOLUME_TOP_60", "trading_value": 1}
        ])

        top30, overlap = build_rise_rank_tables(frame)

        self.assertTrue(top30.empty)
        self.assertTrue(overlap.empty)


if __name__ == "__main__":
    unittest.main()
