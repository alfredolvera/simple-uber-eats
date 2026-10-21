"""Behavior checks with minimal framework doubles; these are not HA integration tests."""

import importlib
import sys
import unittest
from datetime import UTC
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
PKG = "custom_components.simple_uber_eats"


def module(name, **attrs):
    result = sys.modules.setdefault(name, ModuleType(name))
    result.__dict__.update(attrs)
    return result


class Base:
    def __init__(self, coordinator=None):
        self.coordinator = coordinator

    async def async_added_to_hass(self):
        pass

    async def async_will_remove_from_hass(self):
        pass

    def _handle_coordinator_update(self):
        pass

    def async_write_ha_state(self):
        pass

    def __class_getitem__(cls, key):
        return cls

    def __init_subclass__(cls, **kwargs):
        pass


class Coordinator(Base):
    def __init__(self, hass, logger, **kwargs):
        self.hass = hass
        self.data = None
        self.last_update_success = True
        self.update_interval = kwargs.get("update_interval")

    async def async_shutdown(self):
        pass


class Failure(Exception):
    pass


class UpdateFailed(Failure):
    def __init__(self, *args, retry_after=None, **kwargs):
        super().__init__(*args)
        self.retry_after = retry_after


def install_doubles():
    package = module(PKG)
    package.__path__ = [str(ROOT / "custom_components/simple_uber_eats")]
    module("custom_components").__path__ = [str(ROOT / "custom_components")]
    module("homeassistant")
    module(
        "homeassistant.util",
        dt=SimpleNamespace(async_get_time_zone=AsyncMock(return_value=UTC)),
    )
    module("homeassistant.helpers")
    module("homeassistant.components")
    module(
        "homeassistant.exceptions",
        ConfigEntryAuthFailed=Failure,
        ConfigEntryNotReady=Failure,
    )
    module(
        "homeassistant.helpers.update_coordinator",
        DataUpdateCoordinator=Coordinator,
        CoordinatorEntity=Base,
        UpdateFailed=UpdateFailed,
    )
    module(
        "homeassistant.helpers.entity",
        DeviceInfo=dict,
        EntityCategory=SimpleNamespace(DIAGNOSTIC="diagnostic"),
    )
    module(
        "homeassistant.helpers.aiohttp_client",
        async_get_clientsession=lambda hass: object(),
    )
    module("homeassistant.core", callback=lambda fn: fn, HomeAssistant=Base)
    module(
        "homeassistant.config_entries",
        ConfigEntry=Base,
        ConfigFlow=Base,
        OptionsFlow=Base,
    )
    module(
        "homeassistant.const",
        Platform=SimpleNamespace(
            SENSOR="sensor",
            BINARY_SENSOR="binary_sensor",
            DEVICE_TRACKER="device_tracker",
        ),
    )
    module("homeassistant.helpers.entity_registry")
    module(
        "homeassistant.components.sensor",
        SensorEntity=Base,
        SensorDeviceClass=SimpleNamespace(TIMESTAMP="timestamp"),
    )
    module(
        "homeassistant.components.binary_sensor",
        BinarySensorEntity=Base,
        BinarySensorDeviceClass=SimpleNamespace(CONNECTIVITY="connectivity"),
    )
    module(
        "homeassistant.components.device_tracker",
        SourceType=SimpleNamespace(GPS="gps"),
        TrackerEntity=Base,
    )
    module("homeassistant.components.device_tracker.config_entry", TrackerEntity=Base)
    module(
        "homeassistant.helpers.event",
        async_track_time_interval=lambda *args: lambda: None,
    )


class DoubleCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.module_scope = patch.dict(sys.modules)
        self.module_scope.start()
        self.addCleanup(self.module_scope.stop)
        for name in list(sys.modules):
            if (
                name == PKG
                or name.startswith((PKG + ".", "homeassistant."))
                or name == "homeassistant"
            ):
                del sys.modules[name]
        install_doubles()


class RuntimeTests(DoubleCase):
    def setUp(self):
        super().setUp()
        self.runtime = importlib.import_module(PKG + ".runtime")
        self.model = importlib.import_module(PKG + ".model")
        self.web = importlib.import_module(PKG + ".web_session")
        self.entry = SimpleNamespace(
            entry_id="one",
            data={"sid": "first", "session_id": "second", "time_zone": "UTC"},
            options={},
        )
        self.updates = []

        def update(entry, **kwargs):
            self.updates.append(kwargs)
            if "data" in kwargs:
                entry.data = kwargs["data"]

        self.hass = SimpleNamespace(
            data={}, config_entries=SimpleNamespace(async_update_entry=update)
        )
        self.client = SimpleNamespace(
            credentials=self.web.WebSession("first", "second")
        )
        self.coordinator = self.runtime.UberCoordinator(
            self.hass, self.entry, self.client
        )

    async def test_polling_tracks_valid_snapshots(self):
        for snapshot, seconds in [
            (self.model.Snapshot(), 60),
            (self.model.Snapshot((self.model.Delivery(reference="x"),)), 15),
            (
                self.model.Snapshot(
                    (
                        self.model.Delivery(
                            reference="x", courier_point=self.model.GeoPoint(12, 13)
                        ),
                    )
                ),
                10,
            ),
        ]:

            async def read(snapshot=snapshot):
                return snapshot

            self.client.read_orders = read
            self.assertEqual(await self.coordinator._async_update_data(), snapshot)
            self.assertEqual(self.coordinator.update_interval.total_seconds(), seconds)

    async def test_auth_rejection_distinct_and_rotations_saved_even_on_failure(self):
        async def read():
            self.client.credentials = self.web.WebSession("rotated", "second")
            raise self.model.AuthRejected()

        self.client.read_orders = read
        with self.assertRaises(Failure):
            await self.coordinator._async_update_data()
        self.assertTrue(self.coordinator.auth_rejected)
        self.assertEqual(self.entry.data["sid"], "rotated")

    async def test_transient_and_throttle_backoff(self):
        async def read():
            raise self.model.Throttled()

        self.client.read_orders = read
        for seconds in (60, 120, 300, 300):
            with self.assertRaises(UpdateFailed):
                await self.coordinator._async_update_data()
            self.assertEqual(self.coordinator.update_interval.total_seconds(), seconds)
        self.assertFalse(self.coordinator.auth_rejected)

    async def test_http_auth_rejections_require_three_consecutive_polls(self):
        async def read():
            error = self.model.AuthRejected()
            error.http_status = 401
            raise error

        self.client.read_orders = read
        for _ in range(2):
            with self.assertRaises(UpdateFailed):
                await self.coordinator._async_update_data()
            self.assertFalse(self.coordinator.auth_rejected)
        with self.assertRaises(Failure):
            await self.coordinator._async_update_data()
        self.assertTrue(self.coordinator.auth_rejected)

    async def test_http_grace_counter_survives_recreated_coordinators(self):
        async def read():
            raise self.model.AuthRejected(http_status=401)

        self.client.read_orders = read
        for _ in range(2):
            coordinator = self.runtime.UberCoordinator(
                self.hass, self.entry, self.client
            )
            with self.assertRaises(UpdateFailed):
                await coordinator._async_update_data()
        coordinator = self.runtime.UberCoordinator(self.hass, self.entry, self.client)
        with self.assertRaises(Failure):
            await coordinator._async_update_data()
        self.assertTrue(coordinator.auth_rejected)

    async def test_http_counter_resets_after_nonauth_error_and_accounts_are_independent(
        self,
    ):
        async def rejected():
            raise self.model.AuthRejected(http_status=401)

        self.client.read_orders = rejected
        with self.assertRaises(UpdateFailed):
            await self.coordinator._async_update_data()
        other = SimpleNamespace(entry_id="other", data=self.entry.data, options={})
        second = self.runtime.UberCoordinator(self.hass, other, self.client)
        self.assertEqual(second._http_auth_failures, 0)

        async def temporary():
            raise self.model.ServiceUnavailable()

        self.client.read_orders = temporary
        with self.assertRaises(UpdateFailed):
            await self.coordinator._async_update_data()
        self.assertEqual(self.coordinator._http_auth_failures, 0)

    async def test_diagnostics_whitelist(self):
        diagnostics = importlib.import_module(PKG + ".diagnostics")
        self.entry.runtime_data = self.coordinator
        self.coordinator.data = self.model.Snapshot(
            (self.model.Delivery(reference="SECRET", courier="PRIVATE"),)
        )
        value = await diagnostics.async_get_config_entry_diagnostics(
            self.hass, self.entry
        )
        self.assertEqual(
            set(value),
            {
                "active_order_count",
                "poll_interval_seconds",
                "throttle_count",
                "successful_polls",
                "failed_polls",
                "last_poll_duration_seconds",
            },
        )
        self.assertNotIn("PRIVATE", str(value))
        self.assertNotIn("SECRET", str(value))

    async def test_privacy_defaults_off(self):
        sensor = importlib.import_module(PKG + ".sensor")
        self.coordinator.data = self.model.Snapshot(
            (
                self.model.Delivery(
                    courier="person", courier_phone="SECRET", courier_photo="URL"
                ),
            )
        )
        entity = sensor.UberSensor(self.coordinator, self.entry, "courier")
        self.assertEqual(entity.extra_state_attributes, {"active_orders_count": 1})
        self.entry.options = {"courier_phone": True, "courier_photo": True}
        self.assertEqual(
            entity.extra_state_attributes,
            {"active_orders_count": 1, "phone": "SECRET", "picture_url": "URL"},
        )

    async def test_registry_migration_only_same_entry(self):
        account = importlib.import_module(PKG + ".account")
        registry = SimpleNamespace(changes=[], async_get_entity_id=lambda *args: None)
        registry.async_update_entity = lambda entity_id, **kwargs: (
            registry.changes.append((entity_id, kwargs))
        )
        er = sys.modules["homeassistant.helpers.entity_registry"]
        er.async_get = lambda hass: registry
        er.async_entries_for_config_entry = lambda reg, entry_id: [
            SimpleNamespace(
                unique_id="uber_eats_A_B_driver_eta",
                entity_id="sensor.keep",
                domain="sensor",
                platform="simple_uber_eats",
            )
        ]
        self.entry.data["account_name"] = "A B"
        account.migrate_registry(self.hass, self.entry)
        self.assertEqual(
            registry.changes, [("sensor.keep", {"new_unique_id": "one_eta"})]
        )

    async def test_minimize_entry_preserves_label_and_zone(self):
        settings = importlib.import_module(PKG + ".settings")
        data = settings.clean_entry_data(
            {
                "sid": "one",
                "session_id": "two",
                "full_cookie": "SECRET",
                "account_name": "Name",
                "time_zone": "UTC",
                "irrelevant": "drop",
            },
            UTC,
        )
        self.assertEqual(
            data,
            {
                "sid": "one",
                "session_id": "two",
                "account_name": "Name",
                "time_zone": "UTC",
            },
        )


class LifecycleTests(DoubleCase):
    async def test_missing_credentials_require_reauth(self):
        settings = importlib.import_module(PKG + ".settings")
        with self.assertRaises(ValueError):
            settings.clean_entry_data({"sid": "", "session_id": ""}, UTC)

    async def test_tracker_unknown_and_cleanup(self):
        model = importlib.import_module(PKG + ".model")
        tracker = importlib.import_module(PKG + ".device_tracker")
        coordinator = SimpleNamespace(data=model.Snapshot(), last_update_success=True)
        entry = SimpleNamespace(entry_id="one", data={}, options={})
        entity = tracker.CourierTracker(coordinator, entry)
        self.assertIsNone(entity.latitude)
        cancellations = []
        entity._cancel_tick = lambda: cancellations.append(True)
        await entity.async_will_remove_from_hass()
        self.assertEqual(cancellations, [True])
        self.assertIsNone(entity._cancel_tick)
        self.assertIsNone(entity.longitude)


if __name__ == "__main__":
    unittest.main()
