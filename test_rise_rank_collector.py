import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from crawler import StockCrawler


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self.payload


class RiseRankCollectorTest(unittest.TestCase):
    def test_daily_stocks_schema_persists_previous_day_rate(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "stocks.db"
            crawler = StockCrawler(str(db_path))
            crawler.save_to_db(pd.DataFrame([{
                "ticker": "005930",
                "name": "삼성전자",
                "close": 81000,
                "fluctuation_rate": 5.25,
                "previous_day_rate": -1.75,
                "market_cap": 81_000_000_000,
                "volume": 1200,
                "trading_value": 97_200_000,
                "foreign_net": 0,
                "inst_net": 0,
                "sector": "반도체",
                "theme": "",
            }]), "RISE_TOP_30")

            connection = sqlite3.connect(db_path)
            try:
                saved = connection.execute(
                    "SELECT previous_day_rate FROM daily_stocks WHERE ticker='005930'"
                ).fetchone()[0]
            finally:
                connection.close()

            self.assertEqual(saved, -1.75)

    @patch("crawler.time.sleep", return_value=None)
    @patch("crawler.requests.get")
    def test_collects_common_stocks_and_hydrates_market_values(self, mock_get, _mock_sleep):
        mock_get.side_effect = [
            FakeResponse({
                "rt_cd": "0",
                "output": [
                    {
                        "stck_shrn_iscd": "005930",
                        "hts_kor_isnm": "삼성전자",
                        "stck_prpr": "80000",
                        "prdy_ctrt": "5.25",
                        "acml_vol": "1000",
                    },
                    {
                        "stck_shrn_iscd": "069500",
                        "hts_kor_isnm": "KODEX 200",
                        "stck_prpr": "40000",
                        "prdy_ctrt": "4.20",
                        "acml_vol": "900",
                    },
                ],
            }),
            FakeResponse({
                "rt_cd": "0",
                "output": {
                    "stck_prpr": "81000",
                    "lstn_stcn": "1000000",
                    "acml_vol": "1200",
                    "acml_tr_pbmn": "97200000",
                },
            }),
            FakeResponse({
                "rt_cd": "0",
                "output": [
                    {"stck_bsop_date": "20260821", "prdy_ctrt": "5.25"},
                    {"stck_bsop_date": "20260820", "prdy_ctrt": "-1.75"},
                    {"stck_bsop_date": "20260819", "prdy_ctrt": "0.50"},
                ],
            }),
        ]
        crawler = StockCrawler.__new__(StockCrawler)
        crawler.target_date = "20260821"
        crawler.kis_base_url = "https://example.test"
        crawler.kis_app_key = "key"
        crawler.kis_app_secret = "secret"
        crawler._get_kis_access_token = lambda: "token"

        result = crawler.get_rise_top_data()

        self.assertEqual(result["ticker"].tolist(), ["005930"])
        self.assertEqual(result.iloc[0]["close"], 81000)
        self.assertEqual(result.iloc[0]["market_cap"], 81_000_000_000)
        self.assertEqual(result.iloc[0]["trading_value"], 97_200_000)
        self.assertEqual(result.iloc[0]["previous_day_rate"], -1.75)
        rank_params = mock_get.call_args_list[0].kwargs["params"]
        self.assertEqual(rank_params["FID_COND_SCR_DIV_CODE"], "20170")
        self.assertEqual(rank_params["FID_RANK_SORT_CLS_CODE"], "0")
        self.assertEqual(rank_params["FID_TRGT_EXLS_CLS_CODE"], "0000001101")

    def test_missing_previous_rate_is_saved_as_null(self):
        with tempfile.TemporaryDirectory() as directory:
            crawler = StockCrawler(str(Path(directory) / "stocks.db"))
            crawler.save_to_db(pd.DataFrame([{
                "ticker": "005930", "name": "삼성전자", "close": 100,
                "trading_value": 1000, "previous_day_rate": pd.NA,
            }]), "RISE_TOP_30")
            with closing(sqlite3.connect(crawler.db_path)) as conn:
                self.assertEqual(conn.execute("SELECT previous_day_rate FROM daily_stocks").fetchall(), [(None,)])

    @patch("crawler.time.sleep")
    @patch("crawler.ProgramNetBuyScanner")
    def test_rise_only_ticker_and_optional_failure_preserve_original_snapshots(self, _scanner, _sleep):
        with tempfile.TemporaryDirectory() as directory:
            crawler = StockCrawler(str(Path(directory) / "stocks.db"))
            market = pd.DataFrame([{
                "ticker": "005930", "name": "삼성전자", "sector": "반도체",
                "close": 100, "fluctuation_rate": 1, "market_cap": 1000,
                "volume": 10, "trading_value": 1000,
            }])
            investor = pd.DataFrame([{"ticker": "005930", "foreign_net": 1, "inst_net": 2}])
            rise = market.copy()
            rise["ticker"] = "000660"
            rise["name"] = "SK하이닉스"
            rise["previous_day_rate"] = pd.NA
            with patch.object(crawler, "get_market_data", return_value=market), \
                 patch.object(crawler, "get_investor_data", return_value=investor), \
                 patch.object(crawler, "get_sector_info", return_value="반도체"), \
                 patch.object(crawler, "get_rise_top_data", return_value=rise) as get_rise:
                self.assertTrue(crawler.run())
                with closing(sqlite3.connect(crawler.db_path)) as conn:
                    self.assertEqual(conn.execute("SELECT ticker FROM daily_stocks WHERE category='RISE_TOP_30'").fetchall(), [("000660",)])
                get_rise.side_effect = RuntimeError("provider unavailable")
                self.assertTrue(crawler.run())
                with closing(sqlite3.connect(crawler.db_path)) as conn:
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM daily_stocks WHERE category!='RISE_TOP_30'").fetchone()[0], 3)


if __name__ == "__main__":
    unittest.main()
