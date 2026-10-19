"""Cross-integration adoption against real 2026.8 registries."""
import importlib
import unittest
from types import MappingProxyType

import test_review_registry_real as fixtures
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.helpers import device_registry as dr


class AdoptionChecks(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_unloaded_adoption_keeps_device_entity_and_labels(self):
        fixture = fixtures.RealRegistryChecks()
        await fixture.asyncSetUp()
        try:
            legacy = ConfigEntry(
                domain='uber_eats', entry_id='legacy',
                data={'account_name':'Old Name'}, options={}, version=1,
                minor_version=1, source='user',
                state=ConfigEntryState.SETUP_IN_PROGRESS, title='Old Name',
                unique_id=None, discovery_keys=MappingProxyType({}), subentries_data=(),
            )
            fixture.hass.config_entries._entries[legacy.entry_id] = legacy
            fixture.hass.config_entries.async_update_entry(
                fixture.entry, data={**fixture.entry.data, 'legacy_entry_id':'legacy'})
            devices = dr.async_get(fixture.hass)
            device = devices.async_get_or_create(
                config_entry_id='legacy', identifiers={('uber_eats','old-account')}, name='Old Name')
            old = fixture.registry.async_get_or_create(
                'sensor','uber_eats','uber_eats_Old_Name_driver_eta',
                config_entry=legacy,device_id=device.id,suggested_object_id='preserved_eta')
            fixture.registry.async_update_entity(old.entity_id,labels={'personal'})
            legacy._async_set_state(fixture.hass,ConfigEntryState.NOT_LOADED,None)
            compat = importlib.import_module('custom_components.simple_uber_eats.compatibility')
            self.assertTrue(compat.adopt_selected_legacy(fixture.hass,fixture.entry))
            adopted = fixture.registry.async_get(old.entity_id)
            self.assertEqual(adopted.entity_id,'sensor.preserved_eta')
            self.assertEqual(adopted.platform,'simple_uber_eats')
            self.assertEqual(adopted.config_entry_id,'one')
            self.assertEqual(adopted.unique_id,'one_eta')
            self.assertEqual(adopted.device_id,device.id)
            self.assertEqual(adopted.labels,{'personal'})
            self.assertEqual(devices.async_get(device.id).config_entry_id,'one')
        finally:
            await fixture.asyncTearDown()
