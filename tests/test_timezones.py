"""Tests for runtime-derived configuration timezone choices."""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import patch
import unittest

MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "simple_uber_eats"
    / "timezones.py"
)
SPEC = spec_from_file_location("timezones_under_test", MODULE_PATH)
timezones = module_from_spec(SPEC)
SPEC.loader.exec_module(timezones)


class TimezoneTests(unittest.TestCase):
    def tearDown(self):
        timezones.runtime_time_zones.cache_clear()

    def test_runtime_values_are_sorted_and_cached(self):
        with patch.object(
            timezones,
            "available_timezones",
            return_value={"UTC", "America/Mexico_City", "Asia/Tokyo"},
        ) as available:
            first = timezones.runtime_time_zones()
            second = timezones.runtime_time_zones()
        self.assertEqual(
            ("America/Mexico_City", "Asia/Tokyo", "UTC"), first
        )
        self.assertIs(first, second)
        available.assert_called_once_with()

    def test_configured_zone_is_always_selectable(self):
        with patch.object(
            timezones, "available_timezones", return_value={"UTC", "Asia/Tokyo"}
        ):
            result = timezones.selectable_time_zones("Etc/Custom")
        self.assertEqual(tuple(sorted(result)), result)
        self.assertIn("Etc/Custom", result)
        self.assertEqual(1, result.count("Etc/Custom"))

    def test_no_copied_static_timezone_catalog(self):
        source = MODULE_PATH.read_text()
        self.assertIn("available_timezones()", source)
        self.assertNotIn("TIME_ZONES =", source)


if __name__ == "__main__":
    unittest.main()
