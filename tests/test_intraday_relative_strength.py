import pandas as pd

from intraday_relative_strength import (
    calculate_relative_strength,
    classify_relative_strength,
    session_cutoff,
)


def test_session_cutoff_caps_after_close():
    assert session_cutoff("장중(09:30)") == "09:30"
    assert session_cutoff("정규장(16:00)") == "15:30"


def test_rising_market_leader_uses_only_aligned_bars():
    stock = pd.DataFrame(
        {
            "bar_time": ["09:00", "09:01", "09:02"],
            "open": [100, 100, 101],
            "close": [100, 101, 103],
            "change_rate": [0.0, 1.0, 3.0],
        }
    )
    index = pd.DataFrame(
        {
            "bar_time": ["09:00", "09:01", "09:02"],
            "open": [1000, 1000, 1005],
            "close": [1000, 1005, 1010],
            "change_rate": [0.0, 0.5, 1.0],
        }
    )
    result = calculate_relative_strength(stock, index, trading_value=100_000_000_000)
    assert result["classification"] == "상승장 주도"
    assert result["excess_return"] == 2.0
    assert result["matched_bars"] == 3


def test_down_market_classification():
    assert classify_relative_strength(-1.5, 0.5, 2.0, 80) == "하락장 역행"
    assert classify_relative_strength(-1.5, -0.3, 1.2, 80) == "하락장 방어"


def test_fixed_preclose_and_closing_auction_returns_use_stock_prices():
    times = ["14:50", "15:20", "15:30"]
    stock = pd.DataFrame(
        {
            "bar_time": times,
            "open": [100, 102, 103],
            "close": [100, 103, 104],
            "change_rate": [0.0, 3.0, 4.0],
        }
    )
    index = pd.DataFrame(
        {
            "bar_time": times,
            "open": [1000, 1000, 1000],
            "close": [1000, 1000, 1000],
            "change_rate": [0.0, 0.0, 0.0],
        }
    )

    result = calculate_relative_strength(stock, index)

    assert round(result["pre_close_30m_return"], 6) == 3.0
    assert round(result["closing_auction_return"], 6) == round((104 / 103 - 1) * 100, 6)


def test_fixed_close_intervals_are_missing_before_their_bars_exist():
    stock = pd.DataFrame(
        {"bar_time": ["13:59", "14:00"], "open": [100, 100], "close": [100, 101], "change_rate": [0, 1]}
    )
    index = pd.DataFrame(
        {"bar_time": ["13:59", "14:00"], "open": [1000, 1000], "close": [1000, 1001], "change_rate": [0, 0.1]}
    )

    result = calculate_relative_strength(stock, index)

    assert result["pre_close_30m_return"] is None
    assert result["closing_auction_return"] is None
