"""Pure bounded date inference for Uber ETA labels."""
from __future__ import annotations

from datetime import datetime, timedelta

RECENT_PAST_TOLERANCE = timedelta(minutes=5)
MAX_PLAUSIBLE_FUTURE = timedelta(hours=12)


def infer_time_only_eta(now: datetime, hour: int, minute: int) -> datetime | None:
    """Attach a plausible date to an ETA containing only a clock time."""
    if now.tzinfo is None or not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None

    today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    today_delta = today - now
    if -RECENT_PAST_TOLERANCE <= today_delta <= MAX_PLAUSIBLE_FUTURE:
        return today

    tomorrow = today + timedelta(days=1)
    tomorrow_delta = tomorrow - now
    if timedelta(0) <= tomorrow_delta <= MAX_PLAUSIBLE_FUTURE:
        return tomorrow
    return None
