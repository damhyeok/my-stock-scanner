import sqlite3
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from etf_sector_flow import ETF_UNIVERSE, EtfSectorCollector, build_ranking, build_trend, init_tables
from market_calendar import KST


def observations():
    dates = pd.bdate_range(end="2026-09-23", periods=7).strftime("%Y%m%d").tolist()
    rows = [dict(trade_date=d, session="장중(09:30)", ticker="091160", sector="반도체", name="ETF",
                 price=100, change_rate=1, trading_value=1e8, kospi_rate=0,
                 collected_at_kst=f"{d[:4]}-{d[4:6]}-{d[6:]} 09:31:00") for d in dates]
    rows += [dict(rows[-1], trade_date="20260928", price=110, change_rate=3, kospi_rate=1,
                  trading_value=2e8, collected_at_kst="2026-09-28 09:32:00")]
    history = pd.DataFrame([dict(trade_date=d, ticker="091160", close=100, trading_value=1e8) for d in dates])
    return pd.DataFrame(rows), history


def test_ranking_no_future_or_other_slot():
    snaps, daily = observations()
    wrong = pd.DataFrame([dict(snaps.iloc[0], session="정규장(16:00)", trading_value=999e8),
                          dict(snaps.iloc[-1], trade_date="20260929", trading_value=999e8)])
    result = build_ranking(pd.concat([snaps, wrong]), daily, "20260928", "장중(09:30)").iloc[0]
    assert result.trading_multiple == 2
    assert result.excess_rate == 2
    assert abs(result.return_5d - 10) < 1e-8
    assert result.baseline_days == 7


def test_insufficient_and_delayed_baseline_not_zero():
    snaps, daily = observations()
    snaps.loc[snaps.trade_date < "20260928", "collected_at_kst"] = "2026-09-23 10:00:00"
    result = build_ranking(snaps, daily, "20260928", "장중(09:30)").iloc[0]
    assert pd.isna(result.trading_multiple)
    assert build_ranking(snaps, daily, "20260928", "장중(14:00)").empty


def test_trend_uses_selected_price_not_later_close():
    snaps, daily = observations()
    daily = pd.concat([daily, pd.DataFrame([dict(trade_date="20260928", ticker="091160", close=999, trading_value=0)])])
    ranks = build_ranking(snaps, daily, "20260928", "장중(09:30)")
    trend = build_trend(daily, ranks, "20260928", ["091160"])
    assert abs(trend.iloc[-1]["return"] - 10) < 1e-8
    assert len(trend) <= 10


def test_holiday_creates_no_snapshots(tmp_path):
    crawler = SimpleNamespace(db_path=str(tmp_path / "test.db"), target_date="20261005")
    collector = EtfSectorCollector(crawler)
    with patch("etf_sector_flow.datetime") as clock:
        clock.now.return_value = datetime(2026, 10, 5, 9, 30, tzinfo=KST)
        assert collector.run()["status"] == "market_closed"
    with sqlite3.connect(crawler.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM etf_sector_snapshots").fetchone()[0] == 0


def test_collector_units_and_rerun_removes_stale(tmp_path):
    crawler = SimpleNamespace(db_path=str(tmp_path / "test.db"), target_date="20261002",
                              _get_session_name=lambda: "장중(09:30)", _get_kis_access_token=lambda: "test")
    collector = EtfSectorCollector(crawler, ETF_UNIVERSE[:1])
    def request(path, tr, params):
        if "index-price" in path:
            return {"output": {"bstp_nmix_prdy_ctrt": "1"}}
        if "component" in path:
            return {"output2": [{"stck_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자", "etf_cnfg_issu_rlim": "17", "prdy_ctrt": "2"}]}
        return {"output": {"stck_prpr": "110", "prdy_ctrt": "3", "acml_tr_pbmn": "200000000"}}
    with patch("etf_sector_flow.datetime") as clock, patch.object(collector, "request", side_effect=request), patch.object(collector, "history", return_value=("20261002", [])):
        clock.now.return_value = datetime(2026, 10, 2, 9, 30, tzinfo=KST)
        assert collector.run()["status"] == "success"
    with sqlite3.connect(crawler.db_path) as conn:
        assert conn.execute("SELECT trading_value FROM etf_sector_snapshots").fetchone()[0] == 200000000
    with patch("etf_sector_flow.datetime") as clock, patch.object(collector, "request", side_effect=RuntimeError("outage")):
        clock.now.return_value = datetime(2026, 10, 2, 9, 30, tzinfo=KST)
        assert collector.run()["status"] == "failed"
    with sqlite3.connect(crawler.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM etf_sector_snapshots").fetchone()[0] == 0


def test_universe_includes_food_and_mixed_sector_labels():
    assert len({r[1] for r in ETF_UNIVERSE}) == len(ETF_UNIVERSE) == 18
    assert ("식품", "438900") in [(r[0], r[1]) for r in ETF_UNIVERSE]
    assert "에너지·화학" in [r[0] for r in ETF_UNIVERSE]


def test_ui_renders_empty_and_populated_database(tmp_path):
    from streamlit.testing.v1 import AppTest
    path = tmp_path / "ui.db"
    with sqlite3.connect(path) as conn:
        init_tables(conn)
    script = "from etf_sector_ui import render_etf_sector_tab\n" + f"render_etf_sector_tab({str(path)!r}, 'empty', '20260928', '장중(09:30)')"
    app = AppTest.from_string(script).run(timeout=30)
    assert not app.exception
    assert len(app.info) == 1
    snaps, daily = observations()
    with sqlite3.connect(path) as conn:
        snaps.to_sql("etf_sector_snapshots", conn, if_exists="append", index=False)
        daily.to_sql("etf_sector_daily", conn, if_exists="append", index=False)
    app = AppTest.from_string(script.replace("'empty'", "'populated'")).run(timeout=30)
    assert not app.exception
    assert app.multiselect[0].value == ["반도체"]
    assert app.selectbox[0].value == "반도체"


def test_web_retention_includes_bounded_etf_tables(tmp_path):
    from web_database import RETENTION, _trim_to_latest_dates
    with sqlite3.connect(tmp_path / "retention.db") as conn:
        init_tables(conn)
        conn.executemany("INSERT INTO etf_sector_daily VALUES (?,?,?,?)",
                         [(d, "091160", 100, 1000) for d in pd.bdate_range(end="2026-09-23", periods=80).strftime("%Y%m%d")])
        column, limit = RETENTION["etf_sector_daily"]
        _trim_to_latest_dates(conn, "etf_sector_daily", column, limit)
        assert conn.execute("SELECT COUNT(*) FROM etf_sector_daily").fetchone()[0] == 60
