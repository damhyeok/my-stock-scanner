"""KRX cash-market closures relevant to the scheduled stock analysis jobs.

Keep the dated list aligned with the published KRX calendar before each new year.
An unlisted weekday is deliberately treated as open so a calendar outage or an
out-of-date list cannot silently suppress a real trading session.
"""

from datetime import date, datetime, timedelta, timezone


KST = timezone(timedelta(hours=9))

# 2026 remaining closures as of September 2026. Weekends are handled below.
# KRX disclosure calendar: https://kind.krx.co.kr/external/dst/reference/11625/
# 2026%20%EC%BD%94%EC%8A%A4%EB%8B%A5%EC%8B%9C%EC%9E%A5%20%EA%B3%B5%EC%8B%9C%EC%9D%BC%EC%A0%95%20%EC%BA%98%EB%A6%B0%EB%8D%94_vF.pdf
KRX_CLOSED_DATES = frozenset({
    date(2026, 9, 24),  # Chuseok
    date(2026, 9, 25),  # Chuseok
    date(2026, 10, 5),  # substitute holiday for National Foundation Day
    date(2026, 10, 9),  # Hangeul Day
    date(2026, 12, 25),  # Christmas
    date(2026, 12, 31),  # KRX year-end closure
})


def is_krx_closed(day=None):
    """Return whether the Korean cash market is closed on the KST date."""
    if day is None:
        day = datetime.now(KST).date()
    if isinstance(day, datetime):
        day = day.astimezone(KST).date() if day.tzinfo else day.date()
    return day.weekday() >= 5 or day in KRX_CLOSED_DATES
