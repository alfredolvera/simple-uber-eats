"""Tests for the pure courier playback helper."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "simple_uber_eats"
    / "tracking_playback.py"
)
SPEC = importlib.util.spec_from_file_location("tracking_playback", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
tracking_playback = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = tracking_playback
SPEC.loader.exec_module(tracking_playback)

PathPointBuffer = tracking_playback.PathPointBuffer
MAX_INTERPOLATION_GAP_MS = tracking_playback.MAX_INTERPOLATION_GAP_MS
advance_playback_epoch = tracking_playback.advance_playback_epoch


def point(epoch: float, latitude: float, longitude: float) -> dict:
    return {"epoch": epoch, "latitude": latitude, "longitude": longitude}


class PathPointBufferTests(unittest.TestCase):
    def test_merge_deduplicates_sorts_and_replaces_epoch(self) -> None:
        buffer = PathPointBuffer()
        buffer.merge(
            [
                point(3000, 3, 3),
                point(1000, 1, 1),
                point(2000, 2, 2),
                point(2000, 2.5, 2.5),
            ]
        )
        self.assertEqual([item.epoch for item in buffer.points], [1000, 2000, 3000])
        self.assertEqual(buffer.points[1].latitude, 2.5)

    def test_overlapping_batches_add_only_new_epochs(self) -> None:
        buffer = PathPointBuffer()
        self.assertEqual(buffer.merge([point(1000, 1, 1), point(2000, 2, 2)]), 2)
        self.assertEqual(buffer.merge([point(2000, 2, 2), point(3000, 3, 3)]), 1)
        self.assertEqual([item.epoch for item in buffer.points], [1000, 2000, 3000])

    def test_interpolation_midpoint_and_exact_point(self) -> None:
        buffer = PathPointBuffer()
        buffer.merge([point(1000, 10, 20), point(3000, 14, 28)])
        midpoint = buffer.position_at(2000)
        exact = buffer.position_at(3000)
        self.assertIsNotNone(midpoint)
        self.assertEqual((midpoint.latitude, midpoint.longitude), (12, 24))
        self.assertFalse(midpoint.exact)
        self.assertIsNotNone(exact)
        self.assertEqual((exact.latitude, exact.longitude), (14, 28))
        self.assertTrue(exact.exact)

    def test_never_extrapolates_beyond_newest(self) -> None:
        buffer = PathPointBuffer()
        buffer.merge([point(1000, 1, 1), point(2000, 2, 2)])
        position = buffer.position_at(9000)
        self.assertEqual(position.epoch, 2000)
        self.assertEqual((position.latitude, position.longitude), (2, 2))

    def test_initial_delay_is_twelve_seconds_and_clamped(self) -> None:
        buffer = PathPointBuffer()
        buffer.merge([point(1000, 1, 1), point(21_000, 2, 2)])
        self.assertEqual(buffer.initial_playback_epoch(), 9000)

        short_buffer = PathPointBuffer()
        short_buffer.merge([point(10_000, 1, 1), point(15_000, 2, 2)])
        self.assertEqual(short_buffer.initial_playback_epoch(), 10_000)

    def test_stationary_points_interpolate_without_movement(self) -> None:
        buffer = PathPointBuffer()
        buffer.merge([point(1000, 5, 6), point(3000, 5, 6)])
        position = buffer.position_at(2000)
        self.assertEqual((position.latitude, position.longitude), (5, 6))

    def test_malformed_and_boolean_coordinates_are_ignored(self) -> None:
        buffer = PathPointBuffer()
        buffer.merge(
            [
                point(1000, True, 1),
                point(2000, 91, 1),
                {"epoch": "bad", "latitude": 1, "longitude": 1},
            ]
        )
        self.assertEqual(buffer.points, ())

    def test_reset_isolates_a_new_order(self) -> None:
        buffer = PathPointBuffer()
        buffer.merge([point(1000, 1, 1), point(2000, 2, 2)])
        buffer.reset()
        buffer.merge([point(9000, 9, 9)])
        self.assertEqual([item.epoch for item in buffer.points], [9000])

    def test_buffer_has_a_hard_point_limit(self) -> None:
        buffer = PathPointBuffer(max_points=1000)
        buffer.merge([point(index * 100 + 1, 1, 1) for index in range(1200)])
        self.assertEqual(len(buffer.points), 1000)

    def test_large_gap_discards_stale_segment_and_prevents_interpolation(self) -> None:
        buffer = PathPointBuffer()
        buffer.merge([point(1_000, 1, 1), point(5_000, 2, 2)])
        buffer.merge(
            [
                point(905_000, 40, -74),
                point(907_000, 40.001, -74.001),
            ],
            preserve_epoch=5_000,
        )

        self.assertTrue(buffer.last_merge_had_discontinuity)
        self.assertEqual(
            [item.epoch for item in buffer.points], [905_000, 907_000]
        )
        position = buffer.position_at(500_000)
        self.assertEqual(position.epoch, 905_000)
        self.assertEqual((position.latitude, position.longitude), (40, -74))
        self.assertTrue(position.exact)

    def test_explicit_outage_freezes_then_rebases_same_order(self) -> None:
        buffer = PathPointBuffer()
        buffer.merge([point(1_000, 1, 1), point(5_000, 2, 2)])
        frozen = buffer.position_at(5_000)

        # No telemetry is merged during the same-order outage, so the last
        # safely displayed real point remains frozen.
        self.assertEqual((frozen.latitude, frozen.longitude), (2, 2))

        # The tracker resets the stale segment when valid telemetry resumes.
        buffer.reset()
        buffer.merge(
            [
                point(900_000, 40, -74),
                point(910_000, 40.01, -74.01),
                point(920_000, 40.02, -74.02),
            ]
        )
        playback_epoch = buffer.initial_playback_epoch()
        resumed = buffer.position_at(playback_epoch)

        self.assertEqual(playback_epoch, 908_000)
        self.assertGreaterEqual(resumed.epoch, 900_000)
        self.assertNotEqual(
            (resumed.latitude, resumed.longitude),
            (frozen.latitude, frozen.longitude),
        )

    def test_rebase_uses_twelve_second_delay_on_fresh_segment(self) -> None:
        buffer = PathPointBuffer()
        buffer.merge([point(1_000, 1, 1), point(5_000, 2, 2)])
        buffer.merge(
            [
                point(900_000, 40, -74),
                point(910_000, 40.01, -74.01),
                point(920_000, 40.02, -74.02),
            ],
            preserve_epoch=5_000,
        )

        self.assertEqual(buffer.initial_playback_epoch(), 908_000)
        self.assertEqual(buffer.newest.epoch - buffer.initial_playback_epoch(), 12_000)

    def test_rebase_delay_clamps_to_oldest_fresh_point(self) -> None:
        buffer = PathPointBuffer()
        buffer.merge([point(1_000, 1, 1), point(5_000, 2, 2)])
        buffer.merge(
            [point(900_000, 40, -74), point(906_000, 40.01, -74.01)],
            preserve_epoch=5_000,
        )

        self.assertEqual(buffer.initial_playback_epoch(), 900_000)

    def test_gap_at_threshold_remains_continuous(self) -> None:
        buffer = PathPointBuffer()
        buffer.merge(
            [
                point(1_000, 1, 1),
                point(1_000 + MAX_INTERPOLATION_GAP_MS, 2, 2),
            ]
        )

        self.assertFalse(buffer.last_merge_had_discontinuity)
        midpoint = buffer.position_at(1_000 + MAX_INTERPOLATION_GAP_MS / 2)
        self.assertFalse(midpoint.exact)
        self.assertEqual((midpoint.latitude, midpoint.longitude), (1.5, 1.5))

    def test_normal_overlapping_telemetry_remains_continuous(self) -> None:
        buffer = PathPointBuffer()
        buffer.merge(
            [point(1_000, 1, 1), point(3_000, 2, 2), point(5_000, 3, 3)]
        )
        buffer.merge(
            [point(3_000, 2, 2), point(5_000, 3, 3), point(7_000, 4, 4)],
            preserve_epoch=2_000,
        )

        self.assertFalse(buffer.last_merge_had_discontinuity)
        self.assertEqual(
            [item.epoch for item in buffer.points], [1_000, 3_000, 5_000, 7_000]
        )
        midpoint = buffer.position_at(6_000)
        self.assertEqual((midpoint.latitude, midpoint.longitude), (3.5, 3.5))


class PlaybackClockTests(unittest.TestCase):
    def test_playback_is_monotonic_and_clamped(self) -> None:
        epoch, recovery, rate = advance_playback_epoch(10_000, 1000, 10_500, False)
        self.assertEqual(epoch, 10_500)
        self.assertGreaterEqual(epoch, 10_000)
        self.assertFalse(recovery)
        self.assertEqual(rate, 1.0)

    def test_late_batch_recovery_is_capped_at_one_point_two(self) -> None:
        epoch, recovery, rate = advance_playback_epoch(10_000, 1000, 30_000, False)
        self.assertTrue(recovery)
        self.assertEqual(rate, 1.2)
        self.assertEqual(epoch, 11_200)

    def test_recovery_hysteresis_returns_to_normal_rate(self) -> None:
        epoch, recovery, rate = advance_playback_epoch(17_400, 1000, 30_000, True)
        self.assertFalse(recovery)
        self.assertEqual(rate, 1.0)
        self.assertEqual(epoch, 18_400)


if __name__ == "__main__":
    unittest.main()
