import unittest
from datetime import datetime, timedelta

from market_betting_engine.contracts import StockState
from market_betting_engine.features import NormalizedBar, derive_bar_features
from market_betting_engine.session import KST
from market_betting_engine.setups import EntrySetupType, assess_entry_setup


START = datetime(2026, 8, 3, 9, 0, tzinfo=KST)


def breakout_bars(last_close):
    bars = []
    for index in range(20):
        close = 94 + index * 0.25
        bars.append(
            NormalizedBar(
                START + timedelta(minutes=index),
                close - 0.2,
                100 if index == 19 else close + 0.4,
                90 if index == 0 else close - 0.5,
                close,
                100,
            )
        )
    bars.append(
        NormalizedBar(START + timedelta(minutes=20), last_close - 0.3, last_close + 0.4, last_close - 0.6, last_close, 300)
    )
    return tuple(bars)


def pullback_bars():
    bars = []
    for index in range(20):
        close = 100 + index * 0.45
        bars.append(
            NormalizedBar(
                START + timedelta(minutes=index), close - 0.2,
                110 if index == 19 else close + 0.5, close - 0.6, close, 100,
            )
        )
    bars.extend(
        (
            NormalizedBar(START + timedelta(minutes=20), 105, 106, 103, 104, 150),
            NormalizedBar(START + timedelta(minutes=21), 104, 105, 103.8, 104.5, 200),
        )
    )
    return tuple(bars)


class StructuralSetupTests(unittest.TestCase):
    def assess(self, bars):
        features = derive_bar_features(bars, "stock.TEST")
        return assess_entry_setup(bars, features)

    def test_breakout_trigger_has_failed_breakout_invalidation(self):
        result = self.assess(breakout_bars(100.2))
        self.assertEqual(result.setup_type, EntrySetupType.BREAKOUT)
        self.assertEqual(result.state_hint, StockState.TRIGGERED)
        self.assertLess(result.invalidation_price, result.entry_reference)
        self.assertGreaterEqual(result.reward_risk_ratio, 1.5)

    def test_near_resistance_waits_in_setup(self):
        result = self.assess(breakout_bars(99.8))
        self.assertEqual(result.setup_type, EntrySetupType.BREAKOUT)
        self.assertEqual(result.state_hint, StockState.SETUP)

    def test_far_above_breakout_with_bad_risk_reward_is_extended(self):
        result = self.assess(breakout_bars(108.0))
        self.assertEqual(result.state_hint, StockState.EXTENDED)
        self.assertIn("RISK_REWARD_EXTENDED", result.reasons)

    def test_pullback_rebound_uses_structure_low_as_invalidation(self):
        bars = pullback_bars()
        result = self.assess(bars)
        self.assertEqual(result.setup_type, EntrySetupType.PULLBACK)
        self.assertEqual(result.state_hint, StockState.TRIGGERED)
        self.assertLess(result.invalidation_price, min(bar.low for bar in bars[-8:]))
        self.assertEqual(result.reward_reference, 110)

    def test_insufficient_bars_never_invents_stop(self):
        bars = breakout_bars(100.2)[:10]
        result = self.assess(bars)
        self.assertFalse(result.evaluable)
        self.assertIsNone(result.invalidation_price)
        self.assertEqual(result.state_hint, StockState.WATCH)


if __name__ == "__main__":
    unittest.main()
