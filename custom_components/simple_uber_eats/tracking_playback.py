"""Pure helpers for timestamp-based courier path playback."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable

DEFAULT_PLAYBACK_DELAY_MS = 12_000.0
BUFFER_HISTORY_MS = 180_000.0
MAX_BUFFER_POINTS = 1000
MAX_INTERPOLATION_GAP_MS = 30_000.0
RECOVERY_START_LAG_MS = 2_000.0
RECOVERY_STOP_LAG_MS = 750.0
MAX_PLAYBACK_RATE = 1.2


@dataclass(frozen=True, slots=True)
class PathPoint:
    """One validated real Uber path point."""

    latitude: float
    longitude: float
    epoch: float
    course: float | None = None


@dataclass(frozen=True, slots=True)
class PlaybackPosition:
    """A position resolved on the real Uber timeline."""

    latitude: float
    longitude: float
    epoch: float
    exact: bool


class PathPointBuffer:
    """Bounded, epoch-deduplicated real path-point buffer."""

    def __init__(
        self,
        *,
        history_ms: float = BUFFER_HISTORY_MS,
        max_points: int = MAX_BUFFER_POINTS,
    ) -> None:
        self._history_ms = history_ms
        self._max_points = max_points
        self._points: list[PathPoint] = []
        self._last_merge_had_discontinuity = False

    @property
    def points(self) -> tuple[PathPoint, ...]:
        return tuple(self._points)

    @property
    def oldest(self) -> PathPoint | None:
        return self._points[0] if self._points else None

    @property
    def newest(self) -> PathPoint | None:
        return self._points[-1] if self._points else None

    @property
    def last_merge_had_discontinuity(self) -> bool:
        """Return whether the last merge discarded an older telemetry segment."""
        return self._last_merge_had_discontinuity

    def reset(self) -> None:
        self._points.clear()
        self._last_merge_had_discontinuity = False

    def merge(
        self,
        raw_points: Iterable[Any],
        *,
        preserve_epoch: float | None = None,
    ) -> int:
        """Validate and merge points, returning the count of new epochs."""
        self._last_merge_had_discontinuity = False
        by_epoch = {point.epoch: point for point in self._points}
        old_epochs = set(by_epoch)
        for raw_point in raw_points:
            point = _normalize_point(raw_point)
            if point is not None:
                by_epoch[point.epoch] = point

        points = sorted(by_epoch.values(), key=lambda point: point.epoch)
        if points:
            cutoff = points[-1].epoch - self._history_ms
            anchor = None
            if preserve_epoch is not None:
                anchor = next(
                    (point for point in reversed(points) if point.epoch <= preserve_epoch),
                    None,
                )
            points = [point for point in points if point.epoch >= cutoff]
            if anchor is not None and (not points or anchor.epoch < points[0].epoch):
                points.insert(0, anchor)

            segment_start = 0
            for index, (previous, following) in enumerate(
                zip(points, points[1:]), start=1
            ):
                if following.epoch - previous.epoch > MAX_INTERPOLATION_GAP_MS:
                    segment_start = index
            if segment_start:
                points = points[segment_start:]
                self._last_merge_had_discontinuity = True

            if len(points) > self._max_points:
                points = points[-self._max_points :]

        self._points = points
        return len({point.epoch for point in points} - old_epochs)

    def has_timeline(self) -> bool:
        """Return whether at least two separated points can be interpolated."""
        return (
            len(self._points) >= 2
            and self._points[-1].epoch - self._points[0].epoch >= 1000.0
        )

    def initial_playback_epoch(
        self, delay_ms: float = DEFAULT_PLAYBACK_DELAY_MS
    ) -> float | None:
        """Return the delayed initial epoch, clamped to real buffered history."""
        if not self.has_timeline():
            return None
        return max(self._points[0].epoch, self._points[-1].epoch - delay_ms)

    def position_at(self, epoch: float) -> PlaybackPosition | None:
        """Resolve an epoch without extrapolating outside real telemetry."""
        if not self._points:
            return None
        if epoch <= self._points[0].epoch:
            point = self._points[0]
            return PlaybackPosition(point.latitude, point.longitude, point.epoch, True)
        if epoch >= self._points[-1].epoch:
            point = self._points[-1]
            return PlaybackPosition(point.latitude, point.longitude, point.epoch, True)

        for previous, following in zip(self._points, self._points[1:]):
            if epoch == previous.epoch:
                return PlaybackPosition(
                    previous.latitude, previous.longitude, previous.epoch, True
                )
            if previous.epoch < epoch < following.epoch:
                duration = following.epoch - previous.epoch
                if duration <= 0:
                    return PlaybackPosition(
                        previous.latitude, previous.longitude, previous.epoch, True
                    )
                fraction = (epoch - previous.epoch) / duration
                return PlaybackPosition(
                    previous.latitude
                    + (following.latitude - previous.latitude) * fraction,
                    previous.longitude
                    + (following.longitude - previous.longitude) * fraction,
                    epoch,
                    False,
                )

        point = self._points[-1]
        return PlaybackPosition(point.latitude, point.longitude, point.epoch, True)


def advance_playback_epoch(
    playback_epoch: float,
    elapsed_ms: float,
    newest_epoch: float,
    recovery_active: bool,
    *,
    delay_ms: float = DEFAULT_PLAYBACK_DELAY_MS,
) -> tuple[float, bool, float]:
    """Advance monotonically at 1.0-1.2x with recovery hysteresis."""
    desired_epoch = newest_epoch - delay_ms
    extra_lag = desired_epoch - playback_epoch

    if recovery_active:
        if extra_lag <= RECOVERY_STOP_LAG_MS:
            recovery_active = False
    elif extra_lag >= RECOVERY_START_LAG_MS:
        recovery_active = True

    rate = 1.0
    if recovery_active:
        recovery_fraction = min(
            1.0,
            max(
                0.0,
                (extra_lag - RECOVERY_STOP_LAG_MS)
                / (RECOVERY_START_LAG_MS - RECOVERY_STOP_LAG_MS),
            ),
        )
        rate += (MAX_PLAYBACK_RATE - 1.0) * recovery_fraction

    elapsed_ms = max(0.0, elapsed_ms)
    next_epoch = min(newest_epoch, playback_epoch + elapsed_ms * rate)
    return max(playback_epoch, next_epoch), recovery_active, rate


def _normalize_point(raw_point: Any) -> PathPoint | None:
    """Return a safe point or None for malformed input."""
    if not isinstance(raw_point, dict):
        return None
    if isinstance(raw_point.get("latitude"), bool) or isinstance(
        raw_point.get("longitude"), bool
    ):
        return None
    try:
        latitude = float(raw_point.get("latitude"))
        longitude = float(raw_point.get("longitude"))
        epoch = float(raw_point.get("epoch"))
    except (TypeError, ValueError):
        return None
    if (
        not math.isfinite(latitude)
        or not math.isfinite(longitude)
        or not math.isfinite(epoch)
        or not -90 <= latitude <= 90
        or not -180 <= longitude <= 180
        or epoch <= 0
    ):
        return None

    course = raw_point.get("course")
    if course is not None:
        try:
            course = float(course)
        except (TypeError, ValueError):
            course = None
        if course is not None and not math.isfinite(course):
            course = None
    return PathPoint(latitude, longitude, epoch, course)
