import unittest

import pandas as pd

from crawler import StockCrawler
from sector_overrides import override_frame_sectors


class SectorOverrideIntegrationTests(unittest.TestCase):
    def test_collector_precedence_and_existing_duplicate_sector(self):
        crawler = StockCrawler.__new__(StockCrawler)
        self.assertEqual(crawler._normalize_sector("267260", "HD현대일렉트릭", "에너지"), "전력")
        self.assertEqual(
            crawler._normalize_sector("000000", "삼성에스디에스", "기타"),
            "플랫폼·IT",
        )

    def test_display_rows_reclassified_without_changing_other_columns(self):
        source = pd.DataFrame(
            [
                {"name": "HD현대일렉트릭", "sector": "에너지", "ticker": "267260"},
                {"name": "삼성에스디에스", "sector": "플랫폼·IT", "ticker": "018260"},
            ]
        )
        actual = override_frame_sectors(source)
        self.assertEqual(actual["sector"].tolist(), ["전력", "플랫폼·IT"])
        self.assertEqual(actual["ticker"].tolist(), source["ticker"].tolist())
        self.assertEqual(source["sector"].tolist(), ["에너지", "플랫폼·IT"])


if __name__ == "__main__":
    unittest.main()
