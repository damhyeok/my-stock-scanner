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
