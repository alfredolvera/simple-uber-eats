"""Smooth entity-owned tracker for real Uber courier path points."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import math
import time
from typing import Any

from homeassistant.components.device_tracker import SourceType
from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ACCOUNT_NAME, DOMAIN
from .coordinator import CONNECTION_CONNECTED, UberEatsCoordinator
from .entity import UberEatsCoordinatorEntity
from .tracking_playback import (
    DEFAULT_PLAYBACK_DELAY_MS,
    PathPointBuffer,
    advance_playback_epoch,
)

PLAYBACK_INTERVAL_SECONDS = 1.0
MAX_ELAPSED_PER_TICK_MS = 2_000.0
COORDINATE_CHANGE_EPSILON = 0.0000001


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Uber Eats courier tracker."""
    coordinator: UberEatsCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    account_name = config_entry.data[CONF_ACCOUNT_NAME]
    async_add_entities(
        [UberEatsCourierTracker(coordinator, account_name, config_entry.entry_id)]
    )


class UberEatsCourierTracker(UberEatsCoordinatorEntity, TrackerEntity):
    """Play the primary order's real courier path locally at about 1 Hz."""

    _attr_translation_key = "courier_tracker"

    def __init__(
        self,
        coordinator: UberEatsCoordinator,
        account_name: str,
        entry_id: str,
    ) -> None:
        # Preserve the existing tracker registry entry.
        super().__init__(coordinator, account_name, entry_id, "driver_tracker")
        self._buffer = PathPointBuffer()
        self._current_order_id: str | None = None
        self._playback_epoch_ms: float | None = None
        self._published_latitude: float | None = None
        self._published_longitude: float | None = None
        self._latest_real_latitude: float | None = None
        self._latest_real_longitude: float | None = None
        self._latest_real_epoch_ms: float | None = None
        self._position_mode = "idle"
        self._order_active = False
        self._courier: str | None = None
        self._restaurant: str | None = None
        self._order_status: str | None = None
        self._order_id: str | None = None
        self._eta: datetime | None = None
        self._recovery_active = False
        self._telemetry_interrupted = False
        self._playback_task: asyncio.Task[None] | None = None
        self._generation = 0
        self._removed = False

    async def async_added_to_hass(self) -> None:
        """Register the coordinator listener, then ingest its current data."""
        await super().async_added_to_hass()
        self._ingest_coordinator_data()

    async def async_will_remove_from_hass(self) -> None:
        """Cancel playback before the entity or config entry disappears."""
        self._removed = True
        self._generation += 1
        task = self._cancel_playback_task()
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass
        await super().async_will_remove_from_hass()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Merge newly cached coordinator data without requesting a refresh."""
        self._ingest_coordinator_data()
        super()._handle_coordinator_update()

    def _ingest_coordinator_data(self) -> None:
        data = self.coordinator.data or {}
        orders = data.get("orders")
        if not isinstance(orders, list) or not orders:
            if (
                self._current_order_id is not None
                and self.coordinator.connection_state != CONNECTION_CONNECTED
            ):
                self._freeze_position()
            else:
                self._clear_tracking_state()
            return

        order = orders[0]
        if not isinstance(order, dict):
            self._freeze_position()
            return

        order_identity = self._order_identity(order)
        if order_identity != self._current_order_id:
            self._clear_tracking_state()
            self._current_order_id = order_identity

        self._order_active = True
        self._order_id = order.get("order_id")
        self._restaurant = order.get("restaurant_name") or self._restaurant
        self._order_status = order.get("order_status") or self._order_status
        self._eta = order.get("driver_eta")
        if order.get("driver_name") not in (
            None,
            "",
            "Unknown",
            "No Driver Assigned",
        ):
            self._courier = order["driver_name"]

        courier_latitude = order.get("driver_location_lat")
        courier_longitude = order.get("driver_location_lon")
        if not _valid_coordinates(courier_latitude, courier_longitude):
            self._telemetry_interrupted = True
            self._freeze_position()
            return

        raw_points = order.get("courier_path_points")
        if not isinstance(raw_points, list):
            raw_points = []

        should_rebase = self._telemetry_interrupted
        if should_rebase:
            self._buffer.reset()
        self._buffer.merge(raw_points, preserve_epoch=self._playback_epoch_ms)
        should_rebase = should_rebase or self._buffer.last_merge_had_discontinuity
        self._telemetry_interrupted = False
        newest = self._buffer.newest

        if newest is not None:
            self._latest_real_latitude = newest.latitude
            self._latest_real_longitude = newest.longitude
            self._latest_real_epoch_ms = newest.epoch
        elif self._published_latitude is None:
            self._latest_real_latitude = float(courier_latitude)
            self._latest_real_longitude = float(courier_longitude)
            self._latest_real_epoch_ms = None

        if should_rebase:
            self._rebase_playback(float(courier_latitude), float(courier_longitude))
            return

        if not self._buffer.has_timeline():
            if newest is not None:
                self._publish_direct(newest.latitude, newest.longitude)
            else:
                self._publish_direct(float(courier_latitude), float(courier_longitude))
            self._cancel_playback_task()
            return

        if self._playback_epoch_ms is None:
            if self._published_latitude is None:
                self._playback_epoch_ms = self._buffer.initial_playback_epoch(
                    DEFAULT_PLAYBACK_DELAY_MS
                )
                position = self._buffer.position_at(self._playback_epoch_ms)
                if position is not None:
                    self._published_latitude = position.latitude
                    self._published_longitude = position.longitude
                    self._position_mode = "interpolated"
            else:
                # A direct real point was already visible. Never jump backward to
                # manufacture the desired delay; newer batches will build it naturally.
                self._playback_epoch_ms = newest.epoch

        if (
            self._playback_epoch_ms is not None
            and newest.epoch > self._playback_epoch_ms
        ):
            self._ensure_playback_task()
        elif self._published_latitude is not None:
            self._position_mode = "frozen"
            self._cancel_playback_task()

    def _rebase_playback(
        self, courier_latitude: float, courier_longitude: float
    ) -> None:
        """Jump to the delayed start of the newest contiguous real segment."""
        self._generation += 1
        self._cancel_playback_task()
        self._recovery_active = False
        self._playback_epoch_ms = None
        newest = self._buffer.newest

        if not self._buffer.has_timeline():
            if newest is not None:
                self._publish_direct(newest.latitude, newest.longitude)
            else:
                self._publish_direct(courier_latitude, courier_longitude)
            return

        self._playback_epoch_ms = self._buffer.initial_playback_epoch(
            DEFAULT_PLAYBACK_DELAY_MS
        )
        position = self._buffer.position_at(self._playback_epoch_ms)
        if position is None:
            self._publish_direct(newest.latitude, newest.longitude)
            return

        self._published_latitude = position.latitude
        self._published_longitude = position.longitude
        self._position_mode = (
            "frozen" if self._playback_epoch_ms >= newest.epoch else "interpolated"
        )
        if self._playback_epoch_ms < newest.epoch:
            self._ensure_playback_task()

    def _publish_direct(self, latitude: float, longitude: float) -> None:
        self._published_latitude = latitude
        self._published_longitude = longitude
        self._latest_real_latitude = latitude
        self._latest_real_longitude = longitude
        self._position_mode = "direct"
        newest = self._buffer.newest
        if newest is not None:
            self._playback_epoch_ms = newest.epoch
            self._latest_real_epoch_ms = newest.epoch

    def _freeze_position(self) -> None:
        self._cancel_playback_task()
        self._position_mode = (
            "frozen" if self._published_latitude is not None else "idle"
        )

    def _clear_tracking_state(self) -> None:
        self._generation += 1
        self._cancel_playback_task()
        self._buffer.reset()
        self._current_order_id = None
        self._playback_epoch_ms = None
        self._published_latitude = None
        self._published_longitude = None
        self._latest_real_latitude = None
        self._latest_real_longitude = None
        self._latest_real_epoch_ms = None
        self._position_mode = "idle"
        self._order_active = False
        self._courier = None
        self._restaurant = None
        self._order_status = None
        self._order_id = None
        self._eta = None
        self._recovery_active = False
        self._telemetry_interrupted = False

    def _ensure_playback_task(self) -> None:
        if self._removed or (
            self._playback_task is not None and not self._playback_task.done()
        ):
            return
        generation = self._generation
        order_id = self._current_order_id
        self._playback_task = self.hass.async_create_task(
            self._async_playback_loop(generation, order_id),
            name=f"simple_uber_eats_courier_playback_{self.unique_id}",
        )

    def _cancel_playback_task(self) -> asyncio.Task[None] | None:
        task = self._playback_task
        self._playback_task = None
        if task is not None and not task.done():
            task.cancel()
            return task
        return None

    async def _async_playback_loop(
        self, generation: int, order_id: str | None
    ) -> None:
        last_tick = time.monotonic()
        current_task = asyncio.current_task()
        try:
            while (
                not self._removed
                and generation == self._generation
                and order_id == self._current_order_id
            ):
                await asyncio.sleep(PLAYBACK_INTERVAL_SECONDS)
                now = time.monotonic()
                elapsed_ms = min(
                    MAX_ELAPSED_PER_TICK_MS, max(0.0, (now - last_tick) * 1000.0)
                )
                last_tick = now

                newest = self._buffer.newest
                if newest is None or self._playback_epoch_ms is None:
                    return
                next_epoch, self._recovery_active, _rate = advance_playback_epoch(
                    self._playback_epoch_ms,
                    elapsed_ms,
                    newest.epoch,
                    self._recovery_active,
                )
                self._playback_epoch_ms = next_epoch
                position = self._buffer.position_at(next_epoch)
                if position is None:
                    return

                coordinate_changed = self._coordinate_changed(
                    position.latitude, position.longitude
                )
                old_mode = self._position_mode
                self._published_latitude = position.latitude
                self._published_longitude = position.longitude
                if next_epoch >= newest.epoch:
                    self._position_mode = "frozen"
                else:
                    self._position_mode = "interpolated"

                if (
                    not self._removed
                    and generation == self._generation
                    and order_id == self._current_order_id
                    and (coordinate_changed or old_mode != self._position_mode)
                ):
                    self.async_write_ha_state()

                if next_epoch >= newest.epoch:
                    return
        except asyncio.CancelledError:
            raise
        finally:
            if self._playback_task is current_task:
                self._playback_task = None

    def _coordinate_changed(self, latitude: float, longitude: float) -> bool:
        if self._published_latitude is None or self._published_longitude is None:
            return True
        return (
            abs(latitude - self._published_latitude) >= COORDINATE_CHANGE_EPSILON
            or abs(longitude - self._published_longitude)
            >= COORDINATE_CHANGE_EPSILON
        )

    @staticmethod
    def _order_identity(order: dict[str, Any]) -> str:
        order_id = order.get("order_id")
        if order_id:
            return str(order_id)
        return f"primary:{order.get('restaurant_name') or 'unknown'}"

    @property
    def source_type(self) -> SourceType:
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        return self._published_latitude

    @property
    def longitude(self) -> float | None:
        return self._published_longitude

    @property
    def location_name(self) -> None:
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        latest_timestamp = None
        if self._latest_real_epoch_ms is not None:
            try:
                latest_timestamp = datetime.fromtimestamp(
                    self._latest_real_epoch_ms / 1000.0, tz=timezone.utc
                ).isoformat()
            except (OSError, OverflowError, ValueError):
                pass
        attrs = {
            "tracking_active": self._published_latitude is not None,
            "position_mode": self._position_mode,
            "order_active": self._order_active,
            "courier": self._courier,
            "restaurant": self._restaurant,
            "order_status": self._order_status,
            "order_id": self._order_id,
            "eta": self._eta.isoformat() if self._eta else None,
            "latest_real_latitude": self._latest_real_latitude,
            "latest_real_longitude": self._latest_real_longitude,
            "latest_real_timestamp": latest_timestamp,
        }
        return {key: value for key, value in attrs.items() if value is not None}

    @property
    def state(self) -> str:
        return "idle" if self._published_latitude is None else (
            self._order_status or "unknown"
        )


def _valid_coordinates(latitude: Any, longitude: Any) -> bool:
    if isinstance(latitude, bool) or isinstance(longitude, bool):
        return False
    if not isinstance(latitude, (int, float)) or not isinstance(
        longitude, (int, float)
    ):
        return False
    return (
        math.isfinite(latitude)
        and math.isfinite(longitude)
        and -90 <= latitude <= 90
        and -180 <= longitude <= 180
    )
