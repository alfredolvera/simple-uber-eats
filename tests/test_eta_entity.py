"""Focused lifecycle tests for the entity-owned ETA countdown."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from importlib.util import module_from_spec, spec_from_file_location
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
        self.write_count = 0

    async def async_added_to_hass(self):
        return None

    async def async_will_remove_from_hass(self):
        return None

    def _handle_coordinator_update(self):
        return None

    def async_write_ha_state(self):
        self.write_count += 1


def _install_stubs(now_value: datetime):
    homeassistant = types.ModuleType("homeassistant")
    components = types.ModuleType("homeassistant.components")
    sensor_component = types.ModuleType("homeassistant.components.sensor")
    sensor_component.SensorEntity = type("SensorEntity", (), {})
    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = type("ConfigEntry", (), {})
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = type("HomeAssistant", (), {})
    core.callback = lambda function: function
    helpers = types.ModuleType("homeassistant.helpers")
    entity_platform = types.ModuleType("homeassistant.helpers.entity_platform")
    entity_platform.AddEntitiesCallback = object
    util = types.ModuleType("homeassistant.util")
    dt_module = types.ModuleType("homeassistant.util.dt")
    dt_module.now = lambda: now_value
    util.dt = dt_module
    modules = {
        "homeassistant": homeassistant,
        "homeassistant.components": components,
        "homeassistant.components.sensor": sensor_component,
        "homeassistant.config_entries": config_entries,
        "homeassistant.core": core,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.entity_platform": entity_platform,
        "homeassistant.util": util,
        "homeassistant.util.dt": dt_module,
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
_install_stubs(NOW)
package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_PATH)]
sys.modules[PACKAGE_NAME] = package
_load_module(PACKAGE_NAME, "const")
_load_module(PACKAGE_NAME, "eta_countdown")
_load_module(PACKAGE_NAME, "presentation")
protocol = _load_module(PACKAGE_NAME, "protocol")
entity_module = types.ModuleType(f"{PACKAGE_NAME}.entity")
entity_module.UberEatsCoordinatorEntity = FakeCoordinatorEntity
sys.modules[entity_module.__name__] = entity_module
sensor = _load_module(PACKAGE_NAME, "sensor")


class FakeHass:
    def __init__(self):
        self.created_tasks = []

    def async_create_task(self, coroutine, *, name):
        task = asyncio.create_task(coroutine, name=name)
        self.created_tasks.append(task)
        return task


class FakeCoordinator:
    def __init__(self, arrival: datetime | None):
        self.hass = FakeHass()
        self.connection_state = protocol.CONNECTION_CONNECTED
        self.data = self._data(arrival)

    @staticmethod
    def _data(arrival):
        return {"orders": [{"driver_eta": arrival}]} if arrival else {"orders": []}


class EtaEntityLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        pending = [
            task
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task() and not task.done()
        ]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def test_no_duplicate_task_and_no_http_activity(self):
        coordinator = FakeCoordinator(NOW + timedelta(minutes=1))
        entity = sensor.UberEatsETA(coordinator, "Account", "entry")
        await entity.async_added_to_hass()
        first_task = entity._countdown_task
        entity._ingest_eta()
        self.assertIs(first_task, entity._countdown_task)
        self.assertEqual(1, len(coordinator.hass.created_tasks))
        self.assertFalse(hasattr(coordinator, "async_request_refresh"))

    async def test_completion_clears_state_and_cancels_task(self):
        coordinator = FakeCoordinator(NOW + timedelta(minutes=1))
        entity = sensor.UberEatsETA(coordinator, "Account", "entry")
        await entity.async_added_to_hass()
        task = entity._countdown_task
        coordinator.data = {"orders": []}
        entity._handle_coordinator_update()
        await asyncio.gather(task, return_exceptions=True)
        self.assertIsNone(entity.native_value)
        self.assertIsNone(entity._countdown_task)

    async def test_temporary_coordinator_failure_keeps_local_countdown(self):
        coordinator = FakeCoordinator(NOW + timedelta(minutes=1))
        entity = sensor.UberEatsETA(coordinator, "Account", "entry")
        await entity.async_added_to_hass()
        coordinator.connection_state = protocol.CONNECTION_TEMPORARILY_UNAVAILABLE
        coordinator.data = {"orders": []}
        entity._handle_coordinator_update()
        self.assertIsNotNone(entity.native_value)
        self.assertIsNotNone(entity._countdown_task)

    async def test_unload_cancels_task(self):
        coordinator = FakeCoordinator(NOW + timedelta(minutes=1))
        entity = sensor.UberEatsETA(coordinator, "Account", "entry")
        await entity.async_added_to_hass()
        task = entity._countdown_task
        await entity.async_will_remove_from_hass()
        self.assertTrue(task.cancelled())
        self.assertIsNone(entity._countdown_task)

    async def test_countdown_state_and_attributes(self):
        coordinator = FakeCoordinator(NOW + timedelta(seconds=63))
        entity = sensor.UberEatsETA(coordinator, "Account", "entry")
        await entity.async_added_to_hass()
        self.assertEqual("01:03", entity.native_value)
        self.assertEqual(63, entity.extra_state_attributes["seconds_remaining"])
        self.assertEqual(
            NOW + timedelta(seconds=63),
            entity.extra_state_attributes["arrival_time"],
        )

    async def test_clean_names_keep_legacy_unique_keys(self):
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
