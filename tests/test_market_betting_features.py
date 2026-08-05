import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
import tempfile

from market_betting_engine.adapters import adapt_probe_payload
from market_betting_engine.contracts import AxisStatus, VerificationStatus
from market_betting_engine.features import (
    FeatureConfig,
    NormalizedBar,
    derive_bar_features,
    derive_closing_window_features,
    derive_relative_features,
    extract_bar_series,
)
from market_betting_engine.session import KST, SessionContext
from market_betting_engine.signals import (
    SignalThresholds,
    aggregate_sector_features,
    build_market_axis_signals,
    build_program_actual_flow_signal,
    build_sector_axis_signals,
    build_stock_axis_signals,
)
from market_betting_engine.pipeline import derive_evidence_bundle
from market_betting_engine.config import load_analysis_config
from market_betting_engine.states import assess_market_permission


TARGET = date(2026, 7, 31)
NOW = datetime(2026, 7, 31, 15, 30, tzinfo=KST)
CONTEXT = SessionContext(TARGET, NOW, True, "TEST")


def rising_bars(prefix_start=100.0, volume_multiplier=1.0):
    start = datetime(2026, 7, 31, 9, 0, tzinfo=KST)
    bars = []
    for index in range(10):
        close = prefix_start + index
        volume = (100 if index < 5 else 300) * volume_multiplier
        bars.append(NormalizedBar(start + timedelta(minutes=index), close - 1, close + 1, close - 2, close, volume))
    return tuple(bars)


def falling_bars(prefix_start=120.0):
    start = datetime(2026, 7, 31, 9, 0, tzinfo=KST)
    bars = []
    for index in range(10):
        close = prefix_start - index
        bars.append(NormalizedBar(start + timedelta(minutes=index), close + 1, close + 2, close - 1, close, 100 if index < 5 else 300))
    return tuple(bars)


class FeaturePipelineTests(unittest.TestCase):
    def test_adapter_observations_reconstruct_complete_bar(self):
        payload = {"output2": [{
            "stck_bsop_date": "20260731", "stck_cntg_hour": "093000",
            "stck_oprc": "100", "stck_hgpr": "110", "stck_lwpr": "90",
            "stck_prpr": "105", "cntg_vol": "10", "acml_tr_pbmn": "1000",
        }]}
        adapted = adapt_probe_payload(
            "kis_stock_minute", payload, context=CONTEXT, instrument="005930",
            verification_status=VerificationStatus.VERIFIED,
        )
        bars = extract_bar_series(adapted.observations, "stock.005930")
        self.assertEqual(len(bars), 1)
        self.assertEqual((bars[0].open, bars[0].high, bars[0].low, bars[0].close, bars[0].volume), (100, 110, 90, 105, 10))

    def test_bar_features_calculate_vwap_clv_return_and_activity(self):
        features = derive_bar_features(rising_bars(), "stock.005930")
        self.assertIsNotNone(features)
        self.assertEqual(features.bar_count, 10)
        self.assertTrue(features.session_vwap.available)
        self.assertGreater(features.vwap_distance_ratio.value, 0)
        self.assertGreater(features.latest_clv.value, 0)
        self.assertGreater(features.short_return.value, 0)
        self.assertGreater(features.activity_acceleration.value, 0)
        self.assertIn("PLACEHOLDER_CONFIG", features.flags)

    def test_zero_volume_does_not_become_neutral_vwap(self):
        bars = tuple(
            NormalizedBar(bar.timestamp, bar.open, bar.high, bar.low, bar.close, 0)
            for bar in rising_bars()
        )
        features = derive_bar_features(bars, "stock.005930")
        self.assertFalse(features.session_vwap.available)
        self.assertFalse(features.vwap_distance_ratio.available)

    def test_relative_features_use_same_configured_horizon(self):
        asset = derive_bar_features(rising_bars(100), "stock.A")
        benchmark = derive_bar_features(rising_bars(200), "index.KOSPI")
        relative = derive_relative_features(asset, benchmark)
        expected = asset.short_return.value - benchmark.short_return.value
        self.assertAlmostEqual(relative.relative_short_return.value, expected)

    def test_closing_windows_are_not_mixed(self):
        times = [(14, 30), (14, 59), (15, 0), (15, 19), (15, 20), (15, 29), (15, 30)]
        bars = tuple(
            NormalizedBar(datetime(2026, 7, 31, hour, minute, tzinfo=KST), 100, 102, 99, 101, 10)
            for hour, minute in times
        )
        result = derive_closing_window_features(bars, "stock.005930", FeatureConfig(short_return_bars=1, activity_window_bars=1))
        self.assertEqual(result.pre_close_flow.bar_count, 2)
        self.assertEqual(result.close_continuity.bar_count, 2)
        self.assertEqual(result.closing_auction.bar_count, 3)
        self.assertEqual(result.closing_auction.as_of.time().replace(tzinfo=None), datetime.strptime("153000", "%H%M%S").time())

    def test_cash_market_activity_excludes_closing_call_auction_gap(self):
        bars = []
        for minute in range(10, 20):
            volume = 10 if minute < 15 else 20
            bars.append(
                NormalizedBar(
                    datetime(2026, 7, 31, 15, minute, tzinfo=KST),
                    100, 102, 99, 101, volume,
                )
            )
        for minute in range(20, 30):
            bars.append(
                NormalizedBar(
                    datetime(2026, 7, 31, 15, minute, tzinfo=KST),
                    100, 102, 99, 101, 0,
                )
            )
        bars.append(
            NormalizedBar(datetime(2026, 7, 31, 15, 30, tzinfo=KST), 100, 102, 99, 101, 1000)
        )

        features = derive_bar_features(
            tuple(bars),
            "stock.005930",
            FeatureConfig(activity_window_bars=5),
        )

        self.assertTrue(features.activity_acceleration.available)
        self.assertAlmostEqual(features.activity_acceleration.value, 1.0)
        self.assertIn("CLOSING_AUCTION_EXCLUDED_FROM_ACTIVITY", features.activity_acceleration.flags)


class SignalFactoryTests(unittest.TestCase):
    def setUp(self):
        self.asset = derive_bar_features(rising_bars(100), "stock.A")
        self.benchmark = derive_bar_features(rising_bars(200), "index.KOSPI")
        self.relative = derive_relative_features(self.asset, self.benchmark)

    def test_stock_signals_keep_vwap_and_clv_on_same_axis(self):
        signals = build_stock_axis_signals(self.relative, SignalThresholds(positive_relative_return=-1))
        price_signals = [item for item in signals if item.axis == "price_action"]
        self.assertEqual(len(price_signals), 2)
        self.assertTrue(any("not actual net flow" in item.message for item in price_signals))
        self.assertEqual(len({item.axis for item in signals}), 3)

    def test_rising_activity_on_falling_price_is_counter_evidence(self):
        asset = derive_bar_features(falling_bars(), "stock.FALL")
        relative = derive_relative_features(asset, self.benchmark)
        signals = build_stock_axis_signals(relative)
        activity = next(item for item in signals if item.axis == "activity")
        self.assertEqual(activity.status, AxisStatus.FAIL)
        self.assertEqual(activity.reason_code, "RISING_ACTIVITY_CONFIRMS_DECLINE")

    def test_program_flow_requires_actual_provider_series(self):
        unavailable = build_program_actual_flow_signal([])
        self.assertEqual(unavailable.status, AxisStatus.UNAVAILABLE)
        payload = {"output": [
            {"bsop_hour": "145900", "whol_smtn_ntby_tr_pbmn": "10", "arbt_smtn_ntby_tr_pbmn": "0", "nabt_smtn_ntby_tr_pbmn": "10"},
            {"bsop_hour": "150000", "whol_smtn_ntby_tr_pbmn": "20", "arbt_smtn_ntby_tr_pbmn": "0", "nabt_smtn_ntby_tr_pbmn": "20"},
        ]}
        adapted = adapt_probe_payload(
            "kis_program_summary_kospi", payload, context=CONTEXT, instrument="KOSPI",
            verification_status=VerificationStatus.VERIFIED,
        )
        actual = build_program_actual_flow_signal(adapted.observations)
        self.assertEqual(actual.axis, "actual_flow")
        self.assertEqual(actual.status, AxisStatus.PASS)

    def test_market_signals_cover_three_independent_axes(self):
        payload = {"output": [
            {"bsop_hour": "145900", "whol_smtn_ntby_tr_pbmn": "10", "arbt_smtn_ntby_tr_pbmn": "0", "nabt_smtn_ntby_tr_pbmn": "10"},
            {"bsop_hour": "150000", "whol_smtn_ntby_tr_pbmn": "20", "arbt_smtn_ntby_tr_pbmn": "0", "nabt_smtn_ntby_tr_pbmn": "20"},
        ]}
        adapted = adapt_probe_payload(
            "kis_program_summary_kospi", payload, context=CONTEXT, instrument="KOSPI",
            verification_status=VerificationStatus.VERIFIED,
        )
        futures = derive_bar_features(rising_bars(300), "futures.ACTIVE")
        signals = build_market_axis_signals(self.benchmark, adapted.observations, futures)
        self.assertEqual({item.axis for item in signals}, {"price_action", "actual_flow", "futures"})
        judgment = assess_market_permission(signals)
        self.assertEqual(judgment.decision, "ALLOW")

    def test_sector_summary_is_equal_weighted_not_leader_weighted(self):
        weak = derive_relative_features(derive_bar_features(falling_bars(), "stock.W"), self.benchmark)
        summary = aggregate_sector_features([self.relative, self.relative, weak])
        self.assertEqual(summary.member_count, 3)
        self.assertAlmostEqual(summary.above_vwap_ratio, 2 / 3)
        signals = build_sector_axis_signals(summary)
        self.assertEqual(len(signals), 3)
        self.assertEqual({item.axis for item in signals}, {"sector_participation", "sector_relative_strength", "sector_activity"})
        self.assertTrue(all(item.status == AxisStatus.UNAVAILABLE for item in signals))

        broad_summary = aggregate_sector_features([self.relative] * 4)
        broad_signals = build_sector_axis_signals(broad_summary)
        self.assertTrue(all(item.status == AxisStatus.PASS for item in broad_signals))


class DerivedPipelineTests(unittest.TestCase):
    def _bar_payload(self, kind, start_price):
        rows = []
        for index in range(10):
            price = start_price + index
            common = {
                "stck_bsop_date": "20260731",
                "stck_cntg_hour": f"09{index:02d}00",
                "cntg_vol": str(100 if index < 5 else 300),
            }
            if kind == "index":
                common.update({
                    "bstp_nmix_oprc": str(price - 1), "bstp_nmix_hgpr": str(price + 1),
                    "bstp_nmix_lwpr": str(price - 2), "bstp_nmix_prpr": str(price),
                    "acml_tr_pbmn": str(price * 100),
                })
            elif kind == "futures":
                common.update({
                    "futs_oprc": str(price - 1), "futs_hgpr": str(price + 1),
                    "futs_lwpr": str(price - 2), "futs_prpr": str(price),
                })
            else:
                common.update({
                    "stck_oprc": str(price - 1), "stck_hgpr": str(price + 1),
                    "stck_lwpr": str(price - 2), "stck_prpr": str(price),
                    "acml_tr_pbmn": str(price * 100),
                })
            rows.append(common)
        return {"output2": rows}

    def test_bundle_builds_market_stock_sector_and_reports_missing_members(self):
        index = adapt_probe_payload(
            "kis_index_minute_kospi", self._bar_payload("index", 200),
            context=CONTEXT, instrument="KOSPI", verification_status=VerificationStatus.VERIFIED,
        )
        futures = adapt_probe_payload(
            "kis_futures_minute_active", self._bar_payload("futures", 300),
            context=CONTEXT, instrument="ACTIVE", verification_status=VerificationStatus.VERIFIED,
        )
        stock = adapt_probe_payload(
            "kis_stock_minute", self._bar_payload("stock", 100),
            context=CONTEXT, instrument="005930", verification_status=VerificationStatus.VERIFIED,
        )
        program = adapt_probe_payload(
            "kis_program_summary_kospi",
            {"output": [
                {"bsop_hour": "090000", "whol_smtn_ntby_tr_pbmn": "10", "arbt_smtn_ntby_tr_pbmn": "0", "nabt_smtn_ntby_tr_pbmn": "10"},
                {"bsop_hour": "090100", "whol_smtn_ntby_tr_pbmn": "20", "arbt_smtn_ntby_tr_pbmn": "0", "nabt_smtn_ntby_tr_pbmn": "20"},
            ]},
            context=CONTEXT, instrument="KOSPI", verification_status=VerificationStatus.VERIFIED,
        )
        observations = tuple(
            item
            for result in (index, futures, stock, program)
            for item in result.observations
        )
        bundle = derive_evidence_bundle(
            observations,
            stock_symbols=["005930", "000660"],
            sector_members={"semiconductor": ["005930", "000660"]},
            futures_prefix="futures.ACTIVE",
        )
        self.assertEqual(bundle.market_features.instrument_prefix, "index.KOSPI")
        self.assertIsNotNone(bundle.futures_features)
        self.assertIn("005930", bundle.stocks)
        self.assertEqual(bundle.missing_instruments, ("000660",))
        self.assertEqual(bundle.sectors["semiconductor"].observed_members, ("005930",))
        self.assertEqual(bundle.sectors["semiconductor"].missing_members, ("000660",))
        self.assertEqual({item.axis for item in bundle.market_signals}, {"price_action", "actual_flow", "futures"})
        self.assertTrue(bundle.placeholder_config)

    def test_bundle_requires_market_series_instead_of_inventing_it(self):
        with self.assertRaisesRegex(ValueError, "required market series"):
            derive_evidence_bundle([], stock_symbols=[], sector_members={})


class ConfigTests(unittest.TestCase):
    def test_repository_placeholder_config_is_versioned_and_loadable(self):
        path = Path(__file__).resolve().parents[1] / "config" / "market_betting_engine.placeholder.json"
        config = load_analysis_config(path)
        self.assertEqual(config.config_version, "expert-placeholder-v3-sector-breadth-tiers")
        self.assertTrue(config.placeholder)
        self.assertEqual(config.feature.short_return_bars, 5)

    def test_unknown_config_field_fails_loudly(self):
        import json

        payload = {
            "config_version": "bad-v1",
            "placeholder": True,
            "feature": {"unknown": 1},
            "signals": {},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unknown or invalid"):
                load_analysis_config(path)


if __name__ == "__main__":
    unittest.main()
