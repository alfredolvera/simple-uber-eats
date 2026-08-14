"""Native sensors for the primary active Uber Eats order."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ACCOUNT_NAME, DOMAIN
from .entity import UberEatsCoordinatorEntity
from .presentation import order_count_attributes, primary_order, restaurant_attributes


def _primary_order(coordinator) -> dict[str, Any] | None:
    """Return the deterministic primary active order."""
    return primary_order(coordinator.data)


def _active_orders_attribute(coordinator) -> dict[str, int]:
    """Return a compact multi-order diagnostic attribute."""
    return order_count_attributes(coordinator.data)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the native Uber Eats sensors."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]
    account_name = config_entry.data[CONF_ACCOUNT_NAME]
    async_add_entities(
        [
            UberEatsOrderStatus(coordinator, account_name, config_entry.entry_id),
            UberEatsETA(coordinator, account_name, config_entry.entry_id),
            UberEatsRestaurant(coordinator, account_name, config_entry.entry_id),
            UberEatsCourier(coordinator, account_name, config_entry.entry_id),
        ]
    )


class UberEatsSensorEntity(UberEatsCoordinatorEntity, SensorEntity):
    """Base class for Uber Eats sensors."""


class UberEatsOrderStatus(UberEatsSensorEntity):
    """Uber's visible status for the primary active order."""

    _attr_translation_key = "order_status"

    def __init__(self, coordinator, account_name: str, entry_id: str) -> None:
        super().__init__(coordinator, account_name, entry_id, "order_status")

    @property
    def native_value(self) -> str | None:
        order = _primary_order(self.coordinator)
        return order.get("order_status") if order else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        order = _primary_order(self.coordinator)
        attrs: dict[str, Any] = _active_orders_attribute(self.coordinator)
        if order:
            attrs.update(
                {
                    "description": order.get("order_status_description"),
                    "order_id": order.get("order_id"),
                }
            )
        return {key: value for key, value in attrs.items() if value is not None}


class UberEatsETA(UberEatsSensorEntity):
    """Expose the primary order's authoritative arrival timestamp."""

    _attr_translation_key = "eta"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator, account_name: str, entry_id: str) -> None:
        super().__init__(coordinator, account_name, entry_id, "driver_eta")

    @property
    def native_value(self) -> datetime | None:
        order = _primary_order(self.coordinator)
        arrival = order.get("driver_eta") if order else None
        if not isinstance(arrival, datetime) or arrival.tzinfo is None:
            return None
        return arrival

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = _active_orders_attribute(self.coordinator)
        if (arrival := self.native_value) is not None:
            attrs["arrival_time"] = arrival
        return attrs


class UberEatsRestaurant(UberEatsSensorEntity):
    """Restaurant for the primary active order."""

    _attr_translation_key = "restaurant"

    def __init__(self, coordinator, account_name: str, entry_id: str) -> None:
        # Preserve the existing Restaurant Name entity registry entry.
        super().__init__(coordinator, account_name, entry_id, "restaurant_name")

    @property
    def native_value(self) -> str | None:
        order = _primary_order(self.coordinator)
        return order.get("restaurant_name") if order else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return restaurant_attributes(self.coordinator.data)


class UberEatsCourier(UberEatsSensorEntity):
    """Assigned courier for the primary active order."""

    _attr_translation_key = "courier"

    def __init__(self, coordinator, account_name: str, entry_id: str) -> None:
        # Preserve the existing Driver Name entity registry entry.
        super().__init__(coordinator, account_name, entry_id, "driver_name")

    @property
    def native_value(self) -> str | None:
        order = _primary_order(self.coordinator)
        if not order:
            return None
        driver_name = order.get("driver_name")
        if driver_name in (None, "", "Unknown", "No Driver Assigned"):
            return None
        return driver_name

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        order = _primary_order(self.coordinator)
        attrs: dict[str, Any] = _active_orders_attribute(self.coordinator)
        if order:
            attrs.update(
                {
                    "picture_url": order.get("driver_picture_url"),
                    "phone": order.get("driver_phone_formatted"),
                }
            )
        return {key: value for key, value in attrs.items() if value not in (None, "")}
