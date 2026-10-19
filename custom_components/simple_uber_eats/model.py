"""Immutable, platform-independent delivery state."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class GeoPoint:
    latitude: float
    longitude: float


@dataclass(frozen=True, slots=True)
class Fix:
    at: float
    point: GeoPoint


@dataclass(frozen=True, slots=True)
class Delivery:
    reference: str = ""
    status: str = "unknown"
    eta: datetime | None = None
    restaurant: str | None = None
    restaurant_point: GeoPoint | None = None
    courier: str | None = None
    courier_point: GeoPoint | None = None
    courier_phone: str | None = None
    courier_photo: str | None = None
    route: tuple[Fix, ...] = ()


@dataclass(frozen=True, slots=True)
class Snapshot:
    deliveries: tuple[Delivery, ...] = ()

    @property
    def primary(self) -> Delivery | None:
        return self.deliveries[0] if self.deliveries else None


class AuthRejected(Exception):
    """The server rejected the session."""

    def __init__(self, http_status: int | None = None) -> None:
        super().__init__("Session authentication rejected")
        self.http_status = http_status


class ServiceUnavailable(Exception):
    """A temporary failure whose public message contains no response data."""

    def __init__(self) -> None:
        super().__init__("Delivery service temporarily unavailable")


class Throttled(ServiceUnavailable):
    def __init__(self, retry_after: float | None = None) -> None:
        super().__init__()
        self.retry_after = retry_after
