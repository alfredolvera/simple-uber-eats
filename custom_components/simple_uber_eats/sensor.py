"""Native sensors for the primary active Uber Eats order."""
from __future__ import annotations

import asyncio
from datetime import datetime
import time
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import CONF_ACCOUNT_NAME, DOMAIN
from .entity import UberEatsCoordinatorEntity
from .eta_countdown import CountdownClock, format_countdown
from .presentation import order_count_attributes, primary_order, restaurant_attributes
from .protocol import CONNECTION_CONNECTED

COUNTDOWN_INTERVAL_SECONDS = 1.0


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
    """Locally count down to the primary order's authoritative ETA."""

    _attr_translation_key = "eta"

    def __init__(self, coordinator, account_name: str, entry_id: str) -> None:
        super().__init__(coordinator, account_name, entry_id, "driver_eta")
        self._clock = CountdownClock()
        self._seconds_remaining: int | None = None
        self._countdown_task: asyncio.Task[None] | None = None
        self._generation = 0
        self._removed = False

    async def async_added_to_hass(self) -> None:
        """Initialize from coordinator data after registration."""
        await super().async_added_to_hass()
        self._ingest_eta()

    async def async_will_remove_from_hass(self) -> None:
        """Cancel the local timer before unload or config-entry reload."""
        self._removed = True
        self._generation += 1
        task = self._cancel_countdown()
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass
        await super().async_will_remove_from_hass()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Rebase from cached coordinator data without requesting Uber."""
        self._ingest_eta()
        super()._handle_coordinator_update()

    def _ingest_eta(self) -> None:
        order = _primary_order(self.coordinator)
        if (
            order is None
            and self._clock.arrival_time is not None
            and self.coordinator.connection_state != CONNECTION_CONNECTED
        ):
            if self._seconds_remaining and self._seconds_remaining > 0:
                self._ensure_countdown()
            return
        arrival = order.get("driver_eta") if order else None
        if not isinstance(arrival, datetime) or arrival.tzinfo is None:
            self._clear_countdown()
            return

        self._seconds_remaining = self._clock.rebase(
            arrival,
            wall_now=dt_util.now(),
            monotonic_now=time.monotonic(),
        )
        if self._seconds_remaining > 0:
            self._ensure_countdown()
        else:
            self._cancel_countdown()

    def _clear_countdown(self) -> None:
        self._generation += 1
        self._cancel_countdown()
        self._clock.clear()
        self._seconds_remaining = None

    def _ensure_countdown(self) -> None:
        if self._removed or (
            self._countdown_task is not None and not self._countdown_task.done()
        ):
            return
        generation = self._generation
        self._countdown_task = self.hass.async_create_task(
            self._async_countdown_loop(generation),
            name=f"simple_uber_eats_eta_countdown_{self.unique_id}",
        )

    def _cancel_countdown(self) -> asyncio.Task[None] | None:
        task = self._countdown_task
        self._countdown_task = None
        if task is not None and not task.done():
            task.cancel()
            return task
        return None

    async def _async_countdown_loop(self, generation: int) -> None:
        current_task = asyncio.current_task()
        try:
            while not self._removed and generation == self._generation:
                await asyncio.sleep(COUNTDOWN_INTERVAL_SECONDS)
                remaining = self._clock.remaining(time.monotonic())
                if remaining is None:
                    return
                if remaining != self._seconds_remaining:
                    self._seconds_remaining = remaining
                    if not self._removed and generation == self._generation:
                        self.async_write_ha_state()
                if remaining == 0:
                    return
        except asyncio.CancelledError:
            raise
        finally:
            if self._countdown_task is current_task:
                self._countdown_task = None

    @property
    def native_value(self) -> str | None:
        if self._seconds_remaining is None:
            return None
        return format_countdown(self._seconds_remaining)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = _active_orders_attribute(self.coordinator)
        if self._clock.arrival_time is not None:
            attrs["arrival_time"] = self._clock.arrival_time
        if self._seconds_remaining is not None:
            attrs["seconds_remaining"] = self._seconds_remaining
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
