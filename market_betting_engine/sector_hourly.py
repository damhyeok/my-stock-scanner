"""Sector-only path analysis. Every observed minute matters; no gap filling.

Windows are clock-time windows, not the last N rows. Thresholds are starting
rules, not calibrated forecasts. Closing-auction prints are kept separate.
"""
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from math import isfinite
from typing import Optional, Sequence

from .features import NormalizedBar
from .session import KST


@dataclass(frozen=True)
class HourlyPath:
    mode: str = "ROLLING_60M_PATH_V1"
    status: str = "INSUFFICIENT"
    window_start: str = ""
    window_end: str = ""
    expected_minutes: int = 60
    observed_minutes: int = 0
    aligned_minutes: int = 0
    coverage: float = 0.0
    return_ratio: Optional[float] = None
    relative_return: Optional[float] = None
    relative_retention: Optional[float] = None
    vwap_retention: Optional[float] = None
    window_vwap: Optional[float] = None
    session_vwap: Optional[float] = None
    higher_low_ratio: Optional[float] = None
    price_slope_per_minute: Optional[float] = None
    giveback_ratio: Optional[float] = None
    max_drawdown: Optional[float] = None
    up_turnover_share: Optional[float] = None
    activity_change: Optional[float] = None
    above_vwap: Optional[bool] = None
    outperforming: Optional[bool] = None
    structure_confirming: Optional[bool] = None
    activity_confirming: Optional[bool] = None
    flags: tuple[str, ...] = ()


def _bars_by_minute(bars, day, end):
    result = {}
    for bar in sorted(bars, key=lambda b: b.timestamp):
        stamp = bar.timestamp.astimezone(KST).replace(second=0, microsecond=0)
        values = (bar.open, bar.high, bar.low, bar.close, bar.volume)
        if (stamp.date() != day or stamp > end or stamp.time() < time(9)
                or not all(isfinite(v) for v in values) or min(values[:4]) <= 0
                or bar.volume < 0 or not bar.low <= bar.close <= bar.high):
            continue
        result[stamp] = bar
    return result


def _coverage_ok(stamps, start, end):
    expected = int((end - start).total_seconds() // 60) + 1
    ordered = sorted(stamps)
    if not ordered or len(ordered) / expected < .90:
        return False
    if ordered[0] != start or ordered[-1] != end:
        return False
    return all((right - left).total_seconds() <= 180 for left, right in zip(ordered, ordered[1:]))


def derive_hourly_path(
    stock_bars: Sequence[NormalizedBar], benchmark_bars: Sequence[NormalizedBar],
    *, as_of: datetime,
) -> HourlyPath:
    as_of = as_of.astimezone(KST).replace(second=0, microsecond=0)
    day = as_of.date()
    market_open = datetime.combine(day, time(9), KST)
    continuous_end = datetime.combine(day, time(15, 19), KST)
    end = min(as_of, continuous_end)
    if end < market_open:
        return HourlyPath(flags=("BEFORE_OPEN",))
    start = max(market_open, end - timedelta(minutes=59))
    expected = int((end - start).total_seconds() // 60) + 1
    stocks = _bars_by_minute(stock_bars, day, end)
    index = _bars_by_minute(benchmark_bars, day, end)
    stamps = sorted(t for t in stocks if start <= t <= end)
    aligned = [t for t in stamps if t in index]
    flags = ["AUCTION_EXCLUDED"] if as_of > continuous_end else []
    base = dict(window_start=start.isoformat(), window_end=end.isoformat(),
                expected_minutes=expected, observed_minutes=len(stamps),
                aligned_minutes=len(aligned), coverage=len(aligned) / expected)
    if expected < 60:
        flags.append("OPENING_PARTIAL_WINDOW")
    if expected < 20 or not _coverage_ok(aligned, start, end):
        return HourlyPath(**base, flags=tuple(flags + ["MISSING_OR_UNALIGNED_MINUTES"]))

    # Each minute's cumulative excess return uses the same starting minute
    # and benchmark timestamps. Intermediate values are not interpolated.
    stock_base, index_base = stocks[start].open, index[start].open
    relatives = [stocks[t].close / stock_base - index[t].close / index_base for t in aligned]
    relative = relatives[-1]
    relative_retention = sum(value > 0 for value in relatives) / len(relatives)
    volume = weighted_price = 0.0
    holds = []
    for t in stamps:
        b = stocks[t]
        volume += b.volume
        weighted_price += (b.high + b.low + b.close) / 3 * b.volume
        if volume > 0:
            holds.append(b.close >= weighted_price / volume)
    if volume <= 0 or len(holds) < len(stamps) * .90:
        return HourlyPath(**base, flags=tuple(flags + ["VOLUME_UNAVAILABLE"]))
    vwap = weighted_price / volume
    vwap_retention = sum(holds) / len(holds)
    session_vwap = None
    if _coverage_ok(list(stocks), market_open, end):
        day_volume = sum(b.volume for b in stocks.values())
        if day_volume:
            session_vwap = sum((b.high + b.low + b.close) / 3 * b.volume for b in stocks.values()) / day_volume
    else:
        flags.append("FULL_SESSION_VWAP_UNAVAILABLE")

    # Low structure uses all lows in successive ten-minute blocks, not just
    # the boundary candles. A tiny incomplete last block is not compared.
    blocks = {}
    for t in stamps:
        slot = int((t - start).total_seconds() // 600)
        blocks.setdefault(slot, []).append(stocks[t].low)
    lows = [min(values) for _, values in sorted(blocks.items()) if len(values) >= 8]
    higher_lows = sum(b > a for a, b in zip(lows, lows[1:])) / (len(lows) - 1) if len(lows) > 1 else None
    xs = [(t - start).total_seconds() / 60 for t in stamps]
    ys = [stocks[t].close / stock_base - 1 for t in stamps]
    xmean, ymean = sum(xs) / len(xs), sum(ys) / len(ys)
    slope = sum((x - xmean) * (y - ymean) for x, y in zip(xs, ys)) / sum((x - xmean) ** 2 for x in xs)
    high = max(stocks[t].high for t in stamps)
    last = stocks[end].close
    giveback = max(0.0, (high - last) / (high - stock_base)) if high > stock_base else 1.0
    peak, drawdown = stock_base, 0.0
    up = down = 0.0
    for t in stamps:
        b = stocks[t]
        peak = max(peak, b.close)
        drawdown = max(drawdown, 1 - b.close / peak)
        previous = stocks.get(t - timedelta(minutes=1))
        reference = previous.close if previous is not None else (b.open if t == start else None)
        if reference is not None:
            if b.close > reference:
                up += b.close * b.volume
            elif b.close < reference:
                down += b.close * b.volume
    up_share = up / (up + down) if up + down else None
    prior_start, prior_end = start - timedelta(minutes=60), start - timedelta(minutes=1)
    prior_stamps = [t for t in stocks if prior_start <= t <= prior_end]
    acceleration = None
    if expected == 60 and prior_start >= market_open and _coverage_ok(prior_stamps, prior_start, prior_end):
        prior_activity = sum(stocks[t].close * stocks[t].volume for t in prior_stamps) / len(prior_stamps)
        current_activity = sum(stocks[t].close * stocks[t].volume for t in stamps) / len(stamps)
        if prior_activity > 0:
            acceleration = current_activity / prior_activity - 1
    if acceleration is None:
        flags.append("PREVIOUS_HOUR_UNAVAILABLE")
    structure = slope > 0 and higher_lows is not None and higher_lows >= .60 and giveback <= .50
    return HourlyPath(
        **base, status="COMPLETE" if expected == 60 else "PROVISIONAL",
        return_ratio=last / stock_base - 1, relative_return=relative,
        relative_retention=relative_retention, vwap_retention=vwap_retention,
        window_vwap=vwap, session_vwap=session_vwap, higher_low_ratio=higher_lows,
        price_slope_per_minute=slope, giveback_ratio=giveback, max_drawdown=drawdown,
        up_turnover_share=up_share, activity_change=acceleration,
        above_vwap=last >= vwap and vwap_retention >= .60,
        outperforming=relative > 0 and relative_retention >= .60,
        structure_confirming=structure,
        activity_confirming=up_share is not None and up_share >= .55 and last > stock_base
            and (acceleration is None or acceleration >= 0),
        flags=tuple(flags),
    )
