"""Diagnostics support for the Uber Eats integration."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return sanitized, in-memory diagnostics for a config entry."""
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    integration = await async_get_integration(hass, DOMAIN)
    history = coordinator.tracking_diagnostic_history if coordinator else []
    update_interval = coordinator.update_interval if coordinator else None

    return {
        "integration_version": integration.version,
        "polling_interval_seconds": (
            update_interval.total_seconds() if update_interval is not None else None
        ),
        "connection_state": coordinator.connection_state if coordinator else "unknown",
        "consecutive_http_429": (
            coordinator.consecutive_rate_limits if coordinator else 0
        ),
        "rate_limit_backoff_seconds": (
            coordinator.rate_limit_backoff_seconds if coordinator else None
        ),
        "diagnostic_samples_retained": len(history),
        "tracking_diagnostic_history": history,
    }
