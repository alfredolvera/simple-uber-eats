"""Validated minimal persisted entry data."""

from datetime import UTC, tzinfo

from homeassistant.util import dt as dt_util

from .web_session import WebSession


async def async_valid_timezone(value, fallback="UTC") -> tzinfo:
    """Resolve a user or system zone without disk I/O on the event loop."""
    for candidate in (value, fallback, "UTC"):
        if not isinstance(candidate, str):
            continue
        try:
            timezone = await dt_util.async_get_time_zone(candidate)
        except ValueError:
            # Absolute or otherwise malformed ZoneInfo keys are invalid too.
            continue
        if timezone is not None:
            return timezone
    return UTC


def clean_entry_data(data, timezone: tzinfo) -> dict:
    """Drop legacy keys and persist the name of an already resolved zone."""
    credentials = WebSession(data.get("sid", ""), data.get("session_id", ""))
    result = credentials.as_entry_data()
    name = data.get("account_name")
    if isinstance(name, str) and name.strip():
        result["account_name"] = name.strip()
    legacy = data.get("legacy_entry_id")
    if isinstance(legacy, str) and legacy:
        result["legacy_entry_id"] = legacy
    result["time_zone"] = getattr(timezone, "key", "UTC")
    return result
