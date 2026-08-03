import unittest
from datetime import date, datetime, timedelta, timezone

from market_betting_engine.contracts import (
    AxisSignal,
    AxisStatus,
    CalculationMode,
    DataQualityReport,
    Observation,
    ObservationMeta,
    QualityIssue,
    QualitySeverity,
    VerificationStatus,
    MarketPermission,
    StockState,
)
from market_betting_engine.metrics import (
    PriceBar,
    activity_rate_change,
    close_location_value,
    clv_weighted_turnover_proxy,
    ordinary_least_squares_slope,
    relative_return,
    volume_weighted_average_price,
)
from market_betting_engine.engines import (
    OvernightGateConfig,
    SectorCoverage,
    SectorGateConfig,
    assess_overnight_permissions,
    assess_sector_state,
)
from market_betting_engine.quality import combine_quality, evaluate_observations
from market_betting_engine.states import (
    MarketGateConfig,
    StockGateSignals,
    assess_market_permission,
    resolve_stock_state,
    validate_stock_transition,
)


UTC = timezone.utc
TRADE_DATE = date(2026, 7, 31)
NOW = datetime(2026, 7, 31, 6, 0, tzinfo=UTC)


def observation(
    metric="price",
    *,
    source="KIS",
    trade_date=TRADE_DATE,
    observed_at=NOW,
    status=VerificationStatus.VERIFIED,
    stale_after=30,
    value=1.0,
):
    return Observation(
        metric,
        value,
        ObservationMeta(
            source=source,
            observed_at=observed_at,
            source_trade_date=trade_date,
            unit="KRW",
            semantics_status=status,
            stale_after_seconds=stale_after,
        ),
    )


def axis(name, status):
    return AxisSignal(name, status, f"{name}_{status.value}", f"{name}: {status.value}")


class MetricTests(unittest.TestCase):
    def test_clv_boundaries_and_midpoint(self):
        self.assertEqual(close_location_value(110, 90, 110).value, 1.0)
        self.assertEqual(close_location_value(110, 90, 90).value, -1.0)
        self.assertEqual(close_location_value(110, 90, 100).value, 0.0)

    def test_flat_bar_is_unavailable_not_neutral(self):
        result = close_location_value(100, 100, 100)
        self.assertFalse(result.available)
        self.assertIsNone(result.value)
        self.assertIn("FLAT_BAR", result.flags)

    def test_invalid_ohlc_is_rejected(self):
        self.assertFalse(close_location_value(100, 90, 110).available)

    def test_vwap_and_zero_volume(self):
        result = volume_weighted_average_price([100, 110], [1, 3])
        self.assertAlmostEqual(result.value, 107.5)
        self.assertFalse(volume_weighted_average_price([100], [0]).available)

    def test_clv_turnover_is_explicit_proxy(self):
        result = clv_weighted_turnover_proxy(
            [PriceBar(110, 90, 110, 10), PriceBar(110, 90, 90, 10)]
        )
        self.assertTrue(result.available)
        self.assertEqual(result.calculation_mode, CalculationMode.PROXY)
        self.assertEqual(result.value, 200.0)
        self.assertAlmostEqual(result.components["proxy_ratio"], 0.1)

    def test_flat_bar_turnover_is_excluded_and_disclosed(self):
        result = clv_weighted_turnover_proxy(
            [PriceBar(100, 100, 100, 10), PriceBar(110, 90, 110, 10)]
        )
        self.assertTrue(result.available)
        self.assertIn("FLAT_BARS_EXCLUDED", result.flags)
        self.assertEqual(result.components["flat_bar_count"], 1.0)

    def test_relative_return_and_slope(self):
        self.assertAlmostEqual(relative_return(0.03, 0.01).value, 0.02)
        slope = ordinary_least_squares_slope([(0, 10), (1, 12), (2, 14)], "KRW/min")
        self.assertAlmostEqual(slope.value, 2.0)
        self.assertEqual(slope.unit, "KRW/min")

    def test_activity_rate_compares_unequal_windows(self):
        result = activity_rate_change(200, 10, 100, 10)
        self.assertEqual(result.calculation_mode, CalculationMode.ACTIVITY)
        self.assertAlmostEqual(result.value, 1.0)
        self.assertFalse(activity_rate_change(100, 10, 0, 10).available)


class QualityTests(unittest.TestCase):
    def test_fresh_verified_observation_passes(self):
        report = evaluate_observations([observation()], target_trade_date=TRADE_DATE, now=NOW)
        self.assertFalse(report.blocking)
        self.assertEqual(report.issues, ())

    def test_stale_and_trade_date_mismatch_block(self):
        item = observation(
            trade_date=date(2026, 7, 30),
            observed_at=NOW - timedelta(minutes=1),
        )
        report = evaluate_observations([item], target_trade_date=TRADE_DATE, now=NOW)
        self.assertTrue(report.blocking)
        self.assertIn("SOURCE_TRADE_DATE_MISMATCH", report.codes())
        self.assertIn("OBSERVATION_STALE", report.codes())

    def test_cross_source_date_conflict_is_detected(self):
        report = evaluate_observations(
            [
                observation("stock", source="KIS"),
                observation("future", source="KIWOOM", trade_date=date(2026, 7, 30)),
            ],
            target_trade_date=None,
            now=NOW,
        )
        self.assertIn("CROSS_SOURCE_TRADE_DATE_CONFLICT", report.codes())
        self.assertTrue(report.blocking)

    def test_unverified_semantics_warns_but_does_not_block(self):
        report = evaluate_observations(
            [observation(status=VerificationStatus.UNVERIFIED)],
            target_trade_date=TRADE_DATE,
            now=NOW,
        )
        self.assertFalse(report.blocking)
        self.assertIn("FIELD_SEMANTICS_UNVERIFIED", report.codes())

    def test_missing_expected_metric_blocks_and_counts_completeness(self):
        report = evaluate_observations(
            [observation("price")],
            target_trade_date=TRADE_DATE,
            now=NOW,
            expected_metrics={"price", "volume"},
        )
        self.assertTrue(report.blocking)
        self.assertEqual(report.completeness_ratio, 0.5)

    def test_combine_quality_deduplicates(self):
        issue = QualityIssue("X", QualitySeverity.WARNING, "x")
        report = combine_quality(DataQualityReport((issue,), 1, 1), DataQualityReport((issue,), 1, 1))
        self.assertEqual(len(report.issues), 1)
        self.assertEqual(report.observed_count, 2)


class MarketGateTests(unittest.TestCase):
    def test_all_available_axes_pass_allows(self):
        result = assess_market_permission(
            [axis("price", AxisStatus.PASS), axis("flow", AxisStatus.PASS), axis("breadth", AxisStatus.PASS)]
        )
        self.assertEqual(result.decision, MarketPermission.ALLOW.value)

    def test_one_independent_failure_is_selective(self):
        result = assess_market_permission(
            [axis("price", AxisStatus.FAIL), axis("flow", AxisStatus.PASS), axis("breadth", AxisStatus.PASS)]
        )
        self.assertEqual(result.decision, MarketPermission.SELECTIVE.value)

    def test_two_independent_failures_block(self):
        result = assess_market_permission(
            [axis("price", AxisStatus.FAIL), axis("flow", AxisStatus.FAIL), axis("breadth", AxisStatus.PASS)]
        )
        self.assertEqual(result.decision, MarketPermission.BLOCK.value)

    def test_repeated_failure_same_axis_is_not_two_axis_veto(self):
        result = assess_market_permission(
            [
                axis("flow", AxisStatus.FAIL), axis("flow", AxisStatus.FAIL),
                axis("price", AxisStatus.PASS), axis("breadth", AxisStatus.PASS),
            ]
        )
        self.assertEqual(result.decision, MarketPermission.SELECTIVE.value)

    def test_multiple_signals_from_same_axis_do_not_fake_coverage(self):
        result = assess_market_permission(
            [axis("price", AxisStatus.PASS), axis("price", AxisStatus.PASS), axis("price", AxisStatus.PASS)]
        )
        self.assertEqual(result.decision, MarketPermission.NOT_EVALUABLE.value)

    def test_insufficient_data_is_not_evaluable_not_bearish(self):
        result = assess_market_permission(
            [axis("price", AxisStatus.PASS), axis("flow", AxisStatus.UNAVAILABLE)]
        )
        self.assertEqual(result.decision, MarketPermission.NOT_EVALUABLE.value)

    def test_two_verified_axes_with_one_unavailable_is_selective(self):
        result = assess_market_permission(
            [
                axis("price", AxisStatus.PASS),
                axis("futures", AxisStatus.PASS),
                axis("flow", AxisStatus.UNAVAILABLE),
            ]
        )
        self.assertEqual(result.decision, MarketPermission.SELECTIVE.value)

    def test_blocking_quality_prevents_evaluation(self):
        quality = DataQualityReport(
            (QualityIssue("STALE", QualitySeverity.BLOCKING, "stale"),), 3, 3
        )
        result = assess_market_permission(
            [axis("price", AxisStatus.PASS), axis("flow", AxisStatus.PASS), axis("breadth", AxisStatus.PASS)],
            quality,
        )
        self.assertEqual(result.decision, MarketPermission.NOT_EVALUABLE.value)


class StockStateTests(unittest.TestCase):
    def test_recovered_data_can_move_not_evaluable_to_extended(self):
        result = resolve_stock_state(
            StockState.NOT_EVALUABLE,
            StockGateSignals(True, True, extended_risk_reward=True),
        )
        self.assertTrue(result.allowed)
        self.assertEqual(result.current, StockState.EXTENDED)

    def test_setup_does_not_trigger_without_structural_invalidation(self):
        result = resolve_stock_state(
            StockState.SETUP,
            StockGateSignals(True, True, setup_ready=True, trigger_confirmed=True),
        )
        self.assertEqual(result.current, StockState.SETUP)

    def test_all_trigger_gates_are_required(self):
        result = resolve_stock_state(
            StockState.SETUP,
            StockGateSignals(
                True,
                True,
                setup_ready=True,
                trigger_confirmed=True,
                structural_invalidation_price_defined=True,
            ),
        )
        self.assertEqual(result.current, StockState.TRIGGERED)

    def test_extended_prevents_chase_before_trigger(self):
        result = resolve_stock_state(
            StockState.WATCH,
            StockGateSignals(True, True, setup_ready=True, extended_risk_reward=True),
        )
        self.assertEqual(result.current, StockState.EXTENDED)

    def test_invalidation_has_precedence(self):
        result = resolve_stock_state(
            StockState.TRIGGERED,
            StockGateSignals(
                True,
                True,
                setup_ready=True,
                trigger_confirmed=True,
                structural_invalidation_price_defined=True,
                reaction_failed=True,
                thesis_invalidated=True,
            ),
        )
        self.assertEqual(result.current, StockState.INVALIDATED)

    def test_invalidated_is_latched_until_explicit_rearm(self):
        signals = StockGateSignals(True, True)
        held = resolve_stock_state(StockState.INVALIDATED, signals)
        rearmed = resolve_stock_state(StockState.INVALIDATED, signals, rearm=True)
        self.assertEqual(held.current, StockState.INVALIDATED)
        self.assertEqual(rearmed.current, StockState.WATCH)
        self.assertTrue(validate_stock_transition(StockState.INVALIDATED, StockState.WATCH, rearm=True))

    def test_post_trigger_failed_state(self):
        result = resolve_stock_state(
            StockState.TRIGGERED,
            StockGateSignals(True, True, reaction_failed=True),
        )
        self.assertEqual(result.current, StockState.FAILED)


class SectorAndOvernightTests(unittest.TestCase):
    def test_sector_low_coverage_is_not_evaluable(self):
        result = assess_sector_state(
            [axis("relative_strength", AxisStatus.PASS)],
            SectorCoverage(20, 2, 1_000, 200, 100),
            persistence_confirmed=False,
        )
        self.assertEqual(result.decision, "NOT_EVALUABLE")
        self.assertIn("PLACEHOLDER", result.confidence_label)

    def test_sector_single_name_dominance_is_not_called_leading(self):
        result = assess_sector_state(
            [axis("relative_strength", AxisStatus.PASS), axis("activity", AxisStatus.PASS)],
            SectorCoverage(20, 10, 1_000, 800, 500),
            persistence_confirmed=True,
        )
        self.assertEqual(result.decision, "NEUTRAL")
        self.assertTrue(any(item.code == "SECTOR_SINGLE_NAME_CONCENTRATION" for item in result.warnings))

    def test_sector_emerging_and_leading_are_separate(self):
        signals = [axis("relative_strength", AxisStatus.PASS), axis("activity", AxisStatus.PASS)]
        coverage = SectorCoverage(20, 10, 1_000, 800, 300)
        emerging = assess_sector_state(signals, coverage, persistence_confirmed=False)
        leading = assess_sector_state(signals, coverage, persistence_confirmed=True)
        self.assertEqual(emerging.decision, "EMERGING")
        self.assertEqual(leading.decision, "LEADING")

    def test_close_new_entry_and_existing_hold_are_separate(self):
        result = assess_overnight_permissions(
            [axis("price", AxisStatus.FAIL), axis("flow", AxisStatus.PASS), axis("futures", AxisStatus.PASS)],
            existing_thesis_valid=True,
        )
        self.assertEqual(result.close_new_entry.decision, "SELECTIVE")
        self.assertEqual(result.hold_existing.decision, "REDUCE")

    def test_invalid_existing_thesis_exits_but_does_not_rewrite_new_entry_logic(self):
        result = assess_overnight_permissions(
            [axis("price", AxisStatus.PASS), axis("flow", AxisStatus.PASS), axis("futures", AxisStatus.PASS)],
            existing_thesis_valid=False,
        )
        self.assertEqual(result.close_new_entry.decision, "ALLOWED")
        self.assertEqual(result.hold_existing.decision, "EXIT")

    def test_overnight_insufficient_data_is_not_evaluable(self):
        result = assess_overnight_permissions(
            [axis("price", AxisStatus.PASS), axis("flow", AxisStatus.UNAVAILABLE)],
            existing_thesis_valid=True,
        )
        self.assertEqual(result.close_new_entry.decision, "NOT_EVALUABLE")
        self.assertEqual(result.hold_existing.decision, "NOT_EVALUABLE")


if __name__ == "__main__":
    unittest.main()
