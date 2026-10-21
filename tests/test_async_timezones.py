"""Regression checks for time zone I/O on real HA async entry points."""

import importlib
import threading
import unittest
import zoneinfo
from contextlib import ExitStack, contextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import aiozoneinfo
from test_core_contract import Http, Response, envelope

PKG = "custom_components.simple_uber_eats"
ZONE = "America/Argentina/Salta"


class AsyncTimeZones(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.lifecycle = importlib.import_module(PKG)
        self.flow_mod = importlib.import_module(PKG + ".config_flow")
        self.settings = importlib.import_module(PKG + ".settings")
        self.wire = importlib.import_module(PKG + ".wire")
        self.web = importlib.import_module(PKG + ".web_session")
        self.thread = threading.get_ident()
        self.lookups = []

    @contextmanager
    def no_loop_zone_loading(self):
        original = zoneinfo.ZoneInfo
        original.clear_cache()
        aiozoneinfo.CachedZoneInfo.clear_cache()

        def checked(name):
            self.assertNotEqual(
                threading.get_ident(),
                self.thread,
                f"Blocking ZoneInfo({name!r}) ran on the event loop",
            )
            self.lookups.append(name)
            return original(name)

        with ExitStack() as stack:
            stack.enter_context(patch.object(zoneinfo, "ZoneInfo", side_effect=checked))
            if hasattr(self.settings, "ZoneInfo"):
                stack.enter_context(
                    patch.object(self.settings, "ZoneInfo", side_effect=checked)
                )
            yield

    def http(self):
        http = Http(Response(payload=envelope()))
        http.detach = Mock()
        return http

    def flow(self, legacy=()):
        flow = self.flow_mod.UberEatsConfigFlow()
        flow.hass = SimpleNamespace(
            config=SimpleNamespace(time_zone=ZONE),
            data={},
            config_entries=SimpleNamespace(async_entries=lambda domain: legacy),
        )
        return flow

    async def test_setup_resolves_cold_zone_and_passes_object_to_gateway(self):
        entry = SimpleNamespace(
            data={
                "sid": "a",
                "session_id": "b",
                "time_zone": ZONE,
                "account_name": "Test",
            },
            entry_id="new",
            options={},
            async_on_unload=Mock(),
            add_update_listener=Mock(),
        )
        hass = SimpleNamespace(
            config=SimpleNamespace(time_zone="UTC"),
            config_entries=SimpleNamespace(async_forward_entry_setups=AsyncMock()),
        )
        coordinator = SimpleNamespace(async_config_entry_first_refresh=AsyncMock())
        with (
            patch.object(self.lifecycle, "create_session", return_value=self.http()),
            patch.object(
                self.lifecycle, "UberCoordinator", return_value=coordinator
            ) as make_coordinator,
            patch.object(self.lifecycle, "migrate_registry"),
            patch.object(self.lifecycle, "adopt_selected_legacy"),
            self.no_loop_zone_loading(),
        ):
            self.assertTrue(await self.lifecycle.async_setup_entry(hass, entry))
            gateway = make_coordinator.call_args.args[2]
            self.assertEqual(gateway._timezone.key, ZONE)
        self.assertIn(ZONE, self.lookups)

    async def test_all_credential_flows_resolve_cold_submitted_zone(self):
        for step in ("user", "connect", "reauth_confirm", "reconfigure"):
            with self.subTest(step=step):
                flow = self.flow()
                entry = SimpleNamespace(data={"time_zone": "UTC"}, entry_id="existing")
                http = self.http()
                with (
                    patch.object(self.flow_mod, "create_session", return_value=http),
                    patch.object(
                        self.flow_mod.Gateway,
                        "account_label",
                        new=AsyncMock(return_value="Test"),
                    ),
                    patch.object(flow, "_get_reauth_entry", return_value=entry),
                    patch.object(flow, "_get_reconfigure_entry", return_value=entry),
                    patch.object(
                        flow,
                        "async_update_reload_and_abort",
                        return_value={"type": "abort"},
                    ),
                    self.no_loop_zone_loading(),
                ):
                    result = await getattr(flow, "async_step_" + step)(
                        {
                            "cookie": "sid=QA.test; uev2.id.session=test-session",
                            "time_zone": ZONE,
                        }
                    )
                self.assertEqual(
                    result["type"],
                    "abort"
                    if step in ("reauth_confirm", "reconfigure")
                    else "create_entry",
                )
                self.assertEqual(http.calls[0][1]["json"]["timezone"], ZONE)
                http.detach.assert_called_once()

    async def test_form_defaults_use_async_fallbacks(self):
        for requested, fallback, expected in (
            (None, ZONE, ZONE),
            ("bad/zone", ZONE, ZONE),
            ("Asia/Tokyo", ZONE, "Asia/Tokyo"),
            ("/invalid/path", "bad/fallback", "UTC"),
            (123, None, "UTC"),
        ):
            with self.subTest(requested=requested, fallback=fallback):
                flow = self.flow()
                flow.hass.config.time_zone = fallback
                entry = SimpleNamespace(data={"time_zone": requested})
                with self.no_loop_zone_loading():
                    result = await flow._credentials_step("reconfigure", entry=entry)
                keys = result["data_schema"].schema
                zone_key = next(key for key in keys if str(key) == "time_zone")
                self.assertEqual(zone_key.default(), expected)

    async def test_invalid_submitted_zone_never_contacts_uber(self):
        for name in ("bad/zone", "/invalid/path"):
            flow = self.flow()
            with (
                patch.object(self.flow_mod, "create_session") as create_session,
                self.no_loop_zone_loading(),
            ):
                result = await flow.async_step_user(
                    {
                        "cookie": "sid=QA.test; uev2.id.session=test-session",
                        "time_zone": name,
                    }
                )
            self.assertEqual(result["errors"], {"time_zone": "invalid_timezone"})
            create_session.assert_not_called()

    async def test_listed_but_unresolvable_submitted_zone_is_rejected(self):
        flow = self.flow()
        original = self.flow_mod.dt_util.async_get_time_zone

        async def resolve(name):
            return None if name == "Asia/Tokyo" else await original(name)

        with (
            patch.object(
                self.flow_mod.dt_util, "async_get_time_zone", side_effect=resolve
            ),
            patch.object(self.flow_mod, "create_session") as create_session,
            self.no_loop_zone_loading(),
        ):
            result = await flow.async_step_user(
                {
                    "cookie": "sid=QA.test; uev2.id.session=test-session",
                    "time_zone": "Asia/Tokyo",
                }
            )
        self.assertEqual(result["errors"], {"time_zone": "invalid_timezone"})
        create_session.assert_not_called()

    async def test_legacy_import_resolves_cold_zone_for_both_cookie_formats(self):
        for credentials in (
            {"sid": "QA.test", "session_id": "test-session"},
            {"full_cookie": "sid=QA.test; uev2.id.session=test-session"},
        ):
            source = {**credentials, "time_zone": ZONE}
            entry = SimpleNamespace(data=source, entry_id="old", title="Legacy")
            flow = self.flow(legacy=[entry])
            http = self.http()
            with (
                patch.object(self.flow_mod, "create_session", return_value=http),
                self.no_loop_zone_loading(),
            ):
                result = await flow.async_step_import_legacy({"legacy_entry_id": "old"})
            self.assertEqual(result["type"], "create_entry")
            self.assertEqual(result["data"]["time_zone"], ZONE)
            self.assertNotIn("full_cookie", result["data"])
            self.assertEqual(entry.data, source)
            self.assertEqual(http.calls[0][1]["json"]["timezone"], ZONE)

    async def test_migration_resolves_cold_zone_with_valid_or_invalid_credentials(self):
        for sid in ("QA.test", ""):
            entry = SimpleNamespace(
                domain="simple_uber_eats",
                version=1,
                entry_id="old",
                data={"sid": sid, "session_id": "test-session", "time_zone": ZONE},
            )
            update = Mock()
            hass = SimpleNamespace(
                config=SimpleNamespace(time_zone="UTC"),
                config_entries=SimpleNamespace(async_update_entry=update),
            )
            with (
                patch.object(self.lifecycle, "migrate_registry"),
                self.no_loop_zone_loading(),
            ):
                self.assertTrue(await self.lifecycle.async_migrate_entry(hass, entry))
            self.assertEqual(update.call_args.kwargs["data"]["time_zone"], ZONE)
            self.assertEqual(update.call_args.kwargs["version"], 2)

    async def test_gateway_uses_resolved_zone_for_request_and_local_eta(self):
        zone = zoneinfo.ZoneInfo("America/New_York")
        http = Http(
            Response(
                payload=envelope({"feedCards": [{"status": {"title": "12:30 PM"}}]})
            )
        )

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 10, 21, 12, 0, tzinfo=zone).astimezone(tz)

        with (
            self.no_loop_zone_loading(),
            patch.object(self.wire, "datetime", FixedDateTime),
        ):
            gateway = self.wire.Gateway(http, self.web.WebSession("a", "b"), zone)
            snapshot = await gateway.read_orders()
        self.assertIs(gateway._timezone, zone)
        self.assertEqual(http.calls[0][1]["json"]["timezone"], "America/New_York")
        self.assertEqual(
            snapshot.primary.eta, datetime(2026, 10, 21, 12, 30, tzinfo=zone)
        )
