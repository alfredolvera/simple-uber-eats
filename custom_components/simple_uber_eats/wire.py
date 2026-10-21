"""Decode the documented browser protocol without retaining wire payloads."""

import math
import re
from datetime import datetime, timedelta, tzinfo
from email.utils import parsedate_to_datetime

import aiohttp

from .model import (
    AuthRejected,
    Delivery,
    Fix,
    GeoPoint,
    ServiceUnavailable,
    Snapshot,
    Throttled,
)
from .web_session import CookieInputError, WebSession


def _object(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list:
    return value if isinstance(value, list) else []


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _normalized(value: dict) -> dict:
    return {
        re.sub(r"_", "", key).lower(): item
        for key, item in value.items()
        if isinstance(key, str)
    }


def _point(value: object) -> GeoPoint | None:
    value = _normalized(_object(value))
    lat = _number(value.get("latitude", value.get("lat")))
    lon = _number(value.get("longitude", value.get("lon", value.get("lng"))))
    if (
        lat is None
        or lon is None
        or not -90 <= lat <= 90
        or not -180 <= lon <= 180
        or (lat == 0 and lon == 0)
    ):
        return None
    return GeoPoint(lat, lon)


def _epoch(value: object) -> float | None:
    number = _number(value)
    if number is None or number <= 0:
        return None
    # Magnitude permits all documented aliases to use seconds/ms/us/ns.
    while number >= 100_000_000_000:
        number /= 1000
    return number


def _route(value: object) -> tuple[Fix, ...]:
    samples: dict[float, Fix] = {}
    for raw in _list(value):
        item = _normalized(_object(raw))
        point = _point(item)
        timestamp = None
        for key in (
            "epoch",
            "epochms",
            "epochmillis",
            "timestamp",
            "timestampms",
            "time",
        ):
            if key in item:
                timestamp = _epoch(item[key])
                if timestamp is not None:
                    break
        if point is not None and timestamp is not None:
            samples[timestamp] = Fix(timestamp, point)
    return tuple(samples[key] for key in sorted(samples))


def _check_auth(payload: object) -> None:
    root = _object(payload)
    if (
        _object(root.get("error")).get("code")
        in ("UNAUTHORIZED", "SESSION_EXPIRED", "INVALID_TOKEN")
        or _object(root.get("data")).get("isLoggedIn") is False
    ):
        raise AuthRejected()


def _eta(label: object, now: datetime) -> datetime | None:
    if not isinstance(label, str) or now.tzinfo is None:
        return None
    matches = list(
        re.finditer(
            r"(?<!\d)(\d{1,2}):(\d{2})(?:\s*([AP]\.?M\.?))?(?!\d)", label, re.IGNORECASE
        )
    )
    if not matches:
        return None
    last = matches[-1]
    hour, minute = int(last[1]), int(last[2])
    meridiem = last[3]
    if meridiem is None:
        meridiem = next(
            (match[3] for match in reversed(matches[:-1]) if match[3]), None
        )
    if minute > 59 or hour > 23:
        return None
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if meridiem.replace(".", "").upper() == "PM" else 0)
    candidates = []
    for day in (-1, 0, 1):
        date = now.date() + timedelta(days=day)
        for fold in (0, 1):
            candidate = datetime(
                date.year,
                date.month,
                date.day,
                hour,
                minute,
                tzinfo=now.tzinfo,
                fold=fold,
            )
            # Reject spring-forward local times which do not survive UTC roundtrip.
            roundtrip = datetime.fromtimestamp(candidate.timestamp(), now.tzinfo)
            if roundtrip.replace(tzinfo=None) != candidate.replace(tzinfo=None):
                continue
            delta = candidate.timestamp() - now.timestamp()
            if -300 <= delta <= 43200:
                candidates.append((abs(delta), candidate.timestamp(), candidate))
    return min(candidates, key=lambda value: value[:2])[2] if candidates else None


def _delivery(raw: dict, now: datetime) -> Delivery:
    status: dict = {}
    found_status = False
    photo = None
    for card in _list(raw.get("feedCards")):
        card = _object(card)
        if not found_status and isinstance(card.get("status"), dict):
            status = card["status"]
            found_status = True
        if card.get("type") == "courier" and photo is None:
            couriers = _list(card.get("courier"))
            if couriers:
                photo = _text(_object(couriers[0]).get("iconUrl"))
    summary = _text(
        _object(_object(status.get("titleSummary")).get("summary")).get("text")
    )
    timeline = status.get("timelineSummary")
    summary = summary or _text(timeline) or _text(_object(timeline).get("text"))
    progress = status.get("currentProgress")
    fallback = {
        0: "preparing",
        1: "preparing",
        2: "picked up",
        3: "en route",
        4: "arriving",
        5: "delivered",
    }
    visible = summary or (
        fallback.get(progress, "unknown")
        if isinstance(progress, int) and not isinstance(progress, bool)
        else "unknown"
    )
    courier = phone = None
    for contact in _list(raw.get("contacts")):
        contact = _object(contact)
        if contact.get("type") == "COURIER":
            courier = _text(contact.get("title"))
            phone = _text(contact.get("formattedPhoneNumber")) or _text(
                contact.get("phoneNumber")
            )
            break
    store = None
    best_route: tuple[Fix, ...] = ()
    current_point = None
    best_at = -math.inf
    for card in _list(raw.get("backgroundFeedCards")):
        for item in _list(_object(card).get("mapEntity")):
            item = _object(item)
            kind = item.get("type")
            if kind == "STORE":
                store = _point(item) or _point(item.get("location")) or store
            elif kind == "COURIER":
                route = _route(item.get("pathPoints"))
                point = (
                    route[-1].point
                    if route
                    else _point(item) or _point(item.get("location"))
                )
                at = route[-1].at if route else -math.inf
                if point is not None and at >= best_at:
                    best_at, best_route, current_point = at, route, point
    if store is None:
        store = _point(
            _object(_object(raw.get("orderInfo")).get("storeInfo")).get("location")
        )
    return Delivery(
        reference=_text(raw.get("uuid")) or "",
        status=" ".join(visible.split()),
        eta=_eta(status.get("title"), now),
        restaurant=_text(_object(raw.get("activeOrderOverview")).get("title")),
        restaurant_point=store,
        courier=courier,
        courier_point=current_point,
        courier_phone=phone,
        courier_photo=photo,
        route=best_route,
    )


def decode_orders(payload: object, now: datetime) -> Snapshot:
    _check_auth(payload)
    orders = _object(_object(payload).get("data")).get("orders")
    if not isinstance(orders, list):
        raise ServiceUnavailable()
    # A malformed order is a temporary protocol failure, never a false completion.
    if any(not isinstance(order, dict) for order in orders):
        raise ServiceUnavailable()
    return Snapshot(tuple(_delivery(order, now) for order in orders))


class Gateway:
    def __init__(self, http_session, credentials: WebSession, timezone: tzinfo) -> None:
        self._http = http_session
        self.credentials = credentials
        self._timezone = timezone
        self._timezone_name = getattr(timezone, "key", "UTC")

    async def _request(self, path: str, body: dict) -> object:
        headers = {
            "content-type": "application/json",
            "x-csrf-token": "x",
            "origin": "https://www.ubereats.com",
            "referer": "https://www.ubereats.com/",
            "cookie": self.credentials.cookie_header(),
        }
        try:
            async with self._http.post(
                f"https://www.ubereats.com{path}?localeCode=en-US",
                json=body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as response:
                try:
                    self.credentials = self.credentials.rotate(response.cookies)
                except CookieInputError:
                    # An invalid server rotation must not poison the established session.
                    pass
                if response.status in (401, 403):
                    raise AuthRejected(http_status=response.status)
                if response.status == 429:
                    retry = None
                    raw_retry = response.headers.get("Retry-After")
                    try:
                        retry = float(raw_retry)
                        if not math.isfinite(retry) or retry < 0:
                            retry = None
                    except (ValueError, TypeError, OverflowError):
                        try:
                            retry = max(
                                0,
                                parsedate_to_datetime(raw_retry).timestamp()
                                - datetime.now().timestamp(),
                            )
                        except (ValueError, TypeError, OverflowError):
                            pass
                    raise Throttled(retry)
                if response.status < 200 or response.status >= 300:
                    raise ServiceUnavailable()
                payload = await response.json()
                _check_auth(payload)
                return payload
        except (AuthRejected, ServiceUnavailable):
            raise
        except (
            aiohttp.ClientError,
            TimeoutError,
            ValueError,
            TypeError,
            OverflowError,
        ):
            raise ServiceUnavailable() from None

    async def read_orders(self) -> Snapshot:
        payload = await self._request(
            "/api/getActiveOrdersV1",
            {
                "orderUuid": None,
                "timezone": self._timezone_name,
                "showAppUpsellIllustration": True,
            },
        )
        return decode_orders(payload, datetime.now(self._timezone))

    async def account_label(self) -> str | None:
        try:
            payload = await self._request("/_p/api/getUserV1", {})
        except ServiceUnavailable:
            return None
        data = _object(_object(payload).get("data"))
        names = [_text(data.get(key)) for key in ("firstName", "lastName")]
        return " ".join(name.strip() for name in names if name) or None
