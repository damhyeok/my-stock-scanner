from market_strength import MarketStrengthAnalyzer


def make_analyzer(tmp_path):
    return MarketStrengthAnalyzer(
        db_path=str(tmp_path / "market.db"),
        analysis_type="closing",
    )


def suspicious_snapshots():
    times = ["14:30", "15:00", "15:20", "15:30"]
    basis = [2.35, 23.45, 2.99, 3.77]
    program = [-2128642, -2279388, -2140312, -2127259]
    non_arbitrage = [-2349795, -2489727, -2312120, -2299074]
    futures = [1081.12, 1102.22, 1081.76, 1082.54]
    return {
        time: {
            "basis": basis[index],
            "program_net": program[index],
            "non_arbitrage_net": non_arbitrage[index],
            "kospi200_futures_price": futures[index],
            "futures_day_high": 1210.88,
            "futures_day_low": 1076.72,
            "futures_vwap": 20213335.0,
        }
        for index, time in enumerate(times)
    }


def test_slightly_smaller_program_selling_gets_no_trend_points(tmp_path):
    analyzer = make_analyzer(tmp_path)
    assert analyzer._score_program(suspicious_snapshots()) <= 5


def test_suspicious_market_data_is_detected(tmp_path):
    analyzer = make_analyzer(tmp_path)
    issues = analyzer._data_quality_issues(suspicious_snapshots())
    assert "베이시스 급변값" in issues
    assert "선물 VWAP 오류" in issues


def test_interpretation_calls_negative_program_flow_selling(tmp_path):
    analyzer = make_analyzer(tmp_path)
    snapshots = suspicious_snapshots()
    text = analyzer._build_interpretation(40, 20, 2, 8, snapshots)
    assert "프로그램 순매도가 이어지고 있습니다" in text
    assert "프로그램 순매수가 증가했습니다" not in text


def test_intraday_vwap_uses_bar_price_and_volume(tmp_path):
    analyzer = make_analyzer(tmp_path)
    rows = [
        {"stck_cntg_hour": "143000", "futs_prpr": "100", "cntg_vol": "2"},
        {"stck_cntg_hour": "150000", "futs_prpr": "110", "cntg_vol": "1"},
        {"stck_cntg_hour": "152000", "futs_prpr": "200", "cntg_vol": "10"},
    ]
    assert analyzer._intraday_vwap(rows, "150000") == 310 / 3


def test_score_explanation_describes_why_points_were_limited(tmp_path):
    analyzer = make_analyzer(tmp_path)
    notes = analyzer.explain_scores(suspicious_snapshots())
    assert "급변값은 신뢰도가 낮아 감점" in notes["basis"]
    assert "순매도라 절대 수급 가점 없음" in notes["program"]
    assert "마지막 반등은 보조점수만 인정" in notes["futures"]


def test_repeated_basis_is_excluded_and_other_scores_are_reweighted(tmp_path):
    analyzer = make_analyzer(tmp_path)
    snapshots = suspicious_snapshots()
    for row in snapshots.values():
        row["basis"] = 1.89
    scores = analyzer.score_snapshots(snapshots)
    assert scores["basis_valid"] is False
    assert scores["basis_score"] == 0
    assert scores["market_strength_score"] == round(
        (scores["program_score"] + scores["futures_trend_score"]) / 65 * 100
    )


def test_strict_minute_match_rejects_distant_or_future_rows(tmp_path):
    analyzer = make_analyzer(tmp_path)
    rows = [
        {"stck_cntg_hour": "142800", "value": "too_old"},
        {"stck_cntg_hour": "143100", "value": "future"},
    ]
    assert analyzer._nearest_row(
        rows,
        "143000",
        "stck_cntg_hour",
        max_gap_seconds=60,
        allow_future=False,
    ) == {}


def test_index_pages_use_minute_interval_and_reach_earliest_snapshot(tmp_path):
    analyzer = MarketStrengthAnalyzer(
        db_path=str(tmp_path / "market.db"), analysis_type="afternoon"
    )
    requested = []

    def fake_kis_get(path, tr_id, params, tr_cont=""):
        requested.append((params["FID_INPUT_HOUR_1"], tr_cont))
        if not tr_cont:
            rows = [
                {"stck_cntg_hour": "140000", "bstp_nmix_prpr": "100"},
                {"stck_cntg_hour": "134500", "bstp_nmix_prpr": "99"},
            ]
            response_tr_cont = "M"
        else:
            rows = [
                {"stck_cntg_hour": "134400", "bstp_nmix_prpr": "98.5"},
                {"stck_cntg_hour": "133000", "bstp_nmix_prpr": "98"},
            ]
            response_tr_cont = ""
        return {
            "rt_cd": "0",
            "output1": {},
            "output2": rows,
            "_response_tr_cont": response_tr_cont,
        }

    analyzer._kis_get = fake_kis_get
    snapshots = analyzer._fetch_index_snapshots()
    assert requested == [("60", ""), ("60", "N")]
    assert snapshots == {"13:30": 98.0, "13:45": 99.0, "14:00": 100.0}


def test_index_single_page_ignores_invalid_time_and_matches_closing_minutes(tmp_path):
    analyzer = make_analyzer(tmp_path)
    rows = [
        {"stck_bsop_date": analyzer.target_date, "stck_cntg_hour": "999999", "bstp_nmix_prpr": "0"},
        {"stck_bsop_date": analyzer.target_date, "stck_cntg_hour": "143000", "bstp_nmix_prpr": "1074.5"},
        {"stck_bsop_date": analyzer.target_date, "stck_cntg_hour": "150000", "bstp_nmix_prpr": "1073.8"},
        {"stck_bsop_date": analyzer.target_date, "stck_cntg_hour": "152000", "bstp_nmix_prpr": "1073.7"},
        {"stck_bsop_date": analyzer.target_date, "stck_cntg_hour": "153000", "bstp_nmix_prpr": "1074.1"},
    ]

    def fake_kis_get(path, tr_id, params, tr_cont=""):
        assert params["FID_INPUT_HOUR_1"] == "60"
        assert tr_cont == ""
        return {"rt_cd": "0", "output2": rows, "_response_tr_cont": ""}

    analyzer._kis_get = fake_kis_get
    assert analyzer._fetch_index_snapshots() == {
        "14:30": 1074.5,
        "15:00": 1073.8,
        "15:20": 1073.7,
        "15:30": 1074.1,
    }


def test_missing_index_does_not_fallback_to_current_basis(tmp_path):
    analyzer = make_analyzer(tmp_path)
    analyzer._fetch_active_futures_code = lambda: "101V09"
    analyzer._fetch_index_snapshots = lambda: {}
    analyzer._fetch_backward_pages = lambda *args, **kwargs: (
        [
            {"stck_cntg_hour": "143000", "futs_prpr": "100", "cntg_vol": "1"},
            {"stck_cntg_hour": "150000", "futs_prpr": "101", "cntg_vol": "1"},
            {"stck_cntg_hour": "152000", "futs_prpr": "102", "cntg_vol": "1"},
            {"stck_cntg_hour": "153000", "futs_prpr": "103", "cntg_vol": "1"},
        ],
        {"output1": {"basis": "1.89", "futs_hgpr": "104", "futs_lwpr": "99"}},
    )
    snapshots = analyzer._fetch_futures_snapshots()
    assert all(row["basis"] is None for row in snapshots.values())
    assert analyzer._basis_is_valid(snapshots) is False


def test_basis_outside_safe_range_is_invalid(tmp_path):
    analyzer = make_analyzer(tmp_path)
    snapshots = suspicious_snapshots()
    snapshots["14:30"]["basis"] = analyzer.BASIS_MAX_ABS + 0.01
    assert analyzer._basis_is_valid(snapshots) is False
