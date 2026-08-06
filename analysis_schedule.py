"""Shared full-analysis schedule labels for Oracle timers and crawler sessions."""

FULL_ANALYSIS_SLOTS = (
    (9 * 60 + 30, "30 0 * * 1-5", "장중(09:30)"),
    (9 * 60 + 50, "50 0 * * 1-5", "장중(09:50)"),
    (10 * 60 + 50, "50 1 * * 1-5", "장중(10:50)"),
    (11 * 60 + 30, "30 2 * * 1-5", "장중(11:30)"),
    (11 * 60 + 50, "50 2 * * 1-5", "장중(11:50)"),
    (12 * 60 + 50, "50 3 * * 1-5", "장중(12:50)"),
    (14 * 60, "0 5 * * 1-5", "장중(14:00)"),
    (14 * 60 + 50, "50 5 * * 1-5", "장중(14:50)"),
    (16 * 60, "0 7 * * 1-5", "정규장(16:00)"),
)

FULL_ANALYSIS_SESSION_BY_CRON = {
    cron: session for _, cron, session in FULL_ANALYSIS_SLOTS
}


def closest_full_analysis_cron(hour: int, minute: int) -> str:
    current_minutes = int(hour) * 60 + int(minute)
    _, cron, _ = min(
        FULL_ANALYSIS_SLOTS,
        key=lambda item: abs(item[0] - current_minutes),
    )
    return cron
