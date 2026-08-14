"""Supported registry transition from the legacy integration domain."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_ACCOUNT_NAME,
    CONF_LEGACY_ENTRY_ID,
    CONF_LEGACY_REGISTRY_MIGRATION,
    DOMAIN,
    LEGACY_DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

REGISTRY_MIGRATED = "migrated"
REGISTRY_MIGRATION_NOT_NEEDED = "not_needed"
REGISTRY_MIGRATION_UNAVAILABLE = "unavailable"

RETAINED_ENTITY_UNIQUE_ID_KEYS = {
    "account_connected",
    "active_order",
    "driver_eta",
    "driver_name",
    "driver_tracker",
    "order_status",
    "restaurant_name",
}

RETIRED_ENTITY_UNIQUE_ID_KEYS = {
    "delivery_fees",
    "driver_ett",
    "driver_latitude",
    "driver_location",
    "driver_location_address",
    "driver_location_county",
    "driver_location_quarter",
    "driver_location_street",
    "driver_location_suburb",
    "driver_longitude",
    "latest_arrival",
    "order_history",
    "order_id",
    "order_stage",
    "order_status_description",
    "total_deliveries",
    "total_delivery_fees",
    "total_spent",
}


def _record_result(hass: HomeAssistant, entry: ConfigEntry, result: str) -> str:
    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, CONF_LEGACY_REGISTRY_MIGRATION: result},
    )
    return result


def migrate_legacy_registry(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Move unloaded legacy registry entries with Home Assistant public APIs.

    The old config entry itself is deliberately retained. If its entities are still
    loaded, Home Assistant refuses the platform move and the new integration falls
    back to creating normal new-domain registry entries.
    """
    if result := entry.data.get(CONF_LEGACY_REGISTRY_MIGRATION):
        return result if isinstance(result, str) else None
    legacy_entry_id = entry.data.get(CONF_LEGACY_ENTRY_ID)
    if not isinstance(legacy_entry_id, str):
        return None
    legacy_entry = hass.config_entries.async_get_entry(legacy_entry_id)
    if legacy_entry is None or legacy_entry.domain != LEGACY_DOMAIN:
        return _record_result(hass, entry, REGISTRY_MIGRATION_UNAVAILABLE)

    account_name = entry.data.get(CONF_ACCOUNT_NAME)
    if not isinstance(account_name, str) or not account_name:
        return _record_result(hass, entry, REGISTRY_MIGRATION_UNAVAILABLE)
    account_key = account_name.replace(" ", "_")
    expected_unique_ids = {
        f"{LEGACY_DOMAIN}_{account_key}_{key}"
        for key in RETAINED_ENTITY_UNIQUE_ID_KEYS | RETIRED_ENTITY_UNIQUE_ID_KEYS
    }

    entity_registry = er.async_get(hass)
    legacy_entities = [
        entity
        for entity in er.async_entries_for_config_entry(
            entity_registry, legacy_entry_id
        )
        if entity.platform == LEGACY_DOMAIN
    ]
    if any(entity.unique_id not in expected_unique_ids for entity in legacy_entities):
        _LOGGER.warning(
            "Skipping legacy registry migration for %s because unexpected legacy "
            "entities are present",
            entry.title,
        )
        return _record_result(hass, entry, REGISTRY_MIGRATION_UNAVAILABLE)

    device_registry = dr.async_get(hass)
    legacy_devices = [
        device
        for device in dr.async_entries_for_config_entry(
            device_registry, legacy_entry_id
        )
        if (LEGACY_DOMAIN, legacy_entry_id) in device.identifiers
    ]
    if len(legacy_devices) > 1:
        _LOGGER.warning(
            "Skipping legacy registry migration for %s because multiple account "
            "devices were found",
            entry.title,
        )
        return _record_result(hass, entry, REGISTRY_MIGRATION_UNAVAILABLE)
    legacy_device = legacy_devices[0] if legacy_devices else None
    if any(
        entity.device_id is not None
        and (legacy_device is None or entity.device_id != legacy_device.id)
        for entity in legacy_entities
    ):
        _LOGGER.warning(
            "Skipping legacy registry migration for %s because entity/device "
            "ownership is ambiguous",
            entry.title,
        )
        return _record_result(hass, entry, REGISTRY_MIGRATION_UNAVAILABLE)

    if not legacy_entities and legacy_device is None:
        return _record_result(hass, entry, REGISTRY_MIGRATION_NOT_NEEDED)

    migrated_entities = []
    try:
        for entity in legacy_entities:
            entity_registry.async_update_entity_platform(
                entity.entity_id,
                DOMAIN,
                new_config_entry_id=entry.entry_id,
            )
            migrated_entities.append(entity)
        if legacy_device is not None:
            moved_device = device_registry.async_update_device(
                legacy_device.id,
                new_config_entry_id=entry.entry_id,
                new_identifiers={(DOMAIN, entry.entry_id)},
            )
            if moved_device is None:
                raise HomeAssistantError("Legacy account device could not be moved")
    except (HomeAssistantError, ValueError) as err:
        for entity in reversed(migrated_entities):
            try:
                entity_registry.async_update_entity_platform(
                    entity.entity_id,
                    LEGACY_DOMAIN,
                    new_config_entry_id=legacy_entry_id,
                )
            except (HomeAssistantError, ValueError):
                _LOGGER.exception(
                    "Could not roll back legacy entity %s after migration failure",
                    entity.entity_id,
                )
        _LOGGER.warning(
            "Legacy registry continuity was unavailable for %s: %s",
            entry.title,
            err,
        )
        return _record_result(hass, entry, REGISTRY_MIGRATION_UNAVAILABLE)

    return _record_result(hass, entry, REGISTRY_MIGRATED)
