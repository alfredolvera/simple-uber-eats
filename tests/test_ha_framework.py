"""Checks against the real HA framework, when available (no live Uber traffic)."""

import importlib
import importlib.util
import unittest
from datetime import UTC
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

HAS_HA = importlib.util.find_spec("homeassistant") is not None
PKG = "custom_components.simple_uber_eats"


@unittest.skipUnless(HAS_HA, "Home Assistant runtime is not installed")
class FrameworkTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_platform_imports(self):
        for name in (
            "config_flow",
            "binary_sensor",
            "sensor",
            "device_tracker",
            "diagnostics",
        ):
            self.assertIsNotNone(importlib.import_module(PKG + "." + name))

    async def test_data_updates_do_not_reload_options_updates_do(self):
        lifecycle = importlib.import_module(PKG)
        reload = AsyncMock()
        hass = SimpleNamespace(config_entries=SimpleNamespace(async_reload=reload))
        entry = SimpleNamespace(
            entry_id="x", options={}, runtime_data=SimpleNamespace(options_snapshot={})
        )
        await lifecycle._async_options_updated(hass, entry)
        reload.assert_not_awaited()
        entry.options = {"courier_phone": True}
        await lifecycle._async_options_updated(hass, entry)
        reload.assert_awaited_once_with("x")

    async def test_flow_valid_orders_survive_optional_profile_failure(self):
        flow_mod = importlib.import_module(PKG + ".config_flow")
        model = importlib.import_module(PKG + ".model")
        credentials = importlib.import_module(PKG + ".web_session").WebSession(
            "sid", "session"
        )
        client = SimpleNamespace(
            credentials=credentials,
            read_orders=AsyncMock(return_value=model.Snapshot()),
            account_label=AsyncMock(side_effect=model.ServiceUnavailable()),
        )
        flow = flow_mod.UberEatsConfigFlow()
        flow.hass = SimpleNamespace(config=SimpleNamespace(time_zone="UTC"))
        with (
            patch.object(flow_mod, "create_session"),
            patch.object(flow_mod, "Gateway", return_value=client),
        ):
            result = await flow.async_step_user(
                {
                    "cookie": "sid=QA.test; uev2.id.session=session-id",
                    "time_zone": "UTC",
                }
            )
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(
            set(result["data"]), {"sid", "session_id", "account_name", "time_zone"}
        )

    async def test_initial_privacy_options_are_false(self):
        flow_mod = importlib.import_module(PKG + ".config_flow")
        flow = flow_mod.UberEatsOptionsFlow()
        # Public property obtains the entry from the flow context in HA.
        with patch.object(
            type(flow),
            "config_entry",
            new=property(lambda self: SimpleNamespace(options={})),
        ):
            result = await flow.async_step_init()
        self.assertEqual(
            result["data_schema"]({}), {"courier_phone": False, "courier_photo": False}
        )

    async def test_reauth_and_reconfigure_use_current_entry(self):
        flow_mod = importlib.import_module(PKG + ".config_flow")
        flow = flow_mod.UberEatsConfigFlow()
        entry = SimpleNamespace(data={})
        with (
            patch.object(flow, "_get_reauth_entry", return_value=entry),
            patch.object(
                flow, "_credentials_step", new=AsyncMock(return_value="ok")
            ) as step,
        ):
            self.assertEqual(await flow.async_step_reauth({}), "ok")
            step.assert_awaited_once_with("reauth_confirm", None, entry)
        with (
            patch.object(flow, "_get_reconfigure_entry", return_value=entry),
            patch.object(
                flow, "_credentials_step", new=AsyncMock(return_value="ok")
            ) as step,
        ):
            self.assertEqual(await flow.async_step_reconfigure({"cookie": "x"}), "ok")
            step.assert_awaited_once_with("reconfigure", {"cookie": "x"}, entry)

    async def test_setup_uses_authoritative_orders_when_profile_unavailable(self):
        lifecycle = importlib.import_module(PKG)
        model = importlib.import_module(PKG + ".model")
        credentials = importlib.import_module(PKG + ".web_session").WebSession("a", "b")
        gateway = SimpleNamespace(
            credentials=credentials,
            account_label=AsyncMock(side_effect=model.ServiceUnavailable()),
        )
        coordinator = SimpleNamespace(
            async_config_entry_first_refresh=AsyncMock(),
            persist_credentials=lambda: None,
            options_snapshot={},
        )
        entry = SimpleNamespace(
            data={"sid": "a", "session_id": "b", "time_zone": "UTC"},
            entry_id="e",
            options={},
            async_on_unload=lambda callback: None,
            add_update_listener=lambda callback: lambda: None,
        )
        hass = SimpleNamespace(
            config=SimpleNamespace(time_zone="UTC"),
            config_entries=SimpleNamespace(
                async_update_entry=lambda *args, **kwargs: None,
                async_forward_entry_setups=AsyncMock(),
            ),
        )
        with (
            patch.object(lifecycle, "Gateway", return_value=gateway),
            patch.object(lifecycle, "UberCoordinator", return_value=coordinator),
            patch.object(lifecycle, "create_session"),
            patch.object(lifecycle, "migrate_registry"),
        ):
            self.assertTrue(await lifecycle.async_setup_entry(hass, entry))
        coordinator.async_config_entry_first_refresh.assert_awaited_once()
        self.assertIs(entry.runtime_data, coordinator)
        hass.config_entries.async_forward_entry_setups.assert_awaited_once()

    async def test_upgrade_invalid_credentials_keeps_valid_zone_and_removes_dump(self):
        lifecycle = importlib.import_module(PKG)
        updates = []
        entry = SimpleNamespace(
            domain="simple_uber_eats",
            version=1,
            entry_id="x",
            data={
                "sid": "",
                "session_id": "",
                "time_zone": "America/Mexico_City",
                "account_name": "Name",
                "full_cookie": "SECRET",
            },
        )
        hass = SimpleNamespace(
            config=SimpleNamespace(time_zone="UTC"),
            config_entries=SimpleNamespace(
                async_update_entry=lambda entry, **data: updates.append(data)
            ),
        )
        with patch.object(lifecycle, "migrate_registry"):
            self.assertTrue(await lifecycle.async_migrate_entry(hass, entry))
        self.assertEqual(
            updates[0]["data"],
            {
                "sid": "",
                "session_id": "",
                "time_zone": "America/Mexico_City",
                "account_name": "Name",
            },
        )
        self.assertEqual(updates[0]["version"], 2)

    async def test_unload_shuts_down_only_after_platforms_unloaded(self):
        lifecycle = importlib.import_module(PKG)
        shutdown = AsyncMock()
        entry = SimpleNamespace(runtime_data=SimpleNamespace(async_shutdown=shutdown))
        unload = AsyncMock(return_value=False)
        hass = SimpleNamespace(
            config_entries=SimpleNamespace(async_unload_platforms=unload)
        )
        self.assertFalse(await lifecycle.async_unload_entry(hass, entry))
        shutdown.assert_not_awaited()
        unload.return_value = True
        self.assertTrue(await lifecycle.async_unload_entry(hass, entry))
        shutdown.assert_awaited_once()

    async def test_account_session_has_no_cookie_storage_and_detaches(self):
        import aiohttp
        from homeassistant.core import HomeAssistant
        from yarl import URL

        transport = importlib.import_module(PKG + ".http")
        hass = HomeAssistant("/tmp/simple-uber-eats-v4-new/test-config")
        connector = aiohttp.TCPConnector()
        with patch(
            "homeassistant.helpers.aiohttp_client._async_get_connector",
            return_value=connector,
        ):
            session = transport.create_session(hass)
        try:
            session.cookie_jar.update_cookies(
                {"sid": "other-account"}, response_url=URL("https://www.ubereats.com")
            )
            self.assertEqual(
                dict(
                    session.cookie_jar.filter_cookies(URL("https://www.ubereats.com"))
                ),
                {},
            )
            self.assertFalse(session.connector_owner)
            self.assertIsInstance(session.cookie_jar, aiohttp.DummyCookieJar)
        finally:
            session.detach()
            self.assertFalse(connector.closed)
            await connector.close()
            await hass.async_stop(force=True)
        self.assertTrue(session.closed)

    async def test_probe_failure_always_detaches_session(self):
        flow_mod = importlib.import_module(PKG + ".config_flow")
        model = importlib.import_module(PKG + ".model")
        session = Mock()
        client = SimpleNamespace(
            read_orders=AsyncMock(side_effect=model.ServiceUnavailable())
        )
        flow = flow_mod.UberEatsConfigFlow()
        flow.hass = SimpleNamespace(config=SimpleNamespace(time_zone="UTC"))
        with (
            patch.object(flow_mod, "create_session", return_value=session),
            patch.object(flow_mod, "Gateway", return_value=client),
        ):
            result = await flow.async_step_user(
                {
                    "cookie": "sid=QA.test; uev2.id.session=session-id",
                    "time_zone": "UTC",
                }
            )
        self.assertEqual(result["errors"], {"base": "cannot_connect"})
        session.detach.assert_called_once_with()

    async def test_failed_entry_setup_detaches_session(self):
        lifecycle = importlib.import_module(PKG)
        session = Mock()
        coordinator = SimpleNamespace(
            async_config_entry_first_refresh=AsyncMock(
                side_effect=RuntimeError("failed")
            )
        )
        entry = SimpleNamespace(
            data={"sid": "a", "session_id": "b", "time_zone": "UTC"},
            entry_id="e",
            options={},
            async_on_unload=Mock(),
        )
        hass = SimpleNamespace(
            config=SimpleNamespace(time_zone="UTC"),
            config_entries=SimpleNamespace(async_update_entry=Mock()),
        )
        with (
            patch.object(lifecycle, "create_session", return_value=session),
            patch.object(lifecycle, "UberCoordinator", return_value=coordinator),
            patch.object(lifecycle, "Gateway"),
            self.assertRaises(RuntimeError),
        ):
            await lifecycle.async_setup_entry(hass, entry)
        session.detach.assert_called_once_with()

    async def test_public_entity_compatibility_names_and_attributes(self):
        from datetime import datetime

        model = importlib.import_module(PKG + ".model")
        sensors = importlib.import_module(PKG + ".sensor")
        binaries = importlib.import_module(PKG + ".binary_sensor")
        tracker = importlib.import_module(PKG + ".device_tracker")
        when = datetime.now(UTC)
        order = model.Delivery(
            reference="order",
            status="Visible status!",
            eta=when,
            courier="No Driver Assigned",
            courier_photo="photo",
        )
        entry = SimpleNamespace(
            entry_id="x",
            data={"account_name": "Person"},
            options={"courier_photo": True},
        )
        coordinator = SimpleNamespace(
            data=model.Snapshot((order,)),
            last_update_success=True,
            auth_rejected=False,
            last_connection_attempt=when.isoformat(),
            last_connection_success=when.isoformat(),
        )
        status = sensors.UberSensor(coordinator, entry, "status")
        self.assertEqual(status.extra_state_attributes["active_orders_count"], 1)
        self.assertEqual(status.extra_state_attributes["order_id"], "order")
        self.assertEqual(status.device_info["name"], "Person Uber Eats")
        eta = sensors.UberSensor(coordinator, entry, "eta")
        self.assertEqual(eta.extra_state_attributes["arrival_time"], when)
        courier = sensors.UberSensor(coordinator, entry, "courier")
        self.assertIsNone(courier.native_value)
        self.assertEqual(courier.extra_state_attributes["picture_url"], "photo")
        connected = binaries.UberBinarySensor(coordinator, entry, "account_connected")
        self.assertEqual(
            connected.extra_state_attributes["connection_state"], "connected"
        )
        self.assertEqual(
            connected.extra_state_attributes["last_connection_success"],
            when.isoformat(),
        )
        tracker_entity = tracker.CourierTracker(coordinator, entry)
        self.assertEqual(tracker_entity.state, "idle")
        self.assertEqual(tracker_entity.extra_state_attributes["order_active"], True)
        coordinator.data = model.Snapshot()
        self.assertIsNone(status.native_value)

    async def test_explicit_legacy_import_validates_and_leaves_source_entry(self):
        flow_mod = importlib.import_module(PKG + ".config_flow")
        model = importlib.import_module(PKG + ".model")
        credentials = importlib.import_module(PKG + ".web_session").WebSession(
            "QA.test", "session-id"
        )
        legacy = SimpleNamespace(
            entry_id="old",
            domain="uber_eats",
            title="Legacy",
            data={
                "sid": "QA.test",
                "session_id": "session-id",
                "account_name": "Old Name",
                "time_zone": "UTC",
                "full_cookie": "DROP",
            },
        )
        manager = SimpleNamespace(
            async_entries=lambda domain, legacy=legacy: (
                [legacy] if domain == "uber_eats" else []
            ),
            async_get_entry=lambda entry_id: legacy,
        )
        flow = flow_mod.UberEatsConfigFlow()
        flow.hass = SimpleNamespace(
            config=SimpleNamespace(time_zone="UTC"), config_entries=manager
        )
        client = SimpleNamespace(
            credentials=credentials,
            read_orders=AsyncMock(return_value=model.Snapshot()),
            account_label=AsyncMock(return_value=None),
        )
        with (
            patch.object(flow_mod, "create_session"),
            patch.object(flow_mod, "Gateway", return_value=client),
        ):
            result = await flow.async_step_import_legacy({"legacy_entry_id": "old"})
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"]["legacy_entry_id"], "old")
        self.assertNotIn("full_cookie", result["data"])
        self.assertEqual(legacy.data["full_cookie"], "DROP")

    async def test_legacy_adoption_unloaded_and_conflict_fallback(self):
        from homeassistant.config_entries import ConfigEntryState

        compat = importlib.import_module(PKG + ".compatibility")
        legacy = SimpleNamespace(
            domain="uber_eats",
            state=ConfigEntryState.NOT_LOADED,
            data={"account_name": "Old Name"},
        )
        entry = SimpleNamespace(entry_id="new", data={"legacy_entry_id": "old"})
        entity = SimpleNamespace(
            entity_id="sensor.keep",
            unique_id="uber_eats_Old_Name_driver_eta",
            platform="uber_eats",
            domain="sensor",
            device_id=None,
        )
        registry = SimpleNamespace(
            async_get_entity_id=Mock(return_value=None),
            async_update_entity_platform=Mock(),
        )
        hass = SimpleNamespace(
            config_entries=SimpleNamespace(async_get_entry=lambda entry_id: legacy)
        )
        with (
            patch.object(compat.er, "async_get", return_value=registry),
            patch.object(
                compat.er, "async_entries_for_config_entry", return_value=[entity]
            ),
            patch.object(compat.dr, "async_get"),
            patch.object(compat, "entity_sources", return_value={}),
        ):
            self.assertTrue(compat.adopt_selected_legacy(hass, entry))
            registry.async_update_entity_platform.assert_called_once_with(
                "sensor.keep",
                "simple_uber_eats",
                new_config_entry_id="new",
                new_unique_id="new_eta",
            )
            registry.async_update_entity_platform.reset_mock()
            registry.async_get_entity_id.return_value = "sensor.conflict"
            self.assertFalse(compat.adopt_selected_legacy(hass, entry))
            registry.async_update_entity_platform.assert_not_called()
            registry.async_get_entity_id.return_value = None
            legacy.state = ConfigEntryState.LOADED
            self.assertFalse(compat.adopt_selected_legacy(hass, entry))
            registry.async_update_entity_platform.assert_not_called()

    async def test_shared_label_unions_existing_and_only_owns_our_entities(self):
        compat = importlib.import_module(PKG + ".compatibility")
        owned = SimpleNamespace(
            platform="simple_uber_eats", entity_id="sensor.owned", labels={"personal"}
        )
        unrelated = SimpleNamespace(
            platform="other", entity_id="sensor.other", labels={"other"}
        )
        registry = SimpleNamespace(async_update_entity=Mock())
        labels = SimpleNamespace(
            async_get_label_by_name=Mock(
                return_value=SimpleNamespace(label_id="uber_eats")
            )
        )
        with (
            patch.object(compat.lr, "async_get", return_value=labels),
            patch.object(compat.er, "async_get", return_value=registry),
            patch.object(
                compat.er,
                "async_entries_for_config_entry",
                return_value=[owned, unrelated],
            ),
        ):
            compat.apply_account_label(object(), SimpleNamespace(entry_id="new"))
        registry.async_update_entity.assert_called_once_with(
            "sensor.owned", labels={"personal", "uber_eats"}
        )

    async def test_tracker_reports_frozen_across_telemetry_gap(self):
        model = importlib.import_module(PKG + ".model")
        tracker = importlib.import_module(PKG + ".device_tracker")
        route = (
            model.Fix(100, model.GeoPoint(10, 10)),
            model.Fix(200, model.GeoPoint(20, 20)),
        )
        order = model.Delivery(reference="order", status="On the way", route=route)
        entry = SimpleNamespace(entry_id="e", data={}, options={})
        coordinator = SimpleNamespace(
            data=model.Snapshot((order,)), last_update_success=True
        )
        with patch.object(tracker, "time", return_value=205):
            entity = tracker.CourierTracker(coordinator, entry)
            self.assertEqual(entity.extra_state_attributes["position_mode"], "frozen")

    async def test_explicit_legacy_import_accepts_cookie_dump_without_persisting_it(
        self,
    ):
        flow_mod = importlib.import_module(PKG + ".config_flow")
        model = importlib.import_module(PKG + ".model")
        web = importlib.import_module(PKG + ".web_session")
        for field in ("full_cookie", "cookie"):
            source = {
                field: "sid=QA.account; uev2.id.session=account-session",
                "account_name": "Legacy",
                "time_zone": "UTC",
            }
            legacy = SimpleNamespace(
                entry_id="old", domain="uber_eats", title="Legacy", data=source
            )
            manager = SimpleNamespace(
                async_entries=lambda domain, legacy=legacy: [legacy]
            )
            flow = flow_mod.UberEatsConfigFlow()
            flow.hass = SimpleNamespace(
                config=SimpleNamespace(time_zone="UTC"), config_entries=manager
            )
            client = SimpleNamespace(
                credentials=web.WebSession("QA.account", "account-session"),
                read_orders=AsyncMock(return_value=model.Snapshot()),
            )
            with (
                patch.object(flow_mod, "create_session"),
                patch.object(flow_mod, "Gateway", return_value=client),
            ):
                result = await flow.async_step_import_legacy({"legacy_entry_id": "old"})
            self.assertEqual(result["type"], "create_entry")
            self.assertEqual(
                set(result["data"]),
                {"sid", "session_id", "account_name", "time_zone", "legacy_entry_id"},
            )
            self.assertEqual(result["data"]["sid"], "QA.account")
            self.assertEqual(legacy.data, source)

    async def test_removing_entry_forgets_only_its_retry_state(self):
        lifecycle = importlib.import_module(PKG)
        states = {"one": 2, "other": 1}
        hass = SimpleNamespace(
            data={"simple_uber_eats": {"http_auth_failures": states}}
        )
        await lifecycle.async_remove_entry(hass, SimpleNamespace(entry_id="one"))
        self.assertEqual(states, {"other": 1})

    async def test_validated_renewal_resets_only_its_http_grace_count(self):
        flow_mod = importlib.import_module(PKG + ".config_flow")
        model = importlib.import_module(PKG + ".model")
        web = importlib.import_module(PKG + ".web_session")
        for step in ("reauth_confirm", "reconfigure"):
            counts = {"renewed": 3, "other": 2}
            flow = flow_mod.UberEatsConfigFlow()
            flow.hass = SimpleNamespace(
                config=SimpleNamespace(time_zone="UTC"),
                data={"simple_uber_eats": {"http_auth_failures": counts}},
            )
            entry = SimpleNamespace(
                entry_id="renewed",
                data={"account_name": "Existing", "time_zone": "UTC"},
            )
            client = SimpleNamespace(
                credentials=web.WebSession("QA.account", "account-session"),
                read_orders=AsyncMock(return_value=model.Snapshot()),
                account_label=AsyncMock(return_value=None),
            )
            with (
                patch.object(flow_mod, "create_session"),
                patch.object(flow_mod, "Gateway", return_value=client),
                patch.object(
                    flow, "async_update_reload_and_abort", return_value="done"
                ),
            ):
                self.assertEqual(
                    await flow._credentials_step(
                        step,
                        {
                            "cookie": "sid=QA.account; uev2.id.session=account-session",
                            "time_zone": "UTC",
                        },
                        entry,
                    ),
                    "done",
                )
            self.assertEqual(counts, {"other": 2})
