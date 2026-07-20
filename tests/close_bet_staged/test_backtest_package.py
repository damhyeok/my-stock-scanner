"""Regression tests for the deployed backtest package surface."""

import importlib
import unittest


class BacktestPackageTests(unittest.TestCase):
    def test_pipeline_imports_with_only_deployed_backtest_modules(self) -> None:
        pipeline = importlib.import_module("close_bet_staged.pipeline")

        self.assertTrue(callable(pipeline.calculate_daily_outcomes))
