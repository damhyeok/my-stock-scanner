import unittest

import pandas as pd

from program_net_divergence import build_program_price_divergence


class ProgramNetDivergenceTest(unittest.TestCase):
    def setUp(self):
        self.sessions = ["장중(09:50)", "장중(10:50)", "장중(11:50)"]
        self.runs = pd.DataFrame([
            {"trade_date": "20260806", "session": session, "status": "success"}
            for session in self.sessions
        ])

    def test_detects_consecutive_program_rise_and_return_fall(self):
        snapshots = pd.DataFrame([
            {
                "trade_date": "20260806", "session": session, "ticker": "005930",
                "name": "삼성전자", "sector": "반도체", "current_price": price,
                "fluctuation_rate": rate, "program_net_buy": program,
                "trading_value": 100_000_000_000, "collected_at_kst": collected,
            }
            for session, price, rate, program, collected in zip(
                self.sessions,
                [80000, 79800, 79500],
                [2.0, 1.4, 0.8],
                [1_000_000_000, 2_000_000_000, 3_500_000_000],
                ["2026-08-06 09:50:00", "2026-08-06 10:50:00", "2026-08-06 11:50:00"],
            )
        ])

        summary, history = build_program_price_divergence(
            snapshots, self.runs, "20260806", "장중(11:50)"
        )

        self.assertEqual(summary.iloc[0]["name"], "삼성전자")
        self.assertEqual(summary.iloc[0]["comparison_count"], 2)
        self.assertEqual(summary.iloc[0]["return_change"], -1.2)
        self.assertEqual(summary.iloc[0]["program_increase"], 2_500_000_000)
        self.assertEqual(summary.iloc[0]["session_flow"], "09:50 → 10:50 → 11:50")
        self.assertEqual(len(history), 3)

    def test_rejects_missing_middle_scan_or_non_decreasing_return(self):
        snapshots = pd.DataFrame([
            {
                "trade_date": "20260806", "session": "장중(09:50)", "ticker": "005930",
                "name": "삼성전자", "sector": "반도체", "current_price": 80000,
                "fluctuation_rate": 1.0, "program_net_buy": 1_000_000_000,
                "trading_value": 1, "collected_at_kst": "2026-08-06 09:50:00",
            },
            {
                "trade_date": "20260806", "session": "장중(11:50)", "ticker": "005930",
                "name": "삼성전자", "sector": "반도체", "current_price": 81000,
                "fluctuation_rate": 1.5, "program_net_buy": 3_000_000_000,
                "trading_value": 1, "collected_at_kst": "2026-08-06 11:50:00",
            },
        ])

        summary, history = build_program_price_divergence(
            snapshots, self.runs, "20260806", "장중(11:50)"
        )

        self.assertTrue(summary.empty)
        self.assertTrue(history.empty)


if __name__ == "__main__":
    unittest.main()
