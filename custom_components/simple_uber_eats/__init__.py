"""The Uber Eats integration."""
from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr

from .coordinator import UberEatsCoordinator
from .const import (
    CONF_ACCOUNT_NAME,
    CONF_FULL_COOKIE,
    CONF_SESSION_ID,
    CONF_SID,
    CONF_TIME_ZONE,
    DOMAIN,
    LEGACY_DOMAIN,
)
from .migration import RETIRED_ENTITY_UNIQUE_ID_KEYS, migrate_legacy_registry
from .protocol import SessionCredentials, rotated_entry_data

PLATFORMS = (Platform.SENSOR, Platform.BINARY_SENSOR, Platform.DEVICE_TRACKER)


def _async_prepare_entity_registry(
    hass: HomeAssistant, entry: ConfigEntry, account_name: str
) -> str:
    """Create the shared label and remove only known retired entry entities."""
    label_registry = lr.async_get(hass)
    label = label_registry.async_get_label_by_name("Uber Eats")
    if label is None:
        label = label_registry.async_create(
            name="Uber Eats",
            color="black",
            icon="mdi:food-takeout-box",
        )

    account_key = account_name.replace(" ", "_")
    retired_unique_ids = {
        f"{LEGACY_DOMAIN}_{account_key}_{key}"
        for key in RETIRED_ENTITY_UNIQUE_ID_KEYS
    }
    entity_registry = er.async_get(hass)
    for entity_entry in er.async_entries_for_config_entry(
        entity_registry, entry.entry_id
    ):
        if (
            entity_entry.platform == DOMAIN
            and entity_entry.unique_id in retired_unique_ids
        ):
            entity_registry.async_remove(entity_entry.entity_id)

    return label.label_id


def _async_apply_label_to_entry_entities(
    hass: HomeAssistant, entry: ConfigEntry, label_id: str
) -> None:
    """Union the integration label into every entity for this config entry."""
    entity_registry = er.async_get(hass)
    for entity_entry in er.async_entries_for_config_entry(
        entity_registry, entry.entry_id
    ):
        if entity_entry.platform != DOMAIN or label_id in entity_entry.labels:
            continue
        entity_registry.async_update_entity(
            entity_entry.entity_id,
            labels={*entity_entry.labels, label_id},
        )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Create the account coordinator and its native entity platforms."""
    credentials = SessionCredentials.from_stored(
        entry.data[CONF_SID],
        entry.data[CONF_SESSION_ID],
        entry.data.get(CONF_FULL_COOKIE),
    )
    normalized_entry_data = rotated_entry_data(entry.data, credentials)
    if normalized_entry_data != dict(entry.data):
        hass.config_entries.async_update_entry(entry, data=normalized_entry_data)

    coordinator = UberEatsCoordinator(
        hass=hass,
        entry_id=entry.entry_id,
        sid=credentials.sid,
        session_id=credentials.session_id,
        account_name=entry.data[CONF_ACCOUNT_NAME],
        time_zone=entry.data[CONF_TIME_ZONE],
        full_cookie=credentials.header(),
    )
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryAuthFailed:
        raise
    except Exception as err:
        raise ConfigEntryNotReady("Initial Uber Eats refresh failed") from err

    migrate_legacy_registry(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    label_id = _async_prepare_entity_registry(
        hass, entry, entry.data[CONF_ACCOUNT_NAME]
    )
    coordinator.label_id = label_id
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_apply_label_to_entry_entities(hass, entry, label_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload native platforms before discarding the account coordinator."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unloaded
