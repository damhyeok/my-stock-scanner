import sqlite3
import tempfile
import unittest
from datetime import timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from program_net_buy_scanner import ProgramNetBuyScanner


class FakeCrawler:
    def __init__(self, db_path):
        self.db_path = db_path
        self.target_date = "20260805"
        self.kst = timezone(timedelta(hours=9))
        self.kis_base_url = "https://example.test"
        self.kis_app_key = "app-key"
        self.kis_app_secret = "app-secret"

    def _get_kis_access_token(self):
        return "token"

    def _get_session_name(self):
        return "장중(14:00)"


class ProgramNetBuyScannerTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = str(Path(self.temp_dir.name) / "test.db")
        self.scanner = ProgramNetBuyScanner(FakeCrawler(self.db_path))
        self.universe = pd.DataFrame(
            [
                {
                    "ticker": "005930",
                    "name": "삼성전자",
                    "close": 80000,
                    "fluctuation_rate": 1.2,
                    "market_cap": 480_000_000_000_000,
                    "trading_value": 200_000_000_000,
                    "sector": "반도체",
                },
                {
                    "ticker": "000660",
                    "name": "SK하이닉스",
                    "close": 210000,
                    "fluctuation_rate": -0.5,
                    "market_cap": 150_000_000_000_000,
                    "trading_value": 100_000_000_000,
                    "sector": "반도체",
                },
            ]
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_saves_only_positive_net_buy_and_latest_time(self):
        def response_for_ticker(ticker, _token):
            if ticker == "005930":
                return self.scanner._latest_row([
                    {
                        "bsop_hour": "093000",
                        "stck_prpr": "79500",
                        "prdy_ctrt": "0.50",
                        "whol_smtn_ntby_tr_pbmn": "1,000,000,000",
                    },
                    {
                        "bsop_hour": "140000",
                        "stck_prpr": "80500",
                        "prdy_ctrt": "1.25",
                        "whol_smtn_ntby_tr_pbmn": "5,000,000,000",
                    },
                ])
            return self.scanner._latest_row([
                    {
                        "bsop_hour": "140000",
                        "stck_prpr": "209000",
                        "prdy_ctrt": "-0.90",
                        "whol_smtn_ntby_tr_pbmn": "-100000000",
                    }
                ])

        with patch.object(self.scanner, "_request_ticker", side_effect=response_for_ticker):
            result = self.scanner.run(self.universe)

        self.assertEqual(result["ticker"].tolist(), ["005930"])
        self.assertEqual(int(result.iloc[0]["program_net_buy"]), 5_000_000_000)
        self.assertEqual(result.iloc[0]["snapshot_time"], "14:00:00")
        self.assertEqual(float(result.iloc[0]["program_net_ratio"]), 2.5)

        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT ticker, program_net_buy FROM stock_program_net_snapshots"
            ).fetchall()
            run = conn.execute(
                "SELECT status, universe_count, queried_count, positive_count, failure_count "
                "FROM stock_program_net_runs"
            ).fetchone()
        self.assertEqual(rows, [("005930", 5_000_000_000)])
        self.assertEqual(run, ("success", 2, 2, 1, 0))

    def test_records_partial_status_without_discarding_good_rows(self):
        responses = [
            {
                "bsop_hour": "095000",
                "stck_prpr": "80000",
                "prdy_ctrt": "1.00",
                "whol_smtn_ntby_tr_pbmn": "100000000",
            },
            RuntimeError("rate limit"),
        ]

        with patch.object(self.scanner, "_request_ticker", side_effect=responses):
            self.scanner.run(self.universe)

        with sqlite3.connect(self.db_path) as conn:
            run = conn.execute(
                "SELECT status, queried_count, positive_count, failure_count "
                "FROM stock_program_net_runs"
            ).fetchone()
        self.assertEqual(run, ("partial", 1, 1, 1))


if __name__ == "__main__":
    unittest.main()
