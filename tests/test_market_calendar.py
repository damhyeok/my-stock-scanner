import unittest
from datetime import date

from market_calendar import is_krx_closed


class MarketCalendarTests(unittest.TestCase):
    def test_chuseok_is_closed(self):
        self.assertTrue(is_krx_closed(date(2026, 9, 24)))
        self.assertTrue(is_krx_closed(date(2026, 9, 25)))

    def test_next_regular_session_is_open(self):
        self.assertFalse(is_krx_closed(date(2026, 9, 23)))
        self.assertFalse(is_krx_closed(date(2026, 9, 28)))

    def test_later_closures(self):
        for month, day in ((10, 5), (10, 9), (12, 25), (12, 31)):
            self.assertTrue(is_krx_closed(date(2026, month, day)))

    def test_weekend(self):
        self.assertTrue(is_krx_closed(date(2026, 9, 26)))


if __name__ == "__main__":
    unittest.main()
