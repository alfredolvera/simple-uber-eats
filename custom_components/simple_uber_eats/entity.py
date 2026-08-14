"""Shared entity helpers for Uber Eats."""
from __future__ import annotations

from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, LEGACY_DOMAIN


class UberEatsCoordinatorEntity(CoordinatorEntity):
    """Base class for coordinator-backed entities belonging to one account."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator,
        account_name: str,
        entry_id: str,
        unique_key: str,
    ) -> None:
        super().__init__(coordinator)
        account_key = account_name.replace(" ", "_")
        # Keep the released 2.x unique IDs so a supported registry move can retain
        # entity identity even though the integration platform has a new domain.
        self._attr_unique_id = f"{LEGACY_DOMAIN}_{account_key}_{unique_key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=f"{account_name} Uber Eats",
            manufacturer="Uber",
            model="Simple Uber Eats account",
        )

    async def async_added_to_hass(self) -> None:
        """Label the entity after its registry entry has been created."""
        await super().async_added_to_hass()
        label_id = getattr(self.coordinator, "label_id", None)
        if not label_id or not self.entity_id:
            return
        registry = er.async_get(self.hass)
        if (
            (entry := registry.async_get(self.entity_id))
            and label_id not in entry.labels
        ):
            registry.async_update_entity(
                self.entity_id,
                labels={*entry.labels, label_id},
            )
