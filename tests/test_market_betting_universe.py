import unittest

from market_betting_engine.universe import (
    AdaptiveUniverseConfig,
    build_adaptive_universe,
    select_candidate_sectors,
)


def row(ticker, sector, turnover, change=0, category="VOLUME_TOP_60"):
    return {
        "ticker": ticker,
        "name": ticker,
        "sector": sector,
        "trading_value": turnover,
        "fluctuation_rate": change,
        "category": category,
    }


class AdaptiveUniverseTests(unittest.TestCase):
    def test_candidate_ranking_is_activity_based_and_labels_no_flow_claim(self):
        candidates = select_candidate_sectors([
            row("000001", "조선", 500, 3),
            row("000002", "조선", 300, 2),
            row("000003", "반도체", 600, 5),
            row("000004", "기타", 900, 9),
        ])
        self.assertEqual([item.name for item in candidates], ["조선", "반도체"])
        self.assertAlmostEqual(candidates[0].leader_concentration, 0.625)

    def test_expansion_uses_only_candidates_and_respects_total_cap(self):
        seed = [
            row("100001", "A", 1000, 1), row("100002", "B", 900, 1),
            row("100003", "C", 10, 1),
        ]
        discovery = [
            row("110001", "A", 800), row("110002", "A", 700),
            row("120001", "B", 850), row("120002", "B", 650),
            row("130001", "C", 500),
        ]
        config = AdaptiveUniverseConfig(
            candidate_sector_limit=2,
            stocks_per_sector=3,
            total_stock_limit=4,
        )
        result = build_adaptive_universe(seed, discovery, config)
        self.assertEqual([item.name for item in result.candidates], ["A", "B"])
        self.assertEqual(len(result.stocks), 4)
        self.assertEqual({item["sector"] for item in result.stocks}, {"A", "B"})
        self.assertNotIn("C", {item["sector"] for item in result.stocks})

    def test_round_robin_prevents_one_sector_from_consuming_cap(self):
        seed = [row("200001", "A", 1000), row("200002", "B", 900)]
        discovery = [
            row("210001", "A", 800), row("210002", "A", 700),
            row("220001", "B", 600), row("220002", "B", 500),
        ]
        result = build_adaptive_universe(
            seed,
            discovery,
            AdaptiveUniverseConfig(candidate_sector_limit=2, stocks_per_sector=3, total_stock_limit=3),
        )
        sectors = [item["sector"] for item in result.stocks]
        self.assertEqual(sectors[:2], ["A", "B"])


if __name__ == "__main__":
    unittest.main()
