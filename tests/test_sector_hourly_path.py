import unittest
from dataclasses import replace, asdict
from datetime import datetime, timedelta
from unittest.mock import patch

from market_betting_engine.features import NormalizedBar
from market_betting_engine.sector_hourly import derive_hourly_path
from market_betting_engine.session import KST
from market_betting_engine.signals import aggregate_hourly_sector_features, build_sector_axis_signals
from market_betting_engine.contracts import AxisStatus
from market_betting_engine.streamlit_tab import _sector_breadth_tier, _sector_member_groups, hourly_member_rows
from market_betting_engine.api_probe import _kis_sector_stock_window_request, PROBE_SPECS
from market_betting_engine.collector import collect_probe_observations
from market_betting_engine.session import SessionContext


START = datetime(2026, 9, 11, 9, tzinfo=KST)


def bars(count=60, *, start=START, style="steady", volume=100):
    result = []
    for i in range(count):
        if style == "steady":
            close = 100 + 3 * (i + 1) / count
        elif style == "late":
            close = 100 if i < count - 2 else (101.5 if i == count - 2 else 103)
        elif style == "roundtrip":
            close = 100 + (i + 1) * .3 if i < count // 2 else 109 - (i - count // 2 + 1) * .2
        else:
            close = 100
        result.append(NormalizedBar(start + timedelta(minutes=i), result[-1].close if result else 100,
                                    close + .01, close - .02, close, volume))
    return tuple(result)


def path(stock, index=None, as_of=None):
    return derive_hourly_path(stock, index or bars(len(stock), start=stock[0].timestamp, style="flat"),
                              as_of=as_of or stock[-1].timestamp)


class HourlyPathTests(unittest.TestCase):
    def test_same_endpoint_different_paths_do_not_get_same_pass(self):
        steady, late = path(bars()), path(bars(style="late"))
        self.assertAlmostEqual(steady.return_ratio, late.return_ratio)
        self.assertEqual(steady.status, "COMPLETE")
        self.assertTrue(steady.outperforming)
        self.assertTrue(steady.structure_confirming)
        self.assertFalse(late.outperforming)
        self.assertFalse(late.structure_confirming)
        self.assertGreater(steady.relative_retention, late.relative_retention)

    def test_roundtrip_is_not_sustained_structure(self):
        result = path(bars(style="roundtrip"))
        self.assertGreater(result.giveback_ratio, .5)
        self.assertFalse(result.structure_confirming)

    def test_missing_middle_minutes_are_not_filled(self):
        full = bars()
        missing = tuple(b for i, b in enumerate(full) if i not in {20, 21, 22})
        result = path(missing, bars(style="flat"))
        self.assertEqual(result.observed_minutes, 57)
        self.assertEqual(result.status, "INSUFFICIENT")

    def test_few_isolated_missing_minutes_are_disclosed(self):
        full = bars()
        missing = tuple(b for i, b in enumerate(full) if i not in {20, 40})
        result = path(missing, bars(style="flat"))
        self.assertEqual(result.aligned_minutes, 58)
        self.assertAlmostEqual(result.coverage, 58 / 60)
        self.assertEqual(result.status, "COMPLETE")

    def test_sixty_sparse_rows_do_not_mean_one_hour(self):
        stock = tuple(replace(b, timestamp=START + timedelta(minutes=i * 2)) for i, b in enumerate(bars()))
        result = path(stock, bars(120, style="flat"))
        self.assertEqual(result.status, "INSUFFICIENT")
        self.assertLessEqual(result.observed_minutes, 30)

    def test_early_window_is_provisional_and_cannot_promote(self):
        result = path(bars(30))
        self.assertEqual(result.status, "PROVISIONAL")
        summary = aggregate_hourly_sector_features([result] * 4)
        self.assertEqual(build_sector_axis_signals(summary)[0].status, AxisStatus.UNAVAILABLE)
        self.assertEqual(_sector_breadth_tier({"decision": "NOT_EVALUABLE"}, asdict(summary)), "PROVISIONAL")

    def test_under_twenty_minutes_and_zero_volume_are_unavailable(self):
        self.assertEqual(path(bars(10)).status, "INSUFFICIENT")
        self.assertEqual(path(bars(volume=0)).status, "INSUFFICIENT")

    def test_stale_stock_end_is_not_compared_to_current_index(self):
        self.assertEqual(path(bars()[:-1], bars(style="flat"), bars()[-1].timestamp).status, "INSUFFICIENT")

    def test_future_and_previous_day_rows_do_not_leak(self):
        original = bars()
        future = replace(original[-1], timestamp=original[-1].timestamp + timedelta(minutes=1), close=500, high=501)
        old = replace(original[0], timestamp=START - timedelta(days=1))
        self.assertEqual(path(original + (future, old), bars(style="flat"), original[-1].timestamp), path(original))

    def test_duplicate_minutes_do_not_inflate_coverage(self):
        self.assertEqual(path(bars() + bars(), bars(style="flat")).observed_minutes, 60)

    def test_previous_hour_activity_uses_equal_duration(self):
        stock = bars(120)
        stock = tuple(replace(b, volume=200 if i >= 60 else 100) for i, b in enumerate(stock))
        result = path(stock)
        self.assertEqual(result.observed_minutes, 60)
        self.assertGreater(result.activity_change, 1)
        self.assertNotIn("PREVIOUS_HOUR_UNAVAILABLE", result.flags)
        self.assertIsNone(path(bars()).activity_change)

    def test_auction_does_not_overwrite_continuous_path(self):
        stock = bars(start=START.replace(hour=14, minute=20))
        benchmark = bars(start=START.replace(hour=14, minute=20), style="flat")
        auction = replace(stock[-1], timestamp=START.replace(hour=15, minute=30), close=200, high=201, volume=10000000)
        result = path(stock + (auction,), benchmark, auction.timestamp)
        self.assertEqual(result.window_end[11:16], "15:19")
        self.assertAlmostEqual(result.return_ratio, path(stock, benchmark).return_ratio)
        self.assertIn("AUCTION_EXCLUDED", result.flags)

    def test_sector_uses_four_axes_and_respects_blockers(self):
        summary = aggregate_hourly_sector_features([path(bars())] * 4)
        self.assertEqual(len(build_sector_axis_signals(summary)), 4)
        self.assertTrue(all(s.status == AxisStatus.PASS for s in build_sector_axis_signals(summary)))
        self.assertEqual(_sector_breadth_tier({"decision": "LEADING"}, asdict(summary)), "GENUINE")
        self.assertEqual(_sector_breadth_tier({"decision": "NOT_EVALUABLE", "blockers": [{}]}, asdict(summary)), "NOT_EVALUABLE")

    def test_missing_members_cannot_silently_shrink_denominator(self):
        good, bad = path(bars()), path(bars(10))
        summary = aggregate_hourly_sector_features([good, good, bad, bad])
        self.assertEqual(summary.window_status, "INSUFFICIENT")
        self.assertEqual(summary.outperforming_ratio, .5)


class HourlyPagingTests(unittest.TestCase):
    def test_extended_sector_history_does_not_change_original_snapshot(self):
        raw = [dict(stck_bsop_date="20260911", stck_cntg_hour=b.timestamp.strftime("%H%M%S"),
                    stck_oprc=b.open, stck_hgpr=b.high, stck_lwpr=b.low, stck_prpr=b.close,
                    cntg_vol=b.volume) for b in bars(120)]
        first, full = {"rt_cd": "0", "output2": raw[-30:]}, {"rt_cd": "0", "output2": raw}
        def request(spec, ticker, current, *, initial_consumer, minutes):
            initial_consumer(first)
            return full, 200
        now = START.replace(hour=11)
        with patch("market_betting_engine.collector._kis_sector_stock_window_request", side_effect=request):
            result = collect_probe_observations("kis_stock_minute", context=SessionContext(now.date(), now, True, "TEST"),
                                                instrument="005930", ticker="005930", sector_window_minutes=120)
        self.assertEqual(result.adapted.included_rows, 30)
        self.assertEqual(result.sector_adapted.included_rows, 120)

    @patch("market_betting_engine.api_probe.time.sleep")
    def test_bounded_paging_preserves_first_snapshot(self, _sleep):
        rows = [dict(stck_bsop_date="20260911", stck_cntg_hour=(START + timedelta(minutes=i)).strftime("%H%M%S")) for i in range(150)]
        def request(spec, ticker, current):
            selected = [r for r in rows if r["stck_cntg_hour"] <= current.strftime("%H%M%S")]
            return {"rt_cd": "0", "output2": list(reversed(selected))[:30]}, 200
        initial = []
        with patch("market_betting_engine.api_probe._kis_request", side_effect=request) as calls:
            result, status = _kis_sector_stock_window_request(PROBE_SPECS["kis_stock_minute"], "005930", START + timedelta(minutes=150), initial_consumer=initial.append)
        self.assertEqual(status, 200)
        self.assertEqual(len(initial[0]["output2"]), 30)
        self.assertEqual(len(result["output2"]), 120)
        self.assertLessEqual(calls.call_count, 5)
        self.assertEqual(min(r["stck_cntg_hour"] for r in result["output2"]), "093000")

    @patch("market_betting_engine.api_probe.time.sleep")
    def test_no_progress_stops_and_preserves_partial_data(self, _sleep):
        payload = {"rt_cd": "0", "output2": [dict(stck_bsop_date="20260911", stck_cntg_hour="112900")]}
        with patch("market_betting_engine.api_probe._kis_request", return_value=(payload, 200)) as calls:
            result, _ = _kis_sector_stock_window_request(PROBE_SPECS["kis_stock_minute"], "005930", START.replace(hour=11, minute=30), initial_consumer=lambda p: None)
        self.assertEqual(calls.call_count, 2)
        self.assertEqual(len(result["output2"]), 1)


class HourlyDisplayTests(unittest.TestCase):
    def test_new_hourly_and_provisional_screens_render(self):
        from streamlit.testing.v1 import AppTest
        import market_betting_engine.streamlit_tab as ui
        for sample in (path(bars()), path(bars(30))):
            summary = aggregate_hourly_sector_features([sample] * 4)
            detail = {"run": {"run_id": "test", "derived_evidence": {
                "bundle": {"stocks": {"005930": {"hourly_path": asdict(sample)}},
                           "sectors": {"반도체": {"observed_members": ["005930"], "summary": asdict(summary)}}},
                "adaptive_universe": {"stocks": [{"ticker": "005930", "name": "삼성전자"}]},
            }}}
            decision = "GENUINE" if sample.status == "COMPLETE" else "PROVISIONAL"
            history = [dict(time=START.replace(hour=10), time_label="10:00", sector="반도체", decision=decision,
                            status=ui._SECTOR_STRENGTH_KO[decision], score=3 if decision == "GENUINE" else None,
                            reason="test", vwap_members="삼성전자", outperforming_members="삼성전자",
                            activity_members="삼성전자", structure_members="삼성전자")]
            with patch.object(ui, "build_sector_strength_history", return_value=(history, {"run_id": "test"})), \
                 patch.object(ui, "load_decision_run", return_value=detail), \
                 patch.object(ui, "build_daily_sector_strength_history", return_value=([], [])):
                at = AppTest.from_string("import streamlit as st\nfrom market_betting_engine.streamlit_tab import render_sector_strength_flow_tab\nrender_sector_strength_flow_tab(st,db_path='unused',selected_date='20260911',selected_session='장중(10:00)')").run()
                self.assertEqual([e.message for e in at.exception], [])
            groups = ui._sector_member_groups(detail, "반도체")
            self.assertEqual(groups["structure_confirming"], ["삼성전자"])
            self.assertEqual(len(ui.hourly_member_rows(detail)), 1)
