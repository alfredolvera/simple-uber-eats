"""Account connectivity and authoritative active order presence."""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)

from .account import AccountEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(
        UberBinarySensor(entry.runtime_data, entry, role)
        for role in ("account_connected", "active_order")
    )


class UberBinarySensor(AccountEntity, BinarySensorEntity):
    def __init__(self, coordinator, entry, role):
        super().__init__(coordinator, entry, role)
        if role == "account_connected":
            self._attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    @property
    def available(self):
        return (
            self.role == "account_connected" and self.coordinator.auth_rejected
        ) or self.coordinator.last_update_success

    @property
    def is_on(self):
        if self.role == "account_connected":
            return not self.coordinator.auth_rejected
        return self.delivery is not None

    @property
    def extra_state_attributes(self):
        if self.role == "active_order":
            data = self.coordinator.data
            return {"active_orders_count": len(data.deliveries) if data else 0}
        attempted = self.coordinator.last_connection_attempt
        state = (
            "unknown"
            if attempted is None
            else "authentication_failed"
            if self.coordinator.auth_rejected
            else "connected"
            if self.coordinator.last_update_success
            else "temporarily_unavailable"
        )
        return {
            "connection_state": state,
            "last_connection_attempt": attempted,
            "last_connection_success": self.coordinator.last_connection_success,
        }
