"""Opt-in legacy registry adoption with a fully unloaded, conflict-free source."""

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr
from homeassistant.helpers.entity import entity_sources

from .account import ROLES
from .const import DOMAIN


def adopt_selected_legacy(hass, entry):
    legacy_id = entry.data.get("legacy_entry_id")
    if not legacy_id:
        return False
    legacy = hass.config_entries.async_get_entry(legacy_id)
    if (
        legacy is None
        or legacy.domain != "uber_eats"
        or legacy.state is not ConfigEntryState.NOT_LOADED
    ):
        return False
    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, legacy_id)
    if not entities:
        return False
    name = legacy.data.get("account_name", "")
    if not isinstance(name, str):
        return False
    prefix = "uber_eats_" + name.replace(" ", "_") + "_"
    plans = []
    seen = set()
    loaded = entity_sources(hass)
    for entity in entities:
        role = (
            ROLES.get(entity.unique_id.removeprefix(prefix))
            if entity.unique_id.startswith(prefix)
            else None
        )
        if (
            entity.platform != "uber_eats"
            or role is None
            or role in seen
            or entity.entity_id in loaded
        ):
            return False
        new_id = f"{entry.entry_id}_{role}"
        if registry.async_get_entity_id(entity.domain, DOMAIN, new_id) is not None:
            return False
        seen.add(role)
        plans.append((entity, new_id))
    device_ids = {entity.device_id for entity, _ in plans if entity.device_id}
    if len(device_ids) > 1:
        return False
    devices = dr.async_get(hass)
    if device_ids:
        old_id = next(iter(device_ids))
        device = devices.async_get(old_id)
        conflict = devices.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
        if (
            device is None
            or device.config_entry_id != legacy_id
            or (conflict is not None and conflict.id != old_id)
        ):
            return False
    # Detach before moving ownership: HA removes old-owner entities that remain
    # attached to a device when its config entry changes.
    for entity, new_id in plans:
        detach = {"new_device_id": None} if entity.device_id else {}
        registry.async_update_entity_platform(
            entity.entity_id,
            DOMAIN,
            new_config_entry_id=entry.entry_id,
            new_unique_id=new_id,
            **detach,
        )
    if device_ids:
        devices.async_update_device(
            old_id,
            new_config_entry_id=entry.entry_id,
            merge_identifiers={(DOMAIN, entry.entry_id)},
        )
        for entity, _ in plans:
            if entity.device_id:
                registry.async_update_entity(
                    entity.entity_id, device_id=entity.device_id
                )
    return True


def apply_account_label(hass, entry):
    """Union our shared label with existing labels, touching only owned entities."""
    labels = lr.async_get(hass)
    label = labels.async_get_label_by_name("Uber Eats")
    if label is None:
        label = labels.async_create("Uber Eats", icon="mdi:food-takeout-box")
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.platform == DOMAIN and label.label_id not in entity.labels:
            registry.async_update_entity(
                entity.entity_id, labels=set(entity.labels) | {label.label_id}
            )
