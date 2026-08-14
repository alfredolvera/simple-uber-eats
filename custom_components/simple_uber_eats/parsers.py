"""Pure normalization of Uber Eats response data."""
from __future__ import annotations

from datetime import datetime
import math
import re
from typing import Any, Callable, Iterable

from .eta_countdown import infer_time_only_eta

AUTH_ERROR_CODES = frozenset({"UNAUTHORIZED", "SESSION_EXPIRED", "INVALID_TOKEN"})

_STATUS_BY_PROGRESS = {
    0: "preparing",
    1: "preparing",
    2: "picked up",
    3: "en route",
    4: "arriving",
    5: "delivered",
}


class MalformedUberResponse(ValueError):
    """The response did not contain the expected protocol envelope."""


def auth_error_code(payload: Any) -> str | None:
    """Return a definitive authentication code from an otherwise valid body."""
    if not isinstance(payload, dict) or not isinstance(payload.get("error"), dict):
        return None
    code = payload["error"].get("code")
    return code if code in AUTH_ERROR_CODES else None


def raw_active_orders(payload: Any) -> list[dict[str, Any]]:
    """Validate the active-orders envelope and return dictionary orders."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise MalformedUberResponse("missing data object")
    orders = payload["data"].get("orders")
    if not isinstance(orders, list):
        raise MalformedUberResponse("orders is not a list")
    return [order for order in orders if isinstance(order, dict)]


def parse_profile(payload: Any, *, require_logged_in: bool = False) -> dict[str, Any] | None:
    """Normalize a logged-in user profile, or reject a logged-out response."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None
    if require_logged_in and data.get("isLoggedIn") is not True:
        return None
    if data.get("isLoggedIn") is False:
        return None
    return {
        "picture_url": data.get("pictureUrl"),
        "first_name": _text(data.get("firstName")) or "",
        "last_name": _text(data.get("lastName")) or "",
        "country_code": _text(data.get("geoIpCountryCode")) or "US",
    }


def normalize_path_points(raw_points: Any) -> list[dict[str, float | int]]:
    """Validate, normalize, sort, and epoch-deduplicate courier samples."""
    if not isinstance(raw_points, list):
        return []
    by_epoch: dict[float, dict[str, float | int]] = {}
    for raw in raw_points:
        if not isinstance(raw, dict):
            continue
        coordinate = coordinates(raw)
        epoch = epoch_milliseconds(raw)
        if coordinate is None or epoch is None or epoch <= 0:
            continue
        latitude, longitude = coordinate
        point: dict[str, float | int] = {
            "latitude": latitude,
            "longitude": longitude,
            "epoch": int(epoch) if epoch.is_integer() else epoch,
        }
        course = finite_number(value_for_keys(raw, "course"))
        if course is not None:
            point["course"] = course
        by_epoch[epoch] = point
    return [by_epoch[key] for key in sorted(by_epoch)]


def select_courier(background_cards: Any) -> tuple[dict[str, Any] | None, dict[str, float] | None, list[dict[str, float | int]]]:
    """Select the freshest valid courier, with response order as tie-breaker."""
    candidates: list[tuple[float, int, dict[str, Any], dict[str, float], list[dict[str, float | int]]]] = []
    for index, entity in enumerate(map_entities(background_cards)):
        if entity.get("type") != "COURIER":
            continue
        coordinate = coordinates(entity)
        if coordinate is None:
            continue
        points = normalize_path_points(entity.get("pathPoints"))
        newest = float(points[-1]["epoch"]) if points else -math.inf
        location = {"lat": coordinate[0], "lon": coordinate[1]}
        course = finite_number(value_for_keys(entity, "course"))
        if course is not None:
            location["course"] = course
        candidates.append((newest, index, entity, location, points))
    if not candidates:
        return None, None, []
    _epoch, _index, raw, location, points = max(candidates, key=lambda item: item[:2])
    return raw, location, points


def locations(background_cards: Any) -> tuple[dict[str, dict[str, float]], list[dict[str, float | int]]]:
    """Return the last valid eater/store and selected courier locations."""
    result: dict[str, dict[str, float]] = {}
    for entity in map_entities(background_cards):
        entity_type = entity.get("type")
        coordinate = coordinates(entity)
        if entity_type in {"EATER", "STORE"} and coordinate is not None:
            result[entity_type] = {"lat": coordinate[0], "lon": coordinate[1]}
    _raw, courier, points = select_courier(background_cards)
    if courier is not None:
        result["COURIER"] = courier
    return result, points


def parse_order(
    raw: dict[str, Any],
    *,
    now: Callable[[], datetime],
    profile: dict[str, Any] | None = None,
    fallback_home: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Normalize one active order without retaining unrelated response data."""
    feed_cards = _dict_list(raw.get("feedCards"))
    contacts = _dict_list(raw.get("contacts"))
    overview = _mapping(raw.get("activeOrderOverview"))
    order_info = _mapping(raw.get("orderInfo"))
    found_locations, path_points = locations(raw.get("backgroundFeedCards"))

    if "STORE" not in found_locations:
        store_info = _mapping(order_info.get("storeInfo"))
        store_coordinate = coordinates(_mapping(store_info.get("location")))
        if store_coordinate is not None:
            found_locations["STORE"] = {
                "lat": store_coordinate[0],
                "lon": store_coordinate[1],
            }

    status = first_status(feed_cards)
    visible_status = visible_status_text(status)
    stage = stage_for_progress(status.get("currentProgress"))
    eta_source = _text(status.get("title"))
    eta = parse_eta(eta_source, now())
    courier_contact = next(
        (item for item in contacts if item.get("type") == "COURIER"),
        contacts[0] if contacts else {},
    )
    courier_picture = next(
        (
            courier[0].get("iconUrl")
            for card in feed_cards
            if card.get("type") == "courier"
            and (courier := _dict_list(card.get("courier")))
        ),
        None,
    )
    customer_infos = _dict_list(order_info.get("customerInfos"))
    customer_picture = customer_infos[0].get("pictureUrl") if customer_infos else None
    courier_location = found_locations.get("COURIER")

    return {
        "order_id": raw.get("uuid"),
        "order_stage": stage,
        "order_status": visible_status or stage,
        "order_status_description": visible_status,
        "restaurant_name": _text(overview.get("title")),
        "driver_name": _text(courier_contact.get("title")),
        "driver_eta_str": eta_source,
        "driver_eta": eta,
        "minutes_remaining": minutes_until(eta, now()),
        "driver_location_lat": courier_location.get("lat") if courier_location else None,
        "driver_location_lon": courier_location.get("lon") if courier_location else None,
        "user_picture_url": customer_picture or (profile or {}).get("picture_url"),
        "driver_picture_url": courier_picture,
        "driver_phone_formatted": (
            courier_contact.get("formattedPhoneNumber")
            or courier_contact.get("phoneNumber")
            or ""
        ),
        "home_location": found_locations.get("EATER") or fallback_home,
        "store_location": found_locations.get("STORE"),
        "driver_location_coords": courier_location,
        "courier_path_points": path_points,
    }


def first_status(feed_cards: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the first status object supplied for the order."""
    for card in feed_cards:
        status = card.get("status")
        if isinstance(status, dict):
            return status
    return {}


def visible_status_text(status: dict[str, Any]) -> str | None:
    """Prefer Uber's visible timeline wording and normalize whitespace only."""
    title_summary = _mapping(status.get("titleSummary"))
    summary = _mapping(title_summary.get("summary"))
    text = _text(summary.get("text"))
    if text:
        return text
    timeline = status.get("timelineSummary")
    if isinstance(timeline, dict):
        timeline = timeline.get("text")
    text = _text(timeline)
    return text


def stage_for_progress(progress: Any) -> str:
    """Return the conservative internal fallback stage."""
    return _STATUS_BY_PROGRESS.get(progress, "unknown")


def parse_eta(value: str | None, now: datetime) -> datetime | None:
    """Parse the last time in Uber's ETA label as a timezone-aware timestamp."""
    if not value or now.tzinfo is None:
        return None
    matches = list(re.finditer(r"(?i)(?<!\d)(\d{1,2}):(\d{2})\s*([ap](?:\.?m\.?)?)?", value))
    if not matches:
        return None
    selected = matches[-1]
    hour, minute = int(selected.group(1)), int(selected.group(2))
    meridiem = selected.group(3) or next(
        (match.group(3) for match in reversed(matches[:-1]) if match.group(3)), None
    )
    if minute > 59:
        return None
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if meridiem.replace(".", "").lower() == "pm" else 0)
    elif not 0 <= hour <= 23:
        return None
    return infer_time_only_eta(now, hour, minute)


def minutes_until(value: datetime | None, now: datetime) -> int | None:
    if value is None:
        return None
    return max(0, int((value - now).total_seconds() // 60))


def epoch_milliseconds(point: Any) -> float | None:
    raw = value_for_keys(point, "epoch", "epochMs", "epochMillis", "timestamp", "timestampMs", "time")
    numeric = finite_number(raw)
    if numeric is None:
        return None
    magnitude = abs(numeric)
    if magnitude < 100_000_000_000:
        numeric *= 1_000
    elif magnitude >= 100_000_000_000_000_000:
        numeric /= 1_000_000
    elif magnitude > 100_000_000_000_000:
        numeric /= 1_000
    return numeric


def coordinates(value: Any) -> tuple[float, float] | None:
    latitude = finite_number(value_for_keys(value, "latitude", "lat"))
    longitude = finite_number(value_for_keys(value, "longitude", "lon", "lng"))
    if latitude is None or longitude is None:
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    if latitude == 0 and longitude == 0:
        return None
    return latitude, longitude


def finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def value_for_keys(value: Any, *names: str) -> Any:
    if not isinstance(value, dict):
        return None
    lookup = {str(key).casefold().replace("_", ""): item for key, item in value.items()}
    for name in names:
        item = lookup.get(name.casefold().replace("_", ""))
        if item is not None:
            return item
    return None


def map_entities(background_cards: Any) -> Iterable[dict[str, Any]]:
    for card in _dict_list(background_cards):
        yield from _dict_list(card.get("mapEntity"))


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    return normalized or None


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dict_list(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
