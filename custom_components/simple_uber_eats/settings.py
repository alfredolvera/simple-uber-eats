"""Validated minimal persisted entry data."""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .web_session import WebSession


def valid_timezone(value, fallback="UTC") -> str:
    """Resolve user or system timezone to a valid IANA zone."""
    for candidate in (value, fallback, "UTC"):
        if not isinstance(candidate, str):
            continue
        try:
            ZoneInfo(candidate)
        except (ValueError, ZoneInfoNotFoundError):
            continue
        return candidate
    return "UTC"


def clean_entry_data(data, fallback="UTC") -> dict:
    """Drop historical cookie dumps and unrelated legacy keys."""
    credentials = WebSession(data.get("sid", ""), data.get("session_id", ""))
    result = credentials.as_entry_data()
    name = data.get("account_name")
    if isinstance(name, str) and name.strip():
        result["account_name"] = name.strip()
    legacy = data.get("legacy_entry_id")
    if isinstance(legacy, str) and legacy:
        result["legacy_entry_id"] = legacy
    result["time_zone"] = valid_timezone(data.get("time_zone"), fallback)
    return result
