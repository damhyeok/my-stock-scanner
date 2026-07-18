"""Column contracts shared by the staged close-bet modules."""

DAILY_COLUMNS = (
    "date",
    "ticker",
    "name",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "trading_value",
    "market_cap",
    "universe_type",
)

DAILY_KEY = ("date", "ticker", "universe_type")

OUTCOME_COLUMNS = (
    "signal_date",
    "ticker",
    "next_trade_date",
    "entry_close",
    "next_open",
    "next_high",
    "next_low",
    "next_close",
    "next_open_gap_return",
    "next_close_return",
    "mfe",
    "mae",
    "hit_plus_2pct",
    "hit_minus_2pct",
    "first_touch",
    "intraday_path_available",
)
