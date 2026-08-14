"""Binary sensors for an Uber Eats account."""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ACCOUNT_NAME, DOMAIN
from .coordinator import (
    CONNECTION_AUTHENTICATION_FAILED,
    CONNECTION_CONNECTED,
)
from .entity import UberEatsCoordinatorEntity


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Uber Eats binary sensors."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]
    account_name = config_entry.data[CONF_ACCOUNT_NAME]
    async_add_entities(
        [
            UberEatsAccountConnected(
                coordinator, account_name, config_entry.entry_id
            ),
            UberEatsActiveOrder(coordinator, account_name, config_entry.entry_id),
        ]
    )


class UberEatsBinarySensorEntity(UberEatsCoordinatorEntity, BinarySensorEntity):
    """Base class for Uber Eats binary sensors."""


class UberEatsAccountConnected(UberEatsBinarySensorEntity):
    """Whether the latest conclusive request authenticated successfully."""

    _attr_translation_key = "account_connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator, account_name: str, entry_id: str) -> None:
        super().__init__(coordinator, account_name, entry_id, "account_connected")

    @property
    def available(self) -> bool:
        return self.coordinator.connection_state in (
            CONNECTION_CONNECTED,
            CONNECTION_AUTHENTICATION_FAILED,
        )

    @property
    def is_on(self) -> bool:
        return self.coordinator.connection_state == CONNECTION_CONNECTED

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attempt = self.coordinator.last_connection_attempt
        success = self.coordinator.last_connection_success
        attrs = {
            "connection_state": self.coordinator.connection_state,
            "last_connection_attempt": attempt.isoformat() if attempt else None,
            "last_connection_success": success.isoformat() if success else None,
        }
        return {key: value for key, value in attrs.items() if value is not None}


class UberEatsActiveOrder(UberEatsBinarySensorEntity):
    """Whether one or more active orders exist."""

    _attr_translation_key = "active_order"

    def __init__(self, coordinator, account_name: str, entry_id: str) -> None:
        super().__init__(coordinator, account_name, entry_id, "active_order")

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data.get("orders"))

    @property
    def extra_state_attributes(self) -> dict[str, int]:
        return {"active_orders_count": len(self.coordinator.data.get("orders", []))}
