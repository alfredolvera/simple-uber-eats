"""Runtime-derived IANA timezone choices for configuration forms."""
from __future__ import annotations

from functools import lru_cache
from zoneinfo import available_timezones


@lru_cache(maxsize=1)
def runtime_time_zones() -> tuple[str, ...]:
    """Return a stable snapshot of all zones available to this Python runtime."""
    return tuple(sorted(available_timezones()))


def selectable_time_zones(configured_time_zone: str) -> tuple[str, ...]:
    """Include Home Assistant's configured zone even on an unusual host."""
    zones = runtime_time_zones()
    if configured_time_zone in zones:
        return zones
    return tuple(sorted((*zones, configured_time_zone)))
