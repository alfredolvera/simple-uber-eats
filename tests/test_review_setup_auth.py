"""Independent startup-auth retry check using the installed HA coordinator."""
import importlib
import tempfile
import unittest
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock

from homeassistant.config_entries import ConfigEntries, ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady


class SetupAuthGrace(unittest.IsolatedAsyncioTestCase):
    async def test_three_startup_retries_eventually_require_reauth(self):
        runtime = importlib.import_module('custom_components.simple_uber_eats.runtime')
        model = importlib.import_module('custom_components.simple_uber_eats.model')
        WebSession = importlib.import_module('custom_components.simple_uber_eats.web_session').WebSession
        with tempfile.TemporaryDirectory() as directory:
            hass = HomeAssistant(directory)
            hass.config_entries = ConfigEntries(hass, {})
            hass.config_entries._store.set_load_empty()
            await hass.config_entries._store.async_load()
            hass.config_entries._initialized.set()
            entry = ConfigEntry(
                domain='simple_uber_eats', entry_id='review-startup-auth',
                data={'sid': 'QA.test', 'session_id': 'test-session'},
                options={}, version=2, minor_version=1, source='user',
                state=ConfigEntryState.SETUP_IN_PROGRESS, title='Example',
                unique_id=None, discovery_keys=MappingProxyType({}), subentries_data=(),
            )
            hass.config_entries._entries[entry.entry_id] = entry
            gateway = SimpleNamespace(
                credentials=WebSession('QA.test', 'test-session'),
                read_orders=AsyncMock(side_effect=model.AuthRejected(http_status=401)),
            )
            results = []
            try:
                for _attempt in range(3):
                    # Exactly as async_setup_entry does on each setup retry.
                    coordinator = runtime.UberCoordinator(hass, entry, gateway)
                    try:
                        await coordinator.async_config_entry_first_refresh()
                    except ConfigEntryAuthFailed:
                        results.append('reauth')
                    except ConfigEntryNotReady:
                        results.append('retry')
                    finally:
                        await coordinator.async_shutdown()
                self.assertEqual(results, ['retry', 'retry', 'reauth'])
            finally:
                entry._async_set_state(hass, ConfigEntryState.NOT_LOADED, None)
                await hass.async_stop(force=True)
