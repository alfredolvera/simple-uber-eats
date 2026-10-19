"""Entity identity and migration using Home Assistant's public registry API."""

from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

ROLES = {
    "account_connected": "account_connected",
    "active_order": "active_order",
    "order_status": "status",
    "driver_eta": "eta",
    "restaurant_name": "restaurant",
    "driver_name": "courier",
    "driver_tracker": "courier_tracker",
}


def migrate_registry(hass, entry):
    """Update unique IDs without replacing entity IDs or crossing entry bounds."""
    registry = er.async_get(hass)
    name = entry.data.get("account_name", "")
    if not isinstance(name, str):
        name = ""
    prefix = "uber_eats_" + name.replace(" ", "_") + "_"
    for registered in er.async_entries_for_config_entry(registry, entry.entry_id):
        if registered.platform != DOMAIN or not registered.unique_id.startswith(prefix):
            continue
        role = ROLES.get(registered.unique_id[len(prefix) :])
        if role is None:
            continue
        unique_id = f"{entry.entry_id}_{role}"
        existing = registry.async_get_entity_id(registered.domain, DOMAIN, unique_id)
        if existing is None or existing == registered.entity_id:
            registry.async_update_entity(registered.entity_id, new_unique_id=unique_id)


class AccountEntity(CoordinatorEntity):
    """All entities read the same typed snapshot."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, role):
        super().__init__(coordinator)
        self.entry = entry
        self.role = role
        self._attr_unique_id = f"{entry.entry_id}_{role}"
        self._attr_translation_key = role
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data.get("account_name", "Uber Eats") + " Uber Eats",
            manufacturer="Uber",
            model="Uber Eats account",
        )

    @property
    def delivery(self):
        return self.coordinator.data.primary if self.coordinator.data else None

    @property
    def courier_name(self):
        name = self.delivery.courier if self.delivery else None
        return (
            None
            if not name or name.strip().casefold() in {"unknown", "no driver assigned"}
            else name
        )

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        from .compatibility import apply_account_label

        apply_account_label(self.hass, self.entry)
