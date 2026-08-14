"""Tests for bounded ETA inference and local countdown behavior."""
from __future__ import annotations

from datetime import datetime, timedelta
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


eta = load("eta_countdown")
parsers = load("parsers")


class EtaInferenceTests(unittest.TestCase):
    def test_same_minute_does_not_jump_to_tomorrow(self):
        now = datetime.fromisoformat("2026-08-14T15:33:21-06:00")
        result = parsers.parse_eta("3:33 PM", now)
        self.assertEqual(datetime.fromisoformat("2026-08-14T15:33:00-06:00"), result)
        self.assertEqual(0, eta.remaining_seconds(result, now))

    def test_midnight_rollover_within_delivery_horizon(self):
        now = datetime.fromisoformat("2026-08-14T23:55:00-06:00")
        result = parsers.parse_eta("12:15 AM", now)
        self.assertEqual(datetime.fromisoformat("2026-08-15T00:15:00-06:00"), result)
        self.assertEqual(1200, eta.remaining_seconds(result, now))

    def test_slightly_past_eta_stays_today(self):
        now = datetime.fromisoformat("2026-08-14T15:34:45-06:00")
        result = parsers.parse_eta("3:33 PM", now)
        self.assertEqual(14, result.day)
        self.assertEqual(0, eta.remaining_seconds(result, now))

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


class CountdownClockTests(unittest.TestCase):
    NOW = datetime.fromisoformat("2026-08-14T15:00:00-06:00")

    def test_format_uses_total_minutes(self):
        self.assertEqual("00:09", eta.format_countdown(9))
        self.assertEqual("01:03", eta.format_countdown(63))
        self.assertEqual("72:15", eta.format_countdown(4335))

    def test_seconds_tick_and_clamp_at_zero(self):
        clock = eta.CountdownClock()
        clock.rebase(
            self.NOW + timedelta(seconds=2), wall_now=self.NOW, monotonic_now=10
        )
        self.assertEqual(2, clock.remaining(10))
        self.assertEqual(1, clock.remaining(11))
        self.assertEqual(0, clock.remaining(12))
        self.assertEqual(0, clock.remaining(999))

    def test_authoritative_update_rebases_active_countdown(self):
        clock = eta.CountdownClock()
        clock.rebase(
            self.NOW + timedelta(seconds=10), wall_now=self.NOW, monotonic_now=1
        )
        self.assertEqual(5, clock.remaining(6))
        updated_now = self.NOW + timedelta(seconds=5)
        clock.rebase(
            updated_now + timedelta(seconds=20),
            wall_now=updated_now,
            monotonic_now=6,
        )
        self.assertEqual(20, clock.remaining(6))

    def test_clear_removes_countdown(self):
        clock = eta.CountdownClock()
        clock.rebase(
            self.NOW + timedelta(seconds=10), wall_now=self.NOW, monotonic_now=1
        )
        clock.clear()
        self.assertIsNone(clock.arrival_time)
        self.assertIsNone(clock.remaining(2))


if __name__ == "__main__":
    unittest.main()
