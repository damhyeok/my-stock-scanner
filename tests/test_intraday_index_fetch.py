import sqlite3
import tempfile
from pathlib import Path
from unittest import TestCase

from intraday_relative_strength import (
    IntradayRelativeStrengthScanner,
    _parse_intraday_time,
    expected_regular_bars,
)


class IntradayIndexFetchTests(TestCase):
    def test_kis_millisecond_time_is_normalized(self):
        self.assertEqual(_parse_intraday_time("153000999").strftime("%H:%M:%S"), "15:30:00")
        self.assertEqual(_parse_intraday_time("15:30:00.999").strftime("%H:%M:%S"), "15:30:00")

    def test_expected_regular_bar_counts_include_both_endpoints(self):
        self.assertEqual(expected_regular_bars("09:30"), 31)
        self.assertEqual(expected_regular_bars("15:30"), 391)

    def test_index_api_uses_sixty_second_interval_and_continuation(self):
        with tempfile.TemporaryDirectory() as directory:
            scanner = IntradayRelativeStrengthScanner.__new__(
                IntradayRelativeStrengthScanner
            )
            scanner.db_path = str(Path(directory) / "intraday.db")
            scanner.index_url = "https://example.test/index"
            scanner._init_db()
            calls = []
            pages = [
                {
                    "rt_cd": "0",
                    "output1": {"prdy_nmix": "1000"},
                    "output2": [
                        {
                            "stck_bsop_date": "20260721",
                            "stck_cntg_hour": "153000",
                            "bstp_nmix_prpr": "1010",
                        },
                        {
                            "stck_bsop_date": "20260721",
                            "stck_cntg_hour": "152900",
                            "bstp_nmix_prpr": "1009",
                        },
                    ],
                    "_response_tr_cont": "M",
                },
                {
                    "rt_cd": "0",
                    "output1": {"prdy_nmix": "1000"},
                    "output2": [
                        {
                            "stck_bsop_date": "20260721",
                            "stck_cntg_hour": "090100",
                            "bstp_nmix_prpr": "1001",
                        },
                        {
                            "stck_bsop_date": "20260721",
                            "stck_cntg_hour": "090000",
                            "bstp_nmix_prpr": "1000",
                        },
                    ],
                    "_response_tr_cont": "",
                },
            ]

            def fake_request(url, tr_id, params, tr_cont=""):
                calls.append((params.copy(), tr_cont))
                return pages[len(calls) - 1]

            scanner._request = fake_request
            saved = scanner._fetch_index_delta(
                "20260721", "KOSPI", "09:00", "15:30"
            )

            self.assertEqual(saved, 4)
            self.assertEqual(
                [call[0]["FID_INPUT_HOUR_1"] for call in calls],
                ["60", "60"],
            )
            self.assertEqual([call[1] for call in calls], ["", "N"])
            with sqlite3.connect(scanner.db_path) as conn:
                rows = conn.execute(
                    "SELECT bar_time, close FROM intraday_index_bars ORDER BY bar_time"
                ).fetchall()
            self.assertEqual(
                rows,
                [("09:00", 1000.0), ("09:01", 1001.0), ("15:29", 1009.0), ("15:30", 1010.0)],
            )
