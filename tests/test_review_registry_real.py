"""Reviewer's upgrade checks against real HA registries and coordinator APIs."""

import importlib
import tempfile
import unittest
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock

from homeassistant.config_entries import ConfigEntries, ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

PKG = "custom_components.simple_uber_eats"


class RealRegistryChecks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.hass = HomeAssistant(self.directory.name)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        self.hass.config_entries._initialized.set()
        self.hass.config_entries._store.set_load_empty()
        await self.hass.config_entries._store.async_load()
        self.entry = self.make_entry("one")
        self.other = self.make_entry("two")
        # Framework fixture registration only; production code uses public APIs.
        for entry in (self.entry, self.other):
            self.hass.config_entries._entries[entry.entry_id] = entry
        dr.async_setup(self.hass)
        await dr.async_load(self.hass, load_empty=True)
        self.registry = er.async_get(self.hass)
        await self.registry.async_load(load_empty=True)

    def make_entry(self, entry_id):
        return ConfigEntry(
            domain="simple_uber_eats",
            entry_id=entry_id,
            data={
                "account_name": "Example Account",
                "sid": "QA.test",
                "session_id": "test-session",
                "time_zone": "UTC",
            },
            options={},
            version=1,
            minor_version=1,
            source="user",
            state=ConfigEntryState.SETUP_IN_PROGRESS,
            title="Example Account",
            unique_id=None,
            discovery_keys=MappingProxyType({}),
            subentries_data=(),
        )

    async def asyncTearDown(self):
        for entry in (self.entry, self.other):
            entry._async_set_state(self.hass, ConfigEntryState.NOT_LOADED, None)
        await self.hass.async_stop(force=True)
        self.directory.cleanup()

    async def test_registry_upgrade_preserves_ids_and_labels_and_other_account(self):
        account = importlib.import_module(PKG + ".account")
        original = self.registry.async_get_or_create(
            "sensor",
            "simple_uber_eats",
            "uber_eats_Example_Account_driver_eta",
            config_entry=self.entry,
            suggested_object_id="my_custom_eta",
        )
        self.registry.async_update_entity(original.entity_id, labels={"my_label"})
        other = self.registry.async_get_or_create(
            "sensor",
            "simple_uber_eats",
            "uber_eats_Example_Account_restaurant_name",
            config_entry=self.other,
            suggested_object_id="other_restaurant",
        )
        account.migrate_registry(self.hass, self.entry)
        updated = self.registry.async_get(original.entity_id)
        self.assertEqual(updated.unique_id, "one_eta")
        self.assertEqual(updated.entity_id, "sensor.my_custom_eta")
        self.assertIn("my_label", updated.labels)
        self.assertEqual(
            self.registry.async_get(other.entity_id).unique_id,
            "uber_eats_Example_Account_restaurant_name",
        )
        account.migrate_registry(self.hass, self.entry)
        self.assertEqual(
            self.registry.async_get(original.entity_id).unique_id, "one_eta"
        )

    async def test_registry_conflict_leaves_existing_ids_intact(self):
        account = importlib.import_module(PKG + ".account")
        old = self.registry.async_get_or_create(
            "sensor",
            "simple_uber_eats",
            "uber_eats_Example_Account_driver_eta",
            config_entry=self.entry,
        )
        current = self.registry.async_get_or_create(
            "sensor", "simple_uber_eats", "one_eta", config_entry=self.entry
        )
        account.migrate_registry(self.hass, self.entry)
        self.assertEqual(
            self.registry.async_get(old.entity_id).unique_id,
            "uber_eats_Example_Account_driver_eta",
        )
        self.assertEqual(
            self.registry.async_get(current.entity_id).unique_id, "one_eta"
        )

    async def test_real_coordinator_refresh_uses_typed_snapshot_and_rotation(self):
        runtime = importlib.import_module(PKG + ".runtime")
        model = importlib.import_module(PKG + ".model")
        WebSession = importlib.import_module(PKG + ".web_session").WebSession
        payload = model.Snapshot(
            (model.Delivery(reference="r", status="Heading your way"),)
        )
        gateway = SimpleNamespace(
            credentials=WebSession("QA.rotated", "rotated-session"),
            read_orders=AsyncMock(return_value=payload),
        )
        coordinator = runtime.UberCoordinator(self.hass, self.entry, gateway)
        try:
            await coordinator.async_config_entry_first_refresh()
            self.assertIs(coordinator.data, payload)
            self.assertEqual(coordinator.update_interval.total_seconds(), 15)
            self.assertEqual(self.entry.data["sid"], "QA.rotated")
            self.assertEqual(self.entry.data["session_id"], "rotated-session")
        finally:
            await coordinator.async_shutdown()
