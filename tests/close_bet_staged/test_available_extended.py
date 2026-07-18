import unittest

import numpy as np
import pandas as pd

from close_bet_staged.features.available_extended import (
    build_daily_extended_features,
    build_sampled_afternoon_features,
)


class AvailableExtendedFeatureTests(unittest.TestCase):
    def _daily(self, periods=30):
        close = np.linspace(100, 115, periods)
        return pd.DataFrame({
            "date": pd.date_range("2025-01-01", periods=periods),
            "ticker": "000001",
            "open": close - 1,
            "high": close + 2,
            "low": close - 2,
            "close": close,
            "volume": np.arange(periods) + 100,
            "universe_type": "test",
        })

    def test_future_bar_does_not_change_prior_daily_features(self):
        full = self._daily()
        changed = full.copy()
        changed.loc[changed.index[-1], ["high", "low", "close", "volume"]] = [999, 1, 500, 9999]
        original_features = build_daily_extended_features(full)
        changed_features = build_daily_extended_features(changed)
        columns = [
            "stock_return_20d", "atr14_pct", "close_vs_swing_avwap_pct",
            "close_vs_volume_anchor_avwap_pct",
        ]
        pd.testing.assert_series_equal(
            original_features.iloc[-2][columns],
            changed_features.iloc[-2][columns],
            check_names=False,
        )

    def test_sampled_afternoon_features_use_only_stored_window(self):
        minute = pd.DataFrame({
            "timestamp": pd.to_datetime([
                "2025-01-02 14:30", "2025-01-02 15:00", "2025-01-02 15:30"
            ]),
            "ticker": ["000001"] * 3,
            "open": [100, 99, 100],
            "high": [101, 101, 103],
            "low": [99, 98, 100],
            "close": [99, 100, 102],
            "volume": [10, 20, 30],
        })
        daily = pd.DataFrame({
            "date": [pd.Timestamp("2025-01-02")],
            "ticker": ["000001"],
            "volume": [120],
            "close": [102],
        })
        row = build_sampled_afternoon_features(minute, daily).iloc[0]
        self.assertAlmostEqual(row["return_after_1430"], 0.02)
        self.assertAlmostEqual(row["sampled_afternoon_volume_ratio"], 0.5)
        self.assertEqual(row["sampled_new_low_count"], 1)
        self.assertAlmostEqual(row["sampled_afternoon_clv"], 0.8)


if __name__ == "__main__":
    unittest.main()
