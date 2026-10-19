"""Render locally buffered real GPS samples without extra cloud polling."""

from datetime import UTC, datetime, timedelta
from itertools import pairwise
from time import time

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_interval

from .account import AccountEntity
from .motion import RouteBuffer

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([CourierTracker(entry.runtime_data, entry)])


class CourierTracker(AccountEntity, TrackerEntity):
    """A one-second timer only publishes local interpolation."""

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "courier_tracker")
        self._route = RouteBuffer(delay=12)
        self._latest_fix = None
        self._order_reference = None
        self._ingest_delivery()
        self._position = self._route.position(time())
        self._cancel_tick = None

    @property
    def source_type(self):
        return SourceType.GPS

    @property
    def latitude(self):
        return self._position.latitude if self._position else None

    @property
    def longitude(self):
        return self._position.longitude if self._position else None

    @property
    def location_accuracy(self):
        return 0

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self._cancel_tick = async_track_time_interval(
            self.hass, self._tick, timedelta(seconds=1)
        )

    @callback
    def _tick(self, now):
        position = self._route.position(time())
        if position != self._position:
            self._position = position
            self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self):
        if self.coordinator.last_update_success:
            self._ingest_delivery()
        self._position = self._route.position(time())
        super()._handle_coordinator_update()

    async def async_will_remove_from_hass(self):
        if self._cancel_tick:
            self._cancel_tick()
            self._cancel_tick = None
        self._route.reset()
        self._position = None
        self._latest_fix = None
        self._order_reference = None
        await super().async_will_remove_from_hass()

    def _ingest_delivery(self):
        delivery = self.delivery
        reference = delivery.reference if delivery else None
        if reference != self._order_reference:
            self._latest_fix = None
            self._order_reference = reference
        self._route.ingest(delivery)
        if delivery and delivery.route:
            candidate = max(delivery.route, key=lambda fix: fix.at)
            if self._latest_fix is None or candidate.at >= self._latest_fix.at:
                self._latest_fix = candidate

    @property
    def state(self):
        return (
            (self.delivery.status or "unknown")
            if self._position and self.delivery
            else "idle"
        )

    @property
    def extra_state_attributes(self):
        delivery = self.delivery
        mode = "idle"
        if self._position is not None:
            mode = "frozen"
            if (
                delivery
                and self._latest_fix
                and self._position != self._latest_fix.point
            ):
                samples = sorted(delivery.route, key=lambda fix: fix.at)
                target = time() - 12
                if any(
                    left.at <= target < right.at and right.at - left.at <= 60
                    for left, right in pairwise(samples)
                ):
                    mode = "playback"
        result = {
            "tracking_active": self._position is not None,
            "order_active": delivery is not None,
            "position_mode": mode,
        }
        if delivery:
            result.update(
                courier=self.courier_name,
                restaurant=delivery.restaurant,
                order_status=delivery.status,
                order_id=delivery.reference,
                eta=delivery.eta.isoformat() if delivery.eta else None,
            )
            if self._latest_fix:
                result.update(
                    latest_real_latitude=self._latest_fix.point.latitude,
                    latest_real_longitude=self._latest_fix.point.longitude,
                    latest_real_timestamp=datetime.fromtimestamp(
                        self._latest_fix.at, UTC
                    ).isoformat(),
                )
            elif delivery.courier_point:
                result.update(
                    latest_real_latitude=delivery.courier_point.latitude,
                    latest_real_longitude=delivery.courier_point.longitude,
                )
        return result
