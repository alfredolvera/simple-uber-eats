"""Lifecycle for the independently authored Simple Uber Eats integration."""

from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryAuthFailed

from .account import migrate_registry
from .compatibility import adopt_selected_legacy
from .http import create_session
from .model import AuthRejected, ServiceUnavailable
from .runtime import UberCoordinator
from .settings import clean_entry_data, valid_timezone
from .web_session import CookieInputError, WebSession
from .wire import Gateway

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.DEVICE_TRACKER]


async def async_setup_entry(hass, entry):
    """Validate active orders first; optional profile cannot block setup."""
    try:
        data = clean_entry_data(entry.data, hass.config.time_zone)
        credentials = WebSession(data["sid"], data["session_id"])
    except (CookieInputError, ValueError, TypeError):
        raise ConfigEntryAuthFailed("Session credentials must be renewed") from None
    if data != entry.data:
        hass.config_entries.async_update_entry(entry, data=data)
    session = create_session(hass)
    entry.async_on_unload(session.detach)
    try:
        gateway = Gateway(session, credentials, data["time_zone"])
        coordinator = UberCoordinator(hass, entry, gateway)
        await coordinator.async_config_entry_first_refresh()
        if not data.get("account_name"):
            try:
                name = await gateway.account_label()
            except (AuthRejected, ServiceUnavailable, TimeoutError):
                name = None
            finally:
                coordinator.persist_credentials()
            if name:
                hass.config_entries.async_update_entry(
                    entry, data={**entry.data, "account_name": name}
                )
        entry.runtime_data = coordinator
        adopt_selected_legacy(hass, entry)
        migrate_registry(hass, entry)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    except BaseException:
        session.detach()
        raise
    return True


async def _async_options_updated(hass, entry):
    """Replace entities so removed private attributes leave current state."""
    options = dict(entry.options)
    if options != entry.runtime_data.options_snapshot:
        entry.runtime_data.options_snapshot = options
        await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded


async def async_migrate_entry(hass, entry):
    """Only migrate our own version-one entries; never adopt another domain."""
    if entry.domain != "simple_uber_eats" or entry.version > 2:
        return False
    if entry.version == 1:
        try:
            data = clean_entry_data(entry.data, hass.config.time_zone)
        except (CookieInputError, ValueError, TypeError):
            # Setup raises ConfigEntryAuthFailed and starts a credential flow.
            data = {
                "sid": "",
                "session_id": "",
                "time_zone": valid_timezone(
                    entry.data.get("time_zone"), hass.config.time_zone
                ),
            }
            name = entry.data.get("account_name")
            if isinstance(name, str) and name.strip():
                data["account_name"] = name.strip()
        migrate_registry(hass, entry)
        hass.config_entries.async_update_entry(
            entry, data=data, version=2, minor_version=1
        )
    return True


async def async_remove_entry(hass, entry):
    """Forget non-secret retry state after an entry is removed."""
    hass.data.get("simple_uber_eats", {}).get("http_auth_failures", {}).pop(
        entry.entry_id, None
    )
