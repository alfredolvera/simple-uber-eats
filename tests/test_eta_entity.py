"""Focused tests for the coordinator-backed ETA timestamp sensor."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from importlib.util import module_from_spec, spec_from_file_location
import inspect
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).parents[1]
PACKAGE_PATH = ROOT / "custom_components" / "simple_uber_eats"
PACKAGE_NAME = "eta_entity_package"


class FakeCoordinatorEntity:
    def __init__(self, coordinator, _account_name, _entry_id, unique_key):
        self.coordinator = coordinator
        self.hass = coordinator.hass
        self.unique_id = unique_key
        self.coordinator_update_count = 0
        self.write_count = 0

    def _handle_coordinator_update(self):
        self.coordinator_update_count += 1

    def async_write_ha_state(self):
        self.write_count += 1


def _install_stubs():
    homeassistant = types.ModuleType("homeassistant")
    components = types.ModuleType("homeassistant.components")
    sensor_component = types.ModuleType("homeassistant.components.sensor")
    sensor_component.SensorEntity = type("SensorEntity", (), {})
    sensor_component.SensorDeviceClass = type(
        "SensorDeviceClass", (), {"TIMESTAMP": "timestamp"}
    )
    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = type("ConfigEntry", (), {})
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = type("HomeAssistant", (), {})
    helpers = types.ModuleType("homeassistant.helpers")
    entity_platform = types.ModuleType("homeassistant.helpers.entity_platform")
    entity_platform.AddEntitiesCallback = object
    modules = {
        "homeassistant": homeassistant,
        "homeassistant.components": components,
        "homeassistant.components.sensor": sensor_component,
        "homeassistant.config_entries": config_entries,
        "homeassistant.core": core,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.entity_platform": entity_platform,
    }
    sys.modules.update(modules)


def _load_module(package_name: str, name: str):
    spec = spec_from_file_location(
        f"{package_name}.{name}", PACKAGE_PATH / f"{name}.py"
    )
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


NOW = datetime(2026, 8, 14, 15, 0, tzinfo=timezone.utc)
_install_stubs()
package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_PATH)]
sys.modules[PACKAGE_NAME] = package
_load_module(PACKAGE_NAME, "const")
_load_module(PACKAGE_NAME, "presentation")
entity_module = types.ModuleType(f"{PACKAGE_NAME}.entity")
entity_module.UberEatsCoordinatorEntity = FakeCoordinatorEntity
sys.modules[entity_module.__name__] = entity_module
sensor = _load_module(PACKAGE_NAME, "sensor")


class FakeHass:
    def __init__(self):
        self.created_tasks = []

    def async_create_task(self, coroutine, *, name):
        self.created_tasks.append((coroutine, name))
        raise AssertionError("ETA sensor must not create a local task")


class FakeCoordinator:
    def __init__(self, arrival: datetime | None):
        self.hass = FakeHass()
        self.data = self._data(arrival)

    @staticmethod
    def _data(arrival):
        return {"orders": [{"driver_eta": arrival}]} if arrival else {"orders": []}


class EtaEntityTests(unittest.TestCase):
    def test_timestamp_state_and_attributes(self):
        arrival = NOW + timedelta(minutes=12)
        coordinator = FakeCoordinator(arrival)
        entity = sensor.UberEatsETA(coordinator, "Account", "entry")

        self.assertEqual("timestamp", entity._attr_device_class)
        self.assertEqual(arrival, entity.native_value)
        self.assertEqual(arrival, entity.extra_state_attributes["arrival_time"])
        self.assertNotIn("seconds_remaining", entity.extra_state_attributes)

    def test_completion_clears_timestamp(self):
        coordinator = FakeCoordinator(NOW + timedelta(minutes=12))
        entity = sensor.UberEatsETA(coordinator, "Account", "entry")

        coordinator.data = {"orders": []}
        entity._handle_coordinator_update()

        self.assertIsNone(entity.native_value)
        self.assertNotIn("arrival_time", entity.extra_state_attributes)
        self.assertEqual(1, entity.coordinator_update_count)

    def test_invalid_or_naive_timestamp_is_not_published(self):
        coordinator = FakeCoordinator(datetime(2026, 8, 14, 15, 12))
        entity = sensor.UberEatsETA(coordinator, "Account", "entry")
        self.assertIsNone(entity.native_value)

        coordinator.data = {"orders": [{"driver_eta": "3:12 PM"}]}
        self.assertIsNone(entity.native_value)

    def test_no_local_task_or_per_second_state_write(self):
        coordinator = FakeCoordinator(NOW + timedelta(minutes=12))
        entity = sensor.UberEatsETA(coordinator, "Account", "entry")

        coordinator.data = FakeCoordinator._data(NOW + timedelta(minutes=15))
        entity._handle_coordinator_update()

        self.assertEqual(NOW + timedelta(minutes=15), entity.native_value)
        self.assertEqual(1, entity.coordinator_update_count)
        self.assertEqual([], coordinator.hass.created_tasks)
        self.assertEqual(0, entity.write_count)
        source = inspect.getsource(sensor.UberEatsETA)
        self.assertNotIn("async_create_task", source)
        self.assertNotIn("async_write_ha_state", source)
        self.assertNotIn("_countdown_task", source)
        self.assertNotIn("sleep", source)

    def test_clean_names_keep_legacy_unique_keys(self):
        coordinator = FakeCoordinator(NOW + timedelta(minutes=1))
        restaurant = sensor.UberEatsRestaurant(coordinator, "Account", "entry")
        courier = sensor.UberEatsCourier(coordinator, "Account", "entry")
        eta_entity = sensor.UberEatsETA(coordinator, "Account", "entry")
        self.assertEqual("restaurant", restaurant._attr_translation_key)
        self.assertEqual("restaurant_name", restaurant.unique_id)
        self.assertEqual("courier", courier._attr_translation_key)
        self.assertEqual("driver_name", courier.unique_id)
        self.assertEqual("eta", eta_entity._attr_translation_key)
        self.assertEqual("driver_eta", eta_entity.unique_id)


if __name__ == "__main__":
    unittest.main()
