"""Tests for the pre-release integration-domain transition."""
from __future__ import annotations

from dataclasses import dataclass, field
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import types
import unittest

ROOT = Path(__file__).parents[1]
PACKAGE_PATH = ROOT / "custom_components" / "simple_uber_eats"
PACKAGE_NAME = "domain_migration_package"


class _Required:
    def __init__(self, key, default=None):
        self.key = key
        self.default = default

    def __hash__(self):
        return hash(self.key)


class FakeConfigFlow:
    def __init_subclass__(cls, domain=None, **kwargs):
        super().__init_subclass__(**kwargs)
        cls.registered_domain = domain

    def __init__(self):
        self.context = {}

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}

    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}

    def async_abort(self, *, reason):
        return {"type": "abort", "reason": reason}

    def async_update_reload_and_abort(self, entry, **kwargs):
        return {"type": "abort", "reason": "reconfigure_successful"}


class HomeAssistantError(Exception):
    pass


class FakeCoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator

    async def async_added_to_hass(self):
        return None


@dataclass
class FakeEntry:
    entry_id: str
    domain: str
    title: str
    data: dict
    options: dict = field(default_factory=dict)


class FakeConfigEntries:
    def __init__(self, entries):
        self.entries = list(entries)
        self.update_calls = []

    def async_entries(self, domain):
        return [entry for entry in self.entries if entry.domain == domain]

    def async_get_entry(self, entry_id):
        return next(
            (entry for entry in self.entries if entry.entry_id == entry_id), None
        )

    def async_update_entry(self, entry, *, data):
        entry.data = dict(data)
        self.update_calls.append((entry.entry_id, dict(data)))


class FakeHass:
    def __init__(self, entries):
        self.config = SimpleNamespace(time_zone="America/Mexico_City")
        self.config_entries = FakeConfigEntries(entries)


class FakeEntityRegistry:
    def __init__(self, by_entry, *, fail_entity_id=None):
        self.by_entry = by_entry
        self.fail_entity_id = fail_entity_id
        self.calls = []

    def async_update_entity_platform(
        self, entity_id, platform, *, new_config_entry_id
    ):
        if entity_id == self.fail_entity_id:
            raise ValueError("entity is loaded")
        entity = next(
            entity
            for entries in self.by_entry.values()
            for entity in entries
            if entity.entity_id == entity_id
        )
        old_entry_id = entity.config_entry_id
        self.by_entry[old_entry_id].remove(entity)
        self.by_entry.setdefault(new_config_entry_id, []).append(entity)
        entity.platform = platform
        entity.config_entry_id = new_config_entry_id
        self.calls.append((entity_id, platform, new_config_entry_id))
        return entity


class FakeDeviceRegistry:
    def __init__(self, by_entry):
        self.by_entry = by_entry
        self.calls = []

    def async_update_device(
        self, device_id, *, new_config_entry_id, new_identifiers
    ):
        device = next(
            device
            for devices in self.by_entry.values()
            for device in devices
            if device.id == device_id
        )
        old_entry_id = device.config_entry_id
        self.by_entry[old_entry_id].remove(device)
        self.by_entry.setdefault(new_config_entry_id, []).append(device)
        device.config_entry_id = new_config_entry_id
        device.identifiers = set(new_identifiers)
        self.calls.append((device_id, new_config_entry_id, set(new_identifiers)))
        return device


entity_registry_module = types.ModuleType("homeassistant.helpers.entity_registry")
device_registry_module = types.ModuleType("homeassistant.helpers.device_registry")


def _install_stubs():
    voluptuous = types.ModuleType("voluptuous")
    voluptuous.Required = _Required
    voluptuous.In = lambda choices: choices
    voluptuous.Schema = lambda schema: schema

    homeassistant = types.ModuleType("homeassistant")
    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = FakeEntry
    config_entries.ConfigFlow = FakeConfigFlow
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = FakeHass
    data_entry_flow = types.ModuleType("homeassistant.data_entry_flow")
    data_entry_flow.FlowResult = dict
    exceptions = types.ModuleType("homeassistant.exceptions")
    exceptions.HomeAssistantError = HomeAssistantError
    helpers = types.ModuleType("homeassistant.helpers")
    aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")
    aiohttp_client.async_get_clientsession = lambda _hass: None
    update_coordinator = types.ModuleType(
        "homeassistant.helpers.update_coordinator"
    )
    update_coordinator.CoordinatorEntity = FakeCoordinatorEntity

    device_registry_module.DeviceInfo = lambda **kwargs: kwargs
    device_registry_module.async_get = lambda hass: hass.device_registry
    device_registry_module.async_entries_for_config_entry = (
        lambda registry, entry_id: list(registry.by_entry.get(entry_id, ()))
    )
    entity_registry_module.async_get = lambda hass: hass.entity_registry
    entity_registry_module.async_entries_for_config_entry = (
        lambda registry, entry_id: list(registry.by_entry.get(entry_id, ()))
    )

    modules = {
        "voluptuous": voluptuous,
        "homeassistant": homeassistant,
        "homeassistant.config_entries": config_entries,
        "homeassistant.core": core,
        "homeassistant.data_entry_flow": data_entry_flow,
        "homeassistant.exceptions": exceptions,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.aiohttp_client": aiohttp_client,
        "homeassistant.helpers.device_registry": device_registry_module,
        "homeassistant.helpers.entity_registry": entity_registry_module,
        "homeassistant.helpers.update_coordinator": update_coordinator,
    }
    sys.modules.update(modules)


def _load(name):
    spec = spec_from_file_location(
        f"{PACKAGE_NAME}.{name}", PACKAGE_PATH / f"{name}.py"
    )
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_install_stubs()
package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_PATH)]
sys.modules[PACKAGE_NAME] = package
const = _load("const")
protocol = _load("protocol")
_load("api")
_load("eta_countdown")
_load("parsers")
_load("timezones")
config_flow = _load("config_flow")
migration = _load("migration")
entity = _load("entity")


def legacy_entry(entry_id="legacy-1", title="Legacy Account", **data_overrides):
    data = {
        const.CONF_SID: "QA.saved-session",
        const.CONF_SESSION_ID: "saved-session-id",
        const.CONF_FULL_COOKIE: (
            "sid=QA.saved-session; uev2.id.session=saved-session-id; theme=dark"
        ),
        const.CONF_ACCOUNT_NAME: title,
        const.CONF_TIME_ZONE: "America/Mexico_City",
        **data_overrides,
    }
    return FakeEntry(
        entry_id,
        const.LEGACY_DOMAIN,
        title,
        data,
        {"preserved_option": True},
    )


class ConfigFlowMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        async def active_probe(_hass, _credentials, _time_zone):
            return True

        async def profile_probe(_hass, _credentials, _time_zone):
            return {"first_name": "Profile", "last_name": "Name"}

        config_flow._probe_active_orders = active_probe
        config_flow._probe_profile = profile_probe

    async def test_fresh_setup_uses_new_domain(self):
        flow = config_flow.UberEatsConfigFlow()
        flow.hass = FakeHass([])
        result = await flow.async_step_user()
        self.assertEqual("simple_uber_eats", const.DOMAIN)
        self.assertEqual("uber_eats", const.LEGACY_DOMAIN)
        self.assertEqual(const.DOMAIN, flow.registered_domain)
        self.assertEqual("user", result["step_id"])
        manifest = json.loads((PACKAGE_PATH / "manifest.json").read_text())
        self.assertEqual(const.DOMAIN, manifest["domain"])

    async def test_single_legacy_import_is_validated_and_non_destructive(self):
        legacy = legacy_entry()
        flow = config_flow.UberEatsConfigFlow()
        flow.hass = hass = FakeHass([legacy])

        confirmation = await flow.async_step_user()
        self.assertEqual("legacy_import", confirmation["step_id"])
        self.assertNotIn("QA.saved-session", repr(confirmation))
        self.assertNotIn("saved-session-id", repr(confirmation))

        result = await flow.async_step_legacy_import(
            {config_flow.CONF_CONFIRM_IMPORT: True}
        )
        self.assertEqual("create_entry", result["type"])
        self.assertEqual(legacy.entry_id, result["data"][const.CONF_LEGACY_ENTRY_ID])
        self.assertEqual(legacy.options, result["options"])
        self.assertEqual("Legacy Account", result["title"])
        self.assertEqual(
            "America/Mexico_City", result["data"][const.CONF_TIME_ZONE]
        )
        self.assertEqual("QA.saved-session", result["data"][const.CONF_SID])
        self.assertEqual(
            "saved-session-id", result["data"][const.CONF_SESSION_ID]
        )
        self.assertIs(legacy, hass.config_entries.async_get_entry(legacy.entry_id))
        self.assertEqual(1, len(hass.config_entries.entries))

    async def test_invalid_legacy_credentials_are_not_imported(self):
        legacy = legacy_entry()

        async def rejected_probe(_hass, _credentials, _time_zone):
            return False

        config_flow._probe_active_orders = rejected_probe
        flow = config_flow.UberEatsConfigFlow()
        flow.hass = hass = FakeHass([legacy])
        await flow.async_step_user()
        result = await flow.async_step_legacy_import(
            {config_flow.CONF_CONFIRM_IMPORT: True}
        )
        self.assertEqual("form", result["type"])
        self.assertEqual("legacy_invalid_credentials", result["errors"]["base"])
        self.assertIs(legacy, hass.config_entries.async_get_entry(legacy.entry_id))

    async def test_multiple_legacy_accounts_require_explicit_selection(self):
        first = legacy_entry("legacy-a", "Same Name")
        second = legacy_entry(
            "legacy-b",
            "Same Name",
            sid="QA.second-session",
            session_id="second-session-id",
            full_cookie=(
                "sid=QA.second-session; uev2.id.session=second-session-id"
            ),
        )
        flow = config_flow.UberEatsConfigFlow()
        flow.hass = FakeHass([second, first])
        selection = await flow.async_step_user()
        self.assertEqual("legacy_select", selection["step_id"])

        confirmation = await flow.async_step_legacy_select(
            {config_flow.CONF_LEGACY_SELECTION: second.entry_id}
        )
        self.assertEqual("legacy_import", confirmation["step_id"])
        result = await flow.async_step_legacy_import(
            {config_flow.CONF_CONFIRM_IMPORT: True}
        )
        self.assertEqual(second.entry_id, result["data"][const.CONF_LEGACY_ENTRY_ID])

    async def test_imported_legacy_entry_is_not_offered_twice(self):
        legacy = legacy_entry()
        current = FakeEntry(
            "current-1",
            const.DOMAIN,
            "Current",
            {
                const.CONF_LEGACY_ENTRY_ID: legacy.entry_id,
                const.CONF_SID: legacy.data[const.CONF_SID],
                const.CONF_SESSION_ID: legacy.data[const.CONF_SESSION_ID],
            },
        )
        flow = config_flow.UberEatsConfigFlow()
        flow.hass = FakeHass([legacy, current])
        result = await flow.async_step_user()
        self.assertEqual("user", result["step_id"])

    async def test_declining_import_opens_fresh_setup_without_deleting_legacy(self):
        legacy = legacy_entry()
        flow = config_flow.UberEatsConfigFlow()
        flow.hass = hass = FakeHass([legacy])
        await flow.async_step_user()
        result = await flow.async_step_legacy_import(
            {config_flow.CONF_CONFIRM_IMPORT: False}
        )
        self.assertEqual("user", result["step_id"])
        self.assertIs(legacy, hass.config_entries.async_get_entry(legacy.entry_id))


class RegistryMigrationTests(unittest.TestCase):
    def setUp(self):
        self.legacy = legacy_entry()
        self.current = FakeEntry(
            "current-1",
            const.DOMAIN,
            "Legacy Account",
            {
                const.CONF_ACCOUNT_NAME: "Legacy Account",
                const.CONF_LEGACY_ENTRY_ID: self.legacy.entry_id,
            },
        )
        self.device = SimpleNamespace(
            id="device-1",
            config_entry_id=self.legacy.entry_id,
            identifiers={(const.LEGACY_DOMAIN, self.legacy.entry_id)},
            name_by_user="My delivery account",
        )
        self.entity = SimpleNamespace(
            entity_id="sensor.my_uber_eats_restaurant",
            unique_id="uber_eats_Legacy_Account_restaurant_name",
            platform=const.LEGACY_DOMAIN,
            config_entry_id=self.legacy.entry_id,
            device_id=self.device.id,
            name="My Restaurant",
            disabled_by="user",
            icon="mdi:silverware-fork-knife",
        )

    def hass(self, *, fail_entity_id=None):
        hass = FakeHass([self.legacy, self.current])
        hass.entity_registry = FakeEntityRegistry(
            {self.legacy.entry_id: [self.entity], self.current.entry_id: []},
            fail_entity_id=fail_entity_id,
        )
        hass.device_registry = FakeDeviceRegistry(
            {self.legacy.entry_id: [self.device], self.current.entry_id: []}
        )
        return hass

    def test_public_registry_move_preserves_entity_and_device_identity(self):
        hass = self.hass()
        result = migration.migrate_legacy_registry(hass, self.current)
        self.assertEqual(migration.REGISTRY_MIGRATED, result)
        self.assertEqual(const.DOMAIN, self.entity.platform)
        self.assertEqual(self.current.entry_id, self.entity.config_entry_id)
        self.assertEqual("sensor.my_uber_eats_restaurant", self.entity.entity_id)
        self.assertEqual("My Restaurant", self.entity.name)
        self.assertEqual("user", self.entity.disabled_by)
        self.assertEqual("mdi:silverware-fork-knife", self.entity.icon)
        self.assertEqual("device-1", self.device.id)
        self.assertEqual(self.current.entry_id, self.device.config_entry_id)
        self.assertEqual(
            {(const.DOMAIN, self.current.entry_id)}, self.device.identifiers
        )
        self.assertEqual("My delivery account", self.device.name_by_user)
        self.assertIs(self.legacy, hass.config_entries.async_get_entry("legacy-1"))

        entity_calls = len(hass.entity_registry.calls)
        device_calls = len(hass.device_registry.calls)
        self.assertEqual(
            migration.REGISTRY_MIGRATED,
            migration.migrate_legacy_registry(hass, self.current),
        )
        self.assertEqual(entity_calls, len(hass.entity_registry.calls))
        self.assertEqual(device_calls, len(hass.device_registry.calls))

    def test_fresh_device_identity_uses_only_the_new_domain(self):
        account_entity = entity.UberEatsCoordinatorEntity(
            SimpleNamespace(), "Account", "current-1", "restaurant_name"
        )
        self.assertEqual(
            {(const.DOMAIN, "current-1")},
            account_entity._attr_device_info["identifiers"],
        )
        self.assertEqual("Account Uber Eats", account_entity._attr_device_info["name"])
        self.assertEqual(
            "uber_eats_Account_restaurant_name", account_entity._attr_unique_id
        )

    def test_loaded_legacy_entity_falls_back_without_moving_device(self):
        hass = self.hass(fail_entity_id=self.entity.entity_id)
        result = migration.migrate_legacy_registry(hass, self.current)
        self.assertEqual(migration.REGISTRY_MIGRATION_UNAVAILABLE, result)
        self.assertEqual(const.LEGACY_DOMAIN, self.entity.platform)
        self.assertEqual(self.legacy.entry_id, self.entity.config_entry_id)
        self.assertEqual(self.legacy.entry_id, self.device.config_entry_id)
        self.assertEqual([], hass.device_registry.calls)

    def test_multiple_accounts_are_isolated(self):
        other_legacy = legacy_entry("legacy-2", "Other Account")
        other_entity = SimpleNamespace(
            entity_id="sensor.other_uber_eats_restaurant",
            unique_id="uber_eats_Other_Account_restaurant_name",
            platform=const.LEGACY_DOMAIN,
            config_entry_id=other_legacy.entry_id,
            device_id=None,
        )
        hass = self.hass()
        hass.config_entries.entries.append(other_legacy)
        hass.entity_registry.by_entry[other_legacy.entry_id] = [other_entity]
        migration.migrate_legacy_registry(hass, self.current)
        self.assertEqual(const.LEGACY_DOMAIN, other_entity.platform)
        self.assertEqual(other_legacy.entry_id, other_entity.config_entry_id)

    def test_no_private_storage_or_config_domain_mutation(self):
        source = (PACKAGE_PATH / "migration.py").read_text()
        self.assertNotIn(".storage", source)
        self.assertNotIn("__dict__", source)
        self.assertNotIn("object.__setattr__", source)
        self.assertNotIn("entry.domain =", source)


if __name__ == "__main__":
    unittest.main()
