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
        data = kwargs.get("data")
        if data is not None:
            entry.data = dict(data)
        return {
            "type": "abort",
            "reason": "reconfigure_successful",
            **kwargs,
        }


class HomeAssistantError(Exception):
    pass


class ConfigEntryAuthFailed(Exception):
    pass


class ConfigEntryNotReady(Exception):
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

    async def async_forward_entry_setups(self, _entry, _platforms):
        return None


class FakeHass:
    def __init__(self, entries):
        self.config = SimpleNamespace(
            time_zone="America/Mexico_City",
            latitude=0.0,
            longitude=0.0,
        )
        self.config_entries = FakeConfigEntries(entries)
        self.data = {}


class FakeResponse:
    def __init__(self, status, body=None, cookies=None):
        self.status = status
        self._body = body
        self.cookies = cookies or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, *, json, headers):
        self.calls.append((url, json, headers))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


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
    const_module = types.ModuleType("homeassistant.const")
    const_module.Platform = SimpleNamespace(
        SENSOR="sensor",
        BINARY_SENSOR="binary_sensor",
        DEVICE_TRACKER="device_tracker",
    )
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = FakeHass
    data_entry_flow = types.ModuleType("homeassistant.data_entry_flow")
    data_entry_flow.FlowResult = dict
    exceptions = types.ModuleType("homeassistant.exceptions")
    exceptions.HomeAssistantError = HomeAssistantError
    exceptions.ConfigEntryAuthFailed = ConfigEntryAuthFailed
    exceptions.ConfigEntryNotReady = ConfigEntryNotReady
    helpers = types.ModuleType("homeassistant.helpers")
    selector = types.ModuleType("homeassistant.helpers.selector")

    class TextSelectorType:
        TEXT = "text"

    @dataclass
    class TextSelectorConfig:
        autocomplete: str | None = None
        multiline: bool = False
        type: str | None = None

    @dataclass
    class TextSelector:
        config: TextSelectorConfig

    selector.TextSelectorType = TextSelectorType
    selector.TextSelectorConfig = TextSelectorConfig
    selector.TextSelector = TextSelector
    helpers.selector = selector
    aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")
    aiohttp_client.async_get_clientsession = lambda _hass: None
    update_coordinator = types.ModuleType(
        "homeassistant.helpers.update_coordinator"
    )
    update_coordinator.CoordinatorEntity = FakeCoordinatorEntity

    class DataUpdateCoordinator:
        def __init__(self, hass, _logger, *, name, update_interval):
            self.hass = hass
            self.name = name
            self.update_interval = update_interval

    update_coordinator.DataUpdateCoordinator = DataUpdateCoordinator

    label_registry = types.ModuleType("homeassistant.helpers.label_registry")
    label_registry.async_get = lambda hass: hass.label_registry
    helpers.label_registry = label_registry

    util = types.ModuleType("homeassistant.util")
    dt_util = types.ModuleType("homeassistant.util.dt")
    dt_util.now = lambda: None
    util.dt = dt_util

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
        "homeassistant.const": const_module,
        "homeassistant.core": core,
        "homeassistant.data_entry_flow": data_entry_flow,
        "homeassistant.exceptions": exceptions,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.selector": selector,
        "homeassistant.helpers.aiohttp_client": aiohttp_client,
        "homeassistant.helpers.device_registry": device_registry_module,
        "homeassistant.helpers.entity_registry": entity_registry_module,
        "homeassistant.helpers.label_registry": label_registry,
        "homeassistant.helpers.update_coordinator": update_coordinator,
        "homeassistant.util": util,
        "homeassistant.util.dt": dt_util,
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
api = _load("api")
_load("eta_countdown")
_load("parsers")
_load("timezones")
config_flow = _load("config_flow")
migration = _load("migration")
coordinator = _load("coordinator")
entity = _load("entity")
integration = _load("__init__")

REAL_VALIDATE_CREDENTIALS = config_flow._validate_credentials
REAL_GET_CLIENT_SESSION = config_flow.async_get_clientsession
REAL_COORDINATOR = coordinator.UberEatsCoordinator


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
    SID = "QA.EXAMPLE-0123456789"
    SESSION = "00000000-0000-0000-0000-000000000000"
    CANONICAL = (
        "sid=QA.EXAMPLE-0123456789; "
        "uev2.id.session=00000000-0000-0000-0000-000000000000"
    )

    async def asyncSetUp(self):
        async def validate(
            _hass, credentials, _time_zone, *, include_profile
        ):
            profile = (
                {"first_name": "Profile", "last_name": "Name"}
                if include_profile
                else None
            )
            return config_flow.CredentialValidationResult(credentials, profile)

        config_flow._validate_credentials = validate

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

        session_field = next(
            value
            for key, value in result["data_schema"].items()
            if key.key == const.CONF_COOKIE
        )
        self.assertTrue(session_field.config.multiline)
        self.assertEqual("text", session_field.config.type)
        self.assertEqual("off", session_field.config.autocomplete)

    async def test_fresh_curl_setup_stores_only_canonical_credentials(self):
        copied = f"""curl 'https://www.ubereats.com/_p/api/getActiveOrdersV1' \\
  -H 'Content-Type: application/json' \\
  -H 'Cookie: analytics=FAKE; sid={self.SID}; uev2.id.session={self.SESSION}; uev2.loc=FAKE' \\
  --data-raw '{{"fake":"request body"}}'"""
        flow = config_flow.UberEatsConfigFlow()
        flow.hass = FakeHass([])
        result = await flow.async_step_user(
            {
                const.CONF_TIME_ZONE: "America/Mexico_City",
                const.CONF_COOKIE: copied,
            }
        )

        self.assertEqual("create_entry", result["type"])
        self.assertEqual(self.SID, result["data"][const.CONF_SID])
        self.assertEqual(self.SESSION, result["data"][const.CONF_SESSION_ID])
        self.assertEqual(self.CANONICAL, result["data"][const.CONF_FULL_COOKIE])
        self.assertNotIn(const.CONF_COOKIE, result["data"])
        stored = repr(result["data"])
        for discarded in ("curl ", "Content-Type", "request body", "analytics", "uev2.loc"):
            self.assertNotIn(discarded, stored)

    async def test_reauth_and_reconfigure_store_only_canonical_credentials(self):
        copied = (
            "POST /_p/api/getActiveOrdersV1 HTTP/2\n"
            "Host: www.ubereats.com\n"
            f"Cookie: sid={self.SID}; ignored=FAKE; "
            f"uev2.id.session={self.SESSION}\n"
            "X-Ignored: FAKE-HEADER"
        )
        for step in ("reauth", "reconfigure"):
            with self.subTest(step=step):
                entry = FakeEntry(
                    "current-1",
                    const.DOMAIN,
                    "Account",
                    {
                        const.CONF_SID: "QA.OLD",
                        const.CONF_SESSION_ID: "old-session",
                        const.CONF_FULL_COOKIE: "sid=QA.OLD; ignored=OLD; uev2.id.session=old-session",
                        const.CONF_ACCOUNT_NAME: "Account",
                        const.CONF_TIME_ZONE: "America/Mexico_City",
                    },
                )
                flow = config_flow.UberEatsConfigFlow()
                flow.hass = FakeHass([entry])
                flow.context["entry_id"] = entry.entry_id
                result = (
                    await flow.async_step_reauth_confirm({const.CONF_COOKIE: copied})
                    if step == "reauth"
                    else await flow.async_step_reconfigure({const.CONF_COOKIE: copied})
                )
                self.assertEqual("abort", result["type"])
                self.assertEqual(self.SID, result["data"][const.CONF_SID])
                self.assertEqual(self.SESSION, result["data"][const.CONF_SESSION_ID])
                self.assertEqual(
                    self.CANONICAL, result["data"][const.CONF_FULL_COOKIE]
                )
                self.assertNotIn("FAKE-HEADER", repr(result["data"]))
                self.assertNotIn("ignored", repr(result["data"]))

    async def test_validation_threads_every_rotation_through_one_client(self):
        initial = protocol.SessionCredentials(self.SID, self.SESSION)
        cases = (
            ("sid_first", {"sid": "QA.FIRST"}, {}, "QA.FIRST", self.SESSION),
            (
                "session_first",
                {"uev2.id.session": "first-session"},
                {},
                self.SID,
                "first-session",
            ),
            (
                "both_first",
                {"sid": "QA.FIRST", "uev2.id.session": "first-session"},
                {},
                "QA.FIRST",
                "first-session",
            ),
            ("sid_profile", {}, {"sid": "QA.PROFILE"}, "QA.PROFILE", self.SESSION),
            (
                "session_profile",
                {},
                {"uev2.id.session": "profile-session"},
                self.SID,
                "profile-session",
            ),
            (
                "both_profile",
                {},
                {"sid": "QA.PROFILE", "uev2.id.session": "profile-session"},
                "QA.PROFILE",
                "profile-session",
            ),
        )
        profile_body = {
            "data": {
                "isLoggedIn": True,
                "firstName": "Profile",
                "lastName": "Name",
            }
        }
        try:
            for name, first_rotation, profile_rotation, final_sid, final_session in cases:
                with self.subTest(name=name):
                    session = FakeSession(
                        [
                            FakeResponse(
                                200, {"data": {"orders": []}}, first_rotation
                            ),
                            FakeResponse(200, profile_body, profile_rotation),
                        ]
                    )
                    config_flow.async_get_clientsession = lambda _hass: session
                    result = await REAL_VALIDATE_CREDENTIALS(
                        FakeHass([]),
                        initial,
                        "America/Mexico_City",
                        include_profile=True,
                    )
                    after_first = initial.rotated(first_rotation)
                    self.assertEqual(
                        after_first.header(), session.calls[1][2]["Cookie"]
                    )
                    self.assertEqual(
                        (final_sid, final_session),
                        (result.credentials.sid, result.credentials.session_id),
                    )
        finally:
            config_flow.async_get_clientsession = REAL_GET_CLIENT_SESSION

    async def test_all_flow_types_persist_final_rotated_credentials(self):
        final = protocol.SessionCredentials("QA.FINAL", "final-session")

        async def rotated_validation(
            _hass, _credentials, _time_zone, *, include_profile
        ):
            profile = (
                {"first_name": "Final", "last_name": "Account"}
                if include_profile
                else None
            )
            return config_flow.CredentialValidationResult(final, profile)

        config_flow._validate_credentials = rotated_validation

        fresh_flow = config_flow.UberEatsConfigFlow()
        fresh_flow.hass = FakeHass([])
        fresh = await fresh_flow.async_step_user(
            {
                const.CONF_TIME_ZONE: "America/Mexico_City",
                const.CONF_COOKIE: self.CANONICAL,
            }
        )
        self.assertEqual(final.sid, fresh["data"][const.CONF_SID])
        self.assertEqual(final.session_id, fresh["data"][const.CONF_SESSION_ID])
        self.assertEqual(final.header(), fresh["data"][const.CONF_FULL_COOKIE])

        for step in ("reauth", "reconfigure"):
            with self.subTest(step=step):
                entry = FakeEntry(
                    f"{step}-entry",
                    const.DOMAIN,
                    "Account",
                    {
                        const.CONF_SID: self.SID,
                        const.CONF_SESSION_ID: self.SESSION,
                        const.CONF_FULL_COOKIE: self.CANONICAL,
                        const.CONF_ACCOUNT_NAME: "Account",
                        const.CONF_TIME_ZONE: "America/Mexico_City",
                        "preserved": True,
                    },
                )
                flow = config_flow.UberEatsConfigFlow()
                flow.hass = FakeHass([entry])
                flow.context["entry_id"] = entry.entry_id
                result = (
                    await flow.async_step_reauth_confirm(
                        {const.CONF_COOKIE: self.CANONICAL}
                    )
                    if step == "reauth"
                    else await flow.async_step_reconfigure(
                        {const.CONF_COOKIE: self.CANONICAL}
                    )
                )
                self.assertEqual(final.sid, result["data"][const.CONF_SID])
                self.assertEqual(
                    final.session_id, result["data"][const.CONF_SESSION_ID]
                )
                self.assertEqual(
                    final.header(), result["data"][const.CONF_FULL_COOKIE]
                )
                self.assertTrue(result["data"]["preserved"])

        legacy = legacy_entry()
        legacy_flow = config_flow.UberEatsConfigFlow()
        legacy_flow.hass = FakeHass([legacy])
        await legacy_flow.async_step_user()
        imported = await legacy_flow.async_step_legacy_import(
            {config_flow.CONF_CONFIRM_IMPORT: True}
        )
        self.assertEqual(final.sid, imported["data"][const.CONF_SID])
        self.assertEqual(final.session_id, imported["data"][const.CONF_SESSION_ID])
        self.assertEqual(final.header(), imported["data"][const.CONF_FULL_COOKIE])

    async def test_http_probe_errors_have_conservative_flow_semantics(self):
        cases = (
            ("401", [FakeResponse(401)], "invalid_credentials"),
            ("403", [FakeResponse(403)], "invalid_credentials"),
            (
                "auth_envelope",
                [FakeResponse(200, {"error": {"code": "SESSION_EXPIRED"}})],
                "invalid_credentials",
            ),
            ("408", [FakeResponse(408)], "cannot_connect"),
            ("429", [FakeResponse(429)], "cannot_connect"),
            ("503", [FakeResponse(503)], "cannot_connect"),
            ("unexpected_404", [FakeResponse(404)], "cannot_connect"),
            (
                "malformed_active_200",
                [FakeResponse(200, {"data": {"unexpected": []}})],
                "cannot_connect",
            ),
            (
                "malformed_profile_200",
                [
                    FakeResponse(200, {"data": {"orders": []}}),
                    FakeResponse(200, {"data": {"unexpected": True}}),
                ],
                "cannot_connect",
            ),
            ("transport", [OSError("temporary")], "cannot_connect"),
        )
        config_flow._validate_credentials = REAL_VALIDATE_CREDENTIALS
        try:
            for name, responses, expected in cases:
                with self.subTest(name=name):
                    session = FakeSession(responses)
                    config_flow.async_get_clientsession = lambda _hass: session
                    flow = config_flow.UberEatsConfigFlow()
                    flow.hass = FakeHass([])
                    result = await flow.async_step_user(
                        {
                            const.CONF_TIME_ZONE: "America/Mexico_City",
                            const.CONF_COOKIE: self.CANONICAL,
                        }
                    )
                    self.assertEqual(expected, result["errors"]["base"])
                    self.assertNotIn(self.SID, repr(result))
                    self.assertNotIn(self.SESSION, repr(result))
        finally:
            config_flow.async_get_clientsession = REAL_GET_CLIENT_SESSION

    async def test_failed_input_is_not_prepopulated_or_echoed(self):
        copied = "curl 'https://www.ubereats.com/' -H 'Cookie: sid=QA.PRIVATE'"
        flow = config_flow.UberEatsConfigFlow()
        flow.hass = FakeHass([])
        result = await flow.async_step_user(
            {
                const.CONF_TIME_ZONE: "America/Mexico_City",
                const.CONF_COOKIE: copied,
            }
        )
        self.assertEqual("form", result["type"])
        self.assertEqual("session_not_found", result["errors"][const.CONF_COOKIE])
        self.assertNotIn("QA.PRIVATE", repr(result))
        session_key = next(
            key for key in result["data_schema"] if key.key == const.CONF_COOKIE
        )
        self.assertIsNone(session_key.default)

    async def test_oversized_input_uses_private_translated_error(self):
        raw = "x" * (protocol.MAX_AUTHENTICATION_INPUT_BYTES + 1)
        flow = config_flow.UberEatsConfigFlow()
        flow.hass = FakeHass([])
        result = await flow.async_step_user(
            {
                const.CONF_TIME_ZONE: "America/Mexico_City",
                const.CONF_COOKIE: raw,
            }
        )
        self.assertEqual(
            "session_input_too_large", result["errors"][const.CONF_COOKIE]
        )
        self.assertNotIn(raw[:100], repr(result))
        strings = json.loads((PACKAGE_PATH / "strings.json").read_text())
        self.assertIn("session_input_too_large", strings["config"]["error"])

    async def test_reauth_and_reconfigure_invalid_input_is_private(self):
        invalid_inputs = (
            "curl https://www.ubereats.com/ -H 'Cookie sid=QA.PRIVATE'",
            "Cookie: uev2.id.session=private-session",
            "Cookie: sid=QA.PRIVATE",
            (
                f"Cookie: {self.CANONICAL}\n"
                "Cookie: sid=QA.CONFLICT; uev2.id.session=conflict-session"
            ),
        )
        for step in ("reauth", "reconfigure"):
            for raw in invalid_inputs:
                with self.subTest(step=step, raw=raw[:20]):
                    entry = FakeEntry(
                        "current-1",
                        const.DOMAIN,
                        "Account",
                        {
                            const.CONF_SID: self.SID,
                            const.CONF_SESSION_ID: self.SESSION,
                            const.CONF_FULL_COOKIE: self.CANONICAL,
                            const.CONF_ACCOUNT_NAME: "Account",
                            const.CONF_TIME_ZONE: "America/Mexico_City",
                            "preserved": "value",
                        },
                        {"preserved_option": True},
                    )
                    original_data = dict(entry.data)
                    original_options = dict(entry.options)
                    flow = config_flow.UberEatsConfigFlow()
                    flow.hass = FakeHass([entry])
                    flow.context["entry_id"] = entry.entry_id
                    result = (
                        await flow.async_step_reauth_confirm(
                            {const.CONF_COOKIE: raw}
                        )
                        if step == "reauth"
                        else await flow.async_step_reconfigure(
                            {const.CONF_COOKIE: raw}
                        )
                    )
                    self.assertEqual("form", result["type"])
                    self.assertIn(const.CONF_COOKIE, result["errors"])
                    self.assertNotIn("QA.PRIVATE", repr(result))
                    self.assertNotIn("private-session", repr(result))
                    session_key = next(
                        key
                        for key in result["data_schema"]
                        if key.key == const.CONF_COOKIE
                    )
                    self.assertIsNone(session_key.default)
                    self.assertEqual(original_data, entry.data)
                    self.assertEqual(original_options, entry.options)

    async def test_reauth_and_reconfigure_probe_failures_are_private(self):
        outcomes = (
            (config_flow.CredentialProbeRejected, "invalid_credentials"),
            (config_flow.CredentialProbeUnavailable, "cannot_connect"),
        )
        for step in ("reauth", "reconfigure"):
            for exception, expected in outcomes:
                with self.subTest(step=step, expected=expected):
                    async def failed_validation(
                        _hass, _credentials, _time_zone, *, include_profile
                    ):
                        raise exception

                    config_flow._validate_credentials = failed_validation
                    entry = FakeEntry(
                        "current-1",
                        const.DOMAIN,
                        "Account",
                        {
                            const.CONF_SID: "QA.OLD",
                            const.CONF_SESSION_ID: "old-session",
                            const.CONF_FULL_COOKIE: (
                                "sid=QA.OLD; uev2.id.session=old-session"
                            ),
                            const.CONF_ACCOUNT_NAME: "Account",
                            const.CONF_TIME_ZONE: "America/Mexico_City",
                            "preserved": "value",
                        },
                        {"preserved_option": True},
                    )
                    original_data = dict(entry.data)
                    flow = config_flow.UberEatsConfigFlow()
                    flow.hass = FakeHass([entry])
                    flow.context["entry_id"] = entry.entry_id
                    result = (
                        await flow.async_step_reauth_confirm(
                            {const.CONF_COOKIE: self.CANONICAL}
                        )
                        if step == "reauth"
                        else await flow.async_step_reconfigure(
                            {const.CONF_COOKIE: self.CANONICAL}
                        )
                    )
                    self.assertEqual(expected, result["errors"]["base"])
                    self.assertNotIn(self.SID, repr(result))
                    self.assertNotIn(self.SESSION, repr(result))
                    self.assertEqual(original_data, entry.data)
                    self.assertEqual({"preserved_option": True}, entry.options)

    async def test_credential_updates_exclude_self_but_reject_other_entries(self):
        other_sid = "QA.OTHER"
        other_session = "other-session"
        other_cookie = f"sid={other_sid}; uev2.id.session={other_session}"
        for step in ("reauth", "reconfigure"):
            with self.subTest(step=step, case="same_entry"):
                current = FakeEntry(
                    "current-1",
                    const.DOMAIN,
                    "Current",
                    {
                        const.CONF_SID: self.SID,
                        const.CONF_SESSION_ID: self.SESSION,
                        const.CONF_FULL_COOKIE: self.CANONICAL,
                        const.CONF_ACCOUNT_NAME: "Current",
                        const.CONF_TIME_ZONE: "America/Mexico_City",
                        "preserved": True,
                    },
                )
                flow = config_flow.UberEatsConfigFlow()
                flow.hass = FakeHass([current])
                flow.context["entry_id"] = current.entry_id
                allowed = (
                    await flow.async_step_reauth_confirm(
                        {const.CONF_COOKIE: self.CANONICAL}
                    )
                    if step == "reauth"
                    else await flow.async_step_reconfigure(
                        {const.CONF_COOKIE: self.CANONICAL}
                    )
                )
                self.assertEqual("abort", allowed["type"])
                self.assertTrue(allowed["data"]["preserved"])

            with self.subTest(step=step, case="other_entry"):
                current = FakeEntry(
                    "current-1",
                    const.DOMAIN,
                    "Current",
                    {
                        const.CONF_SID: self.SID,
                        const.CONF_SESSION_ID: self.SESSION,
                        const.CONF_FULL_COOKIE: self.CANONICAL,
                        const.CONF_ACCOUNT_NAME: "Current",
                        const.CONF_TIME_ZONE: "America/Mexico_City",
                        "preserved": True,
                    },
                )
                other = FakeEntry(
                    "other-1",
                    const.DOMAIN,
                    "Other",
                    {
                        const.CONF_SID: other_sid,
                        const.CONF_SESSION_ID: other_session,
                        const.CONF_FULL_COOKIE: other_cookie,
                        const.CONF_ACCOUNT_NAME: "Other",
                        const.CONF_TIME_ZONE: "America/Mexico_City",
                    },
                )
                original = dict(current.data)
                flow = config_flow.UberEatsConfigFlow()
                flow.hass = FakeHass([current, other])
                flow.context["entry_id"] = current.entry_id
                rejected = (
                    await flow.async_step_reauth_confirm(
                        {const.CONF_COOKIE: other_cookie}
                    )
                    if step == "reauth"
                    else await flow.async_step_reconfigure(
                        {const.CONF_COOKIE: other_cookie}
                    )
                )
                self.assertEqual("form", rejected["type"])
                self.assertEqual("already_configured", rejected["errors"]["base"])
                self.assertNotIn(other_sid, repr(rejected))
                self.assertNotIn(other_session, repr(rejected))
                self.assertEqual(original, current.data)

    async def test_temporary_probe_failure_is_not_reported_as_rejected_session(self):
        for failure in (
            config_flow.CredentialProbeUnavailable,
            OSError,
            TimeoutError,
        ):
            with self.subTest(failure=failure.__name__):
                async def unavailable_probe(
                    _hass, _credentials, _time_zone, *, include_profile
                ):
                    raise failure

                config_flow._validate_credentials = unavailable_probe
                flow = config_flow.UberEatsConfigFlow()
                flow.hass = FakeHass([])
                result = await flow.async_step_user(
                    {
                        const.CONF_TIME_ZONE: "America/Mexico_City",
                        const.CONF_COOKIE: self.CANONICAL,
                    }
                )
                self.assertEqual("cannot_connect", result["errors"]["base"])

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
        self.assertEqual(
            "sid=QA.saved-session; uev2.id.session=saved-session-id",
            result["data"][const.CONF_FULL_COOKIE],
        )
        self.assertIs(legacy, hass.config_entries.async_get_entry(legacy.entry_id))
        self.assertEqual(1, len(hass.config_entries.entries))

    async def test_invalid_legacy_credentials_are_not_imported(self):
        legacy = legacy_entry()

        async def rejected_probe(
            _hass, _credentials, _time_zone, *, include_profile
        ):
            raise config_flow.CredentialProbeRejected

        config_flow._validate_credentials = rejected_probe
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


class AuthenticationLifecycleTests(unittest.IsolatedAsyncioTestCase):
    SID = "QA.AUTHORITATIVE"
    SESSION = "authoritative-session"
    CANONICAL = (
        "sid=QA.AUTHORITATIVE; uev2.id.session=authoritative-session"
    )

    async def _run_setup(self, hass, entry):
        class SetupCoordinator:
            instances = []

            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.label_id = None
                self.__class__.instances.append(self)

            async def async_config_entry_first_refresh(self):
                return None

        original_coordinator = integration.UberEatsCoordinator
        original_migrate = integration.migrate_legacy_registry
        original_prepare = integration._async_prepare_entity_registry
        original_apply = integration._async_apply_label_to_entry_entities
        integration.UberEatsCoordinator = SetupCoordinator
        integration.migrate_legacy_registry = lambda _hass, _entry: None
        integration._async_prepare_entity_registry = (
            lambda _hass, _entry, _account_name: "uber-eats-label"
        )
        integration._async_apply_label_to_entry_entities = (
            lambda _hass, _entry, _label_id: None
        )
        try:
            self.assertTrue(await integration.async_setup_entry(hass, entry))
        finally:
            integration.UberEatsCoordinator = original_coordinator
            integration.migrate_legacy_registry = original_migrate
            integration._async_prepare_entity_registry = original_prepare
            integration._async_apply_label_to_entry_entities = original_apply
        return SetupCoordinator.instances[-1]

    async def test_setup_normalizes_historical_cookie_once(self):
        historical = (
            "sid=QA.STALE; uev2.id.session=stale-session; analytics=FAKE; "
            f"padding={'x' * 10000}"
        )
        entry = FakeEntry(
            "current-1",
            const.DOMAIN,
            "Account",
            {
                const.CONF_SID: self.SID,
                const.CONF_SESSION_ID: self.SESSION,
                const.CONF_FULL_COOKIE: historical,
                const.CONF_ACCOUNT_NAME: "Account",
                const.CONF_TIME_ZONE: "America/Mexico_City",
                "preserved": {"value": True},
            },
            {"preserved_option": True},
        )
        hass = FakeHass([entry])

        first = await self._run_setup(hass, entry)
        self.assertEqual(self.CANONICAL, entry.data[const.CONF_FULL_COOKIE])
        self.assertEqual({"value": True}, entry.data["preserved"])
        self.assertEqual({"preserved_option": True}, entry.options)
        self.assertEqual(1, len(hass.config_entries.update_calls))
        self.assertEqual(self.SID, first.kwargs["sid"])
        self.assertEqual(self.SESSION, first.kwargs["session_id"])
        self.assertEqual(self.CANONICAL, first.kwargs["full_cookie"])

        second = await self._run_setup(hass, entry)
        self.assertEqual(1, len(hass.config_entries.update_calls))
        self.assertEqual(self.CANONICAL, second.kwargs["full_cookie"])

    async def test_setup_accepts_missing_full_cookie(self):
        entry = FakeEntry(
            "current-1",
            const.DOMAIN,
            "Account",
            {
                const.CONF_SID: self.SID,
                const.CONF_SESSION_ID: self.SESSION,
                const.CONF_ACCOUNT_NAME: "Account",
                const.CONF_TIME_ZONE: "America/Mexico_City",
                "preserved": True,
            },
        )
        hass = FakeHass([entry])

        created = await self._run_setup(hass, entry)
        self.assertEqual(self.CANONICAL, entry.data[const.CONF_FULL_COOKIE])
        self.assertEqual(self.CANONICAL, created.kwargs["full_cookie"])
        self.assertTrue(entry.data["preserved"])

    def test_runtime_rotation_persists_and_is_used_after_restart(self):
        entry = FakeEntry(
            "current-1",
            const.DOMAIN,
            "Account",
            {
                const.CONF_SID: "QA.OLD",
                const.CONF_SESSION_ID: "old-session",
                const.CONF_FULL_COOKIE: (
                    "analytics=FAKE; sid=QA.OLD; "
                    "uev2.id.session=old-session; location=FAKE"
                ),
                const.CONF_ACCOUNT_NAME: "Account",
                const.CONF_TIME_ZONE: "America/Mexico_City",
            },
        )
        hass = FakeHass([entry])
        active = REAL_COORDINATOR(
            hass=hass,
            entry_id=entry.entry_id,
            sid=entry.data[const.CONF_SID],
            session_id=entry.data[const.CONF_SESSION_ID],
            account_name=entry.data[const.CONF_ACCOUNT_NAME],
            time_zone=entry.data[const.CONF_TIME_ZONE],
            full_cookie=entry.data[const.CONF_FULL_COOKIE],
        )
        rotated = protocol.SessionCredentials("QA.NEW", "new-session")
        active._api.credentials = rotated
        active._persist_rotated_credentials(api.UberResponse(200, {}, rotated))

        expected = "sid=QA.NEW; uev2.id.session=new-session"
        self.assertEqual("QA.NEW", entry.data[const.CONF_SID])
        self.assertEqual("new-session", entry.data[const.CONF_SESSION_ID])
        self.assertEqual(expected, entry.data[const.CONF_FULL_COOKIE])
        self.assertNotIn("analytics", repr(entry.data))
        self.assertNotIn("location", repr(entry.data))

        restarted = REAL_COORDINATOR(
            hass=hass,
            entry_id=entry.entry_id,
            sid=entry.data[const.CONF_SID],
            session_id=entry.data[const.CONF_SESSION_ID],
            account_name=entry.data[const.CONF_ACCOUNT_NAME],
            time_zone=entry.data[const.CONF_TIME_ZONE],
            full_cookie=entry.data[const.CONF_FULL_COOKIE],
        )
        self.assertEqual(expected, restarted.full_cookie)
        self.assertEqual(expected, restarted._api.credentials.header())


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
