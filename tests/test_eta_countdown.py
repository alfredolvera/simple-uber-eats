"""Tests for bounded ETA date inference."""
from __future__ import annotations

from datetime import datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).parents[1]
PACKAGE = ROOT / "custom_components" / "simple_uber_eats"
namespace = types.ModuleType("eta_test_package")
namespace.__path__ = [str(PACKAGE)]
sys.modules.setdefault("eta_test_package", namespace)


def load(name: str):
    spec = spec_from_file_location(
        f"eta_test_package.{name}", PACKAGE / f"{name}.py"
    )
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


parsers = load("parsers")


class EtaInferenceTests(unittest.TestCase):
    def test_same_minute_does_not_jump_to_tomorrow(self):
        now = datetime.fromisoformat("2026-08-14T15:33:21-06:00")
        result = parsers.parse_eta("3:33 PM", now)
        self.assertEqual(datetime.fromisoformat("2026-08-14T15:33:00-06:00"), result)

    def test_midnight_rollover_within_delivery_horizon(self):
        now = datetime.fromisoformat("2026-08-14T23:55:00-06:00")
        result = parsers.parse_eta("12:15 AM", now)
        self.assertEqual(datetime.fromisoformat("2026-08-15T00:15:00-06:00"), result)

    def test_slightly_past_eta_stays_today(self):
        now = datetime.fromisoformat("2026-08-14T15:34:45-06:00")
        result = parsers.parse_eta("3:33 PM", now)
        self.assertEqual(datetime.fromisoformat("2026-08-14T15:33:00-06:00"), result)

    def test_recent_past_tolerance_is_five_minutes(self):
        at_boundary = datetime.fromisoformat("2026-08-14T15:05:00-06:00")
        self.assertEqual(
            datetime.fromisoformat("2026-08-14T15:00:00-06:00"),
            parsers.parse_eta("3:00 PM", at_boundary),
        )
        outside_boundary = datetime.fromisoformat("2026-08-14T15:05:01-06:00")
        self.assertIsNone(parsers.parse_eta("3:00 PM", outside_boundary))

    def test_maximum_plausible_future_is_twelve_hours(self):
        now = datetime.fromisoformat("2026-08-14T03:00:00-06:00")
        self.assertEqual(
            datetime.fromisoformat("2026-08-14T15:00:00-06:00"),
            parsers.parse_eta("3:00 PM", now),
        )
        self.assertIsNone(parsers.parse_eta("3:01 PM", now))

    def test_implausible_time_is_rejected_without_day_artifact(self):
        now = datetime.fromisoformat("2026-08-14T15:00:00-06:00")
        self.assertIsNone(parsers.parse_eta("2:00 PM", now))
        self.assertIsNone(parsers.parse_eta("4:00 AM", now))

    def test_range_uses_end_and_inherits_meridiem(self):
        now = datetime.fromisoformat("2026-08-14T15:10:00-06:00")
        result = parsers.parse_eta("3:20 - 3:35 PM", now)
        self.assertEqual((15, 35), (result.hour, result.minute))

    def test_invalid_eta(self):
        now = datetime.fromisoformat("2026-08-14T15:10:00-06:00")
        self.assertIsNone(parsers.parse_eta("soon", now))
        self.assertIsNone(parsers.parse_eta("25:99", now))
if __name__ == "__main__":
    unittest.main()
