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
