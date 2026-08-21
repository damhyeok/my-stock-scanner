import unittest
from unittest.mock import patch

from crawler import StockCrawler


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self.payload


class RiseRankCollectorTest(unittest.TestCase):
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
        rank_params = mock_get.call_args_list[0].kwargs["params"]
        self.assertEqual(rank_params["FID_COND_SCR_DIV_CODE"], "20170")
        self.assertEqual(rank_params["FID_TRGT_EXLS_CLS_CODE"], "0000001101")


if __name__ == "__main__":
    unittest.main()
