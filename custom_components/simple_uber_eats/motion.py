"""Display measured paths after a small delay without extrapolation."""

import math

from .model import Delivery, Fix, GeoPoint


def _valid(point: GeoPoint) -> bool:
    return (
        not isinstance(point.latitude, bool)
        and not isinstance(point.longitude, bool)
        and math.isfinite(point.latitude)
        and math.isfinite(point.longitude)
        and -90 <= point.latitude <= 90
        and -180 <= point.longitude <= 180
        and (point.latitude != 0 or point.longitude != 0)
    )


class RouteBuffer:
    def __init__(self, delay: float = 12.0) -> None:
        self.delay = max(0.0, delay)
        self.reset()

    def reset(self) -> None:
        self._reference: str | None = None
        self._fixes: tuple[Fix, ...] = ()
        self._last: GeoPoint | None = None
        self._display_at = -math.inf

    def ingest(self, delivery: Delivery | None) -> None:
        if delivery is None:
            self.reset()
            return
        if self._reference != delivery.reference:
            self.reset()
            self._reference = delivery.reference
        samples = {fix.at: fix for fix in self._fixes}
        for fix in delivery.route:
            if (
                not isinstance(fix.at, bool)
                and math.isfinite(fix.at)
                and _valid(fix.point)
            ):
                samples[fix.at] = fix
        self._fixes = tuple(samples[at] for at in sorted(samples)[-512:])
        if (
            not self._fixes
            and delivery.courier_point is not None
            and _valid(delivery.courier_point)
        ):
            self._last = delivery.courier_point

    def position(self, now: float) -> GeoPoint | None:
        if not math.isfinite(now):
            return self._last
        target = max(self._display_at, now - self.delay)
        if not self._fixes:
            return self._last
        result = self._fixes[0].point
        for index, right in enumerate(self._fixes):
            if right.at > target:
                if index == 0:
                    break
                left = self._fixes[index - 1]
                if right.at - left.at > 60:
                    result = self._last or left.point
                else:
                    ratio = (target - left.at) / (right.at - left.at)
                    # Follow the shorter longitude arc, including across the date line.
                    lon_delta = (
                        right.point.longitude - left.point.longitude + 180
                    ) % 360 - 180
                    lon = (left.point.longitude + ratio * lon_delta + 180) % 360 - 180
                    result = GeoPoint(
                        left.point.latitude
                        + ratio * (right.point.latitude - left.point.latitude),
                        lon,
                    )
                break
            result = right.point
        else:
            # One fresh sample after a long outage cannot define a safe path yet.
            if len(self._fixes) > 1 and self._fixes[-1].at - self._fixes[-2].at > 60:
                result = self._last or self._fixes[-2].point
        self._last = result
        self._display_at = target
        return result
