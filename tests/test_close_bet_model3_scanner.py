import unittest
from pathlib import Path
import numpy as np
import pandas as pd
from close_bet_model3_scanner import CloseBetModel3Scanner


class Model3SignalTests(unittest.TestCase):
    def test_signal_fields_and_normal_volume(self):
        close = pd.Series(np.linspace(100, 90, 70).tolist() + [89, 88, 89, 91, 94])
        result = CloseBetModel3Scanner._signal(pd.DataFrame({"stck_clpr": close, "acml_vol": 1000.0}))
        self.assertEqual(set(result), {"rsi_buy", "macd_buy", "rsi", "today_return", "previous_return", "volume_ratio", "ma20_change_5d"})
        self.assertAlmostEqual(result["volume_ratio"], 1.0)

    def test_volume_average_excludes_signal_day(self):
        frame = pd.DataFrame({"stck_clpr": np.linspace(100, 110, 65), "acml_vol": [100.0] * 64 + [150.0]})
        self.assertAlmostEqual(CloseBetModel3Scanner._signal(frame)["volume_ratio"], 1.5)

    def test_ma20_change_is_reported_without_being_a_required_filter(self):
        source = Path("close_bet_model3_scanner.py").read_text(encoding="utf-8")
        filter_block = source.split("if not (", 1)[1].split("):", 1)[0]
        self.assertNotIn('signal["ma20_change_5d"] <= 1', filter_block)


if __name__ == "__main__":
    unittest.main()
