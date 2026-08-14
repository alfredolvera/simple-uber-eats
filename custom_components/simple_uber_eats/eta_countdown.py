"""Pure time inference and local countdown behavior for Uber ETA labels."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import math

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


def remaining_seconds(arrival_time: datetime, now: datetime) -> int:
    """Return a non-negative whole-second countdown."""
    if arrival_time.tzinfo is None or now.tzinfo is None:
        raise ValueError("arrival_time and now must be timezone-aware")
    return max(0, math.ceil((arrival_time - now).total_seconds()))


def format_countdown(seconds: int) -> str:
    """Format total minutes and seconds without wrapping at one hour."""
    safe_seconds = max(0, int(seconds))
    minutes, seconds_part = divmod(safe_seconds, 60)
    return f"{minutes:02d}:{seconds_part:02d}"


@dataclass(slots=True)
class CountdownClock:
    """Monotonic local view of an authoritative arrival timestamp."""

    arrival_time: datetime | None = None
    _anchor_seconds: int | None = None
    _anchor_monotonic: float | None = None

    def rebase(
        self, arrival_time: datetime, *, wall_now: datetime, monotonic_now: float
    ) -> int:
        self.arrival_time = arrival_time
        self._anchor_seconds = remaining_seconds(arrival_time, wall_now)
        self._anchor_monotonic = monotonic_now
        return self._anchor_seconds

    def clear(self) -> None:
        self.arrival_time = None
        self._anchor_seconds = None
        self._anchor_monotonic = None

    def remaining(self, monotonic_now: float) -> int | None:
        if self._anchor_seconds is None or self._anchor_monotonic is None:
            return None
        elapsed = max(0.0, monotonic_now - self._anchor_monotonic)
        return max(0, math.ceil(self._anchor_seconds - elapsed))
