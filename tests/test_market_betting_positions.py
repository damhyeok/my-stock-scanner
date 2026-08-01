import tempfile
import unittest
from pathlib import Path

from market_betting_engine.contracts import AxisSignal, AxisStatus, DataQualityReport
from market_betting_engine.positions import (
    Position,
    assess_position,
    list_positions,
    remove_position,
    upsert_position,
)


PASS_SIGNALS = tuple(
    AxisSignal(axis, AxisStatus.PASS, f"{axis.upper()}_PASS", "supported")
    for axis in ("price", "flow", "futures")
)


class PositionPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "positions.db"

    def tearDown(self):
        self.temp.cleanup()

    def test_upsert_and_remove_position(self):
        upsert_position(
            self.db_path,
            Position("005930", "삼성전자", 90000, 10, "ACTIVE", "장기 논리", 85000),
        )
        saved = list_positions(self.db_path)
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].ticker, "005930")
        self.assertEqual(saved[0].invalidation_price, 85000)
        self.assertTrue(remove_position(self.db_path, "005930"))
        self.assertEqual(list_positions(self.db_path), [])

    def test_upsert_rejects_invalid_numbers(self):
        with self.assertRaises(ValueError):
            upsert_position(self.db_path, Position("005930", "삼성전자", 0, 10))


class PositionAssessmentTests(unittest.TestCase):
    def test_active_thesis_and_good_market_hold(self):
        result = assess_position(
            Position("005930", "삼성전자", 90000, 10, "ACTIVE", "", 85000),
            95000,
            PASS_SIGNALS,
            DataQualityReport(),
        )
        self.assertEqual(result.decision, "HOLD")
        self.assertAlmostEqual(result.profit_loss_ratio, 95000 / 90000 - 1)

    def test_invalidation_breach_exits_even_with_profit_cushion(self):
        result = assess_position(
            Position("005930", "삼성전자", 50000, 10, "ACTIVE", "", 85000),
            84000,
            PASS_SIGNALS,
            DataQualityReport(),
        )
        self.assertEqual(result.decision, "EXIT")
        self.assertIn("POSITION_INVALIDATION_PRICE_BREACHED", result.reasons)

    def test_unspecified_thesis_is_not_evaluable(self):
        result = assess_position(
            Position("005930", "삼성전자", 90000, 10, "UNSPECIFIED"),
            95000,
            PASS_SIGNALS,
            DataQualityReport(),
        )
        self.assertEqual(result.decision, "NOT_EVALUABLE")


if __name__ == "__main__":
    unittest.main()
