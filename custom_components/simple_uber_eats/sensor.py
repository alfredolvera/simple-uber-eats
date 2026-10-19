"""Native order sensors; ETA is an aware timestamp supplied by the decoder."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity

from .account import AccountEntity
from .const import PHONE_OPTION, PHOTO_OPTION

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(
        UberSensor(entry.runtime_data, entry, role)
        for role in ("status", "eta", "restaurant", "courier")
    )


class UberSensor(AccountEntity, SensorEntity):
    def __init__(self, coordinator, entry, role):
        super().__init__(coordinator, entry, role)
        if role == "eta":
            self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self):
        delivery = self.delivery
        if delivery is None:
            return None
        if self.role == "courier":
            return self.courier_name
        return getattr(delivery, self.role)

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data
        result = {"active_orders_count": len(data.deliveries) if data else 0}
        delivery = self.delivery
        if delivery is None:
            return result
        if self.role == "status":
            result.update(order_id=delivery.reference, description=delivery.status)
        elif self.role == "eta":
            result["arrival_time"] = delivery.eta
        elif self.role == "restaurant" and delivery.restaurant_point:
            result.update(
                latitude=delivery.restaurant_point.latitude,
                longitude=delivery.restaurant_point.longitude,
            )
        elif self.role == "courier":
            if self.entry.options.get(PHONE_OPTION, False) and delivery.courier_phone:
                result["phone"] = delivery.courier_phone
            if self.entry.options.get(PHOTO_OPTION, False) and delivery.courier_photo:
                result["picture_url"] = delivery.courier_photo
        return result
