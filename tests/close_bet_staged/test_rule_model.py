import unittest

import numpy as np
import pandas as pd

from close_bet_staged.rule_model import evaluate_rule_model, load_rule_config


class RuleModelTests(unittest.TestCase):
    def _frame(self):
        rows = []
        for index in range(5):
            row = {
                "signal_date": pd.Timestamp("2026-01-02"),
                "ticker": f"{index:06d}",
                "sector": "테스트업종",
                "avg_trading_value_20d_prior": 100 + index * 10,
                "trading_value_ratio_20d_prior": 0.5 + index * 0.2,
                "volume_ratio_20d_prior": 1.0,
                "higher_low_pct": np.nan,
                "low_vs_support_pct": np.nan,
                "close_vs_support_pct": np.nan,
                "clv": 0.3,
                "upper_wick_ratio": 0.5,
                "lower_wick_ratio": 0.1,
                "close_vs_swing_avwap_pct": -0.01,
                "close_vs_volume_anchor_avwap_pct": -0.01,
                "close_vs_sampled_poc_pct": -0.01,
                "return_after_1430": -0.01,
                "sampled_new_low_count": 2,
                "sampled_up_down_volume_ratio": 0.5,
                "sampled_afternoon_clv": 0.3,
                "stock_return_5d": index * 0.01,
                "stock_return_20d": index * 0.02,
                "atr14_pct": [0.01, 0.02, 0.03, 0.04, 0.09][index],
            }
            for window in (5, 10, 20, 60):
                row[f"breakout_pct_{window}"] = -0.01
            rows.append(row)
        candidate = rows[3]
        candidate.update({
            "clv": 0.8,
            "upper_wick_ratio": 0.1,
            "breakout_pct_20": 0.01,
            "close_vs_swing_avwap_pct": 0.02,
            "close_vs_volume_anchor_avwap_pct": 0.01,
            "close_vs_sampled_poc_pct": 0.01,
            "return_after_1430": 0.01,
            "sampled_new_low_count": 0,
            "sampled_up_down_volume_ratio": 1.5,
            "sampled_afternoon_clv": 0.8,
        })
        return pd.DataFrame(rows)

    def test_manual_market_gate_is_separate_from_technical_pass(self):
        config = load_rule_config("close_bet_staged/configs/rule_model.json")
        pending = evaluate_rule_model(self._frame(), config, manual_market_pass=None)
        candidate = pending.loc[pending["ticker"].eq("000003")].iloc[0]
        self.assertTrue(candidate["technical_pass"])
        self.assertFalse(candidate["final_pass"])
        self.assertEqual(candidate["decision"], "기술조건 통과·시장판단 대기")

        approved = evaluate_rule_model(self._frame(), config, manual_market_pass=True)
        self.assertTrue(approved.loc[approved["ticker"].eq("000003"), "final_pass"].iloc[0])

    def test_market_block_never_changes_stage_results(self):
        config = load_rule_config("close_bet_staged/configs/rule_model.json")
        blocked = evaluate_rule_model(self._frame(), config, manual_market_pass=False)
        candidate = blocked.loc[blocked["ticker"].eq("000003")].iloc[0]
        self.assertTrue(candidate["technical_pass"])
        self.assertFalse(candidate["final_pass"])
        self.assertEqual(candidate["decision"], "기술조건 통과·시장 차단")


if __name__ == "__main__":
    unittest.main()
