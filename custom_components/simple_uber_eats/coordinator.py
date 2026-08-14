"""Coordinator for authoritative Uber Eats account and order data."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
import hashlib
import logging
import math
import statistics
import time
from typing import Any

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .api import UberEatsApiClient, UberResponse
from .parsers import (
    auth_error_code,
    coordinates,
    epoch_milliseconds,
    parse_order,
    parse_profile,
    raw_active_orders,
    select_courier,
    value_for_keys,
)
from .protocol import (
    CONNECTION_AUTHENTICATION_FAILED,
    CONNECTION_CONNECTED,
    RequestPolicy,
    SessionCredentials,
    next_poll_interval,
    rotated_entry_data,
)

_LOGGER = logging.getLogger(__name__)

USER_PROFILE_REFRESH_INTERVAL = timedelta(hours=6)
class UberEatsCoordinator(DataUpdateCoordinator):
    """Own polling, connectivity state, and normalized account data."""

    def __init__(
        self,
        hass,
        entry_id,
        sid,
        session_id,
        account_name,
        time_zone,
        full_cookie=None,
    ) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.account_name = account_name
        self.time_zone = time_zone
        self._credentials = SessionCredentials.from_stored(sid, session_id, full_cookie)
        self._api = UberEatsApiClient(
            async_get_clientsession(hass), self._credentials, time_zone
        )
        self._cached_user_profile: dict[str, Any] | None = None
        self._profile_attempt_at: datetime | None = None
        self._request_policy = RequestPolicy()
        self.last_connection_attempt: datetime | None = None
        self.last_connection_success: datetime | None = None
        self._tracking_diagnostics: deque[dict[str, Any]] = deque(maxlen=1000)
        self._previous_courier_diagnostics: dict[str | None, dict[str, Any]] = {}
        super().__init__(
            hass,
            _LOGGER,
            name=f"Simple Uber Eats Orders - {account_name}",
            update_interval=next_poll_interval(1, False),
        )

    @property
    def sid(self) -> str:
        return self._credentials.sid

    @property
    def session_id(self) -> str:
        return self._credentials.session_id

    @property
    def full_cookie(self) -> str:
        return self._credentials.header()

    @property
    def consecutive_rate_limits(self) -> int:
        return self._request_policy.rate_limits

    @property
    def rate_limit_backoff_seconds(self) -> float | None:
        interval = self._request_policy.rate_limit_interval
        return interval.total_seconds() if interval else None

    @property
    def connection_state(self) -> str:
        return self._request_policy.state

    @connection_state.setter
    def connection_state(self, value: str) -> None:
        self._request_policy.state = value

    @property
    def tracking_diagnostic_history(self) -> list[dict[str, Any]]:
        return list(self._tracking_diagnostics)

    async def _async_update_data(self) -> dict[str, Any]:
        await self._refresh_profile_when_due()
        started = time.monotonic()
        self.last_connection_attempt = datetime.now(timezone.utc)
        status: int | None = None
        recorded = False
        try:
            response = await self._api.active_orders()
            status = response.status
            received = datetime.now(timezone.utc)
            self._persist_rotated_credentials(response)

            if status in (401, 403):
                self._record_diagnostic(started, received, status, [])
                recorded = True
                if self._request_policy.observe_http_failure(status):
                    raise ConfigEntryAuthFailed("Uber Eats session is no longer valid")
                return self._empty_data()

            if status == 429:
                self._request_policy.observe_http_failure(status)
                self.update_interval = self._request_policy.rate_limit_interval
                self._record_diagnostic(started, received, status, [])
                recorded = True
                _LOGGER.warning(
                    "Uber Eats rate limit received; polling paused for %s seconds",
                    self.update_interval.total_seconds(),
                )
                return self._empty_data()
            if status != 200:
                self._request_policy.observe_http_failure(status)
                self._record_diagnostic(started, received, status, [])
                recorded = True
                return self._empty_data()

            self._request_policy.observe_http_success()

            definitive_auth_error = auth_error_code(response.body)
            if definitive_auth_error:
                self._request_policy.observe_authentication_failure()
                raise ConfigEntryAuthFailed(
                    f"Uber Eats authentication error: {definitive_auth_error}"
                )

            raw_orders = raw_active_orders(response.body)
            profile = self._cached_user_profile or {}
            fallback_home = {
                "lat": self.hass.config.latitude or 0.0,
                "lon": self.hass.config.longitude or 0.0,
            }
            parsed_orders = [
                parse_order(
                    order,
                    now=dt_util.now,
                    profile=profile,
                    fallback_home=fallback_home,
                )
                for order in raw_orders
            ]
            self.update_interval = next_poll_interval(
                len(parsed_orders),
                any(order.get("driver_location_coords") for order in parsed_orders),
            )
            self._record_diagnostic(started, received, status, raw_orders)
            recorded = True
            self._request_policy.observe_valid_success()
            self.last_connection_success = received
            return self._data_for_orders(parsed_orders)
        except ConfigEntryAuthFailed:
            self._request_policy.observe_authentication_failure()
            if not recorded:
                self._record_diagnostic(
                    started, datetime.now(timezone.utc), status, [], "ConfigEntryAuthFailed"
                )
            raise
        except Exception as err:
            self._request_policy.observe_temporary_failure()
            if not recorded:
                self._record_diagnostic(
                    started, datetime.now(timezone.utc), status, [], type(err).__name__
                )
            _LOGGER.warning("Uber Eats refresh failed: %s", type(err).__name__)
            return self._empty_data()

    async def _refresh_profile_when_due(self) -> None:
        now = datetime.now(timezone.utc)
        if self._profile_attempt_at and now - self._profile_attempt_at < USER_PROFILE_REFRESH_INTERVAL:
            return
        self._profile_attempt_at = now
        try:
            response = await self._api.user_profile()
            self._persist_rotated_credentials(response)
            profile = parse_profile(response.body) if response.status == 200 else None
            if profile is not None:
                self._cached_user_profile = profile
                display_name = " ".join(
                    value for value in (profile["first_name"], profile["last_name"]) if value
                )
                if display_name:
                    self.account_name = display_name
        except Exception as err:
            _LOGGER.debug("Uber Eats profile refresh failed: %s", type(err).__name__)
        if self._cached_user_profile is None:
            self._cached_user_profile = self._blank_profile()

    async def fetch_user_profile(self) -> dict[str, Any]:
        """Fetch and normalize a profile for backward-compatible callers."""
        response = await self._api.user_profile()
        self._persist_rotated_credentials(response)
        return parse_profile(response.body) or self._blank_profile()

    def _persist_rotated_credentials(self, response: UberResponse) -> None:
        updated = response.credentials
        if updated.sid == self._credentials.sid and updated.session_id == self._credentials.session_id:
            return
        self._credentials = updated
        entry = self.hass.config_entries.async_get_entry(self.entry_id)
        if entry is not None:
            self.hass.config_entries.async_update_entry(
                entry,
                data=rotated_entry_data(entry.data, updated),
            )

    def _data_for_orders(self, orders: list[dict[str, Any]]) -> dict[str, Any]:
        data = self._empty_data()
        data["orders"] = orders
        data["orders_count"] = len(orders)
        if not orders:
            return data
        first = orders[0]
        data.update(
            {
                "active": True,
                "order_stage": first.get("order_stage"),
                "order_status": first.get("order_status"),
                "driver_name": first.get("driver_name"),
                "driver_eta_str": first.get("driver_eta_str"),
                "driver_eta": first.get("driver_eta"),
                "driver_location_lat": first.get("driver_location_lat"),
                "driver_location_lon": first.get("driver_location_lon"),
                "minutes_remaining": first.get("minutes_remaining"),
                "restaurant_name": first.get("restaurant_name"),
                "order_id": first.get("order_id"),
                "order_status_description": first.get("order_status_description"),
                "user_picture_url": first.get("user_picture_url"),
                "driver_picture_url": first.get("driver_picture_url"),
                "driver_phone_formatted": first.get("driver_phone_formatted", ""),
                "home_location": first.get("home_location"),
                "store_location": first.get("store_location"),
                "driver_location_coords": first.get("driver_location_coords"),
            }
        )
        return data

    def _empty_data(self) -> dict[str, Any]:
        profile = self._cached_user_profile or self._blank_profile()
        return {
            "active": False,
            "orders": [],
            "orders_count": 0,
            "order_stage": None,
            "order_status": None,
            "driver_name": None,
            "driver_eta_str": None,
            "driver_eta": None,
            "driver_location_lat": None,
            "driver_location_lon": None,
            "minutes_remaining": None,
            "restaurant_name": None,
            "order_id": None,
            "order_status_description": None,
            "user_picture_url": profile.get("picture_url"),
            "driver_picture_url": None,
            "driver_phone_formatted": "",
            "home_location": {
                "lat": self.hass.config.latitude or 0.0,
                "lon": self.hass.config.longitude or 0.0,
            },
            "store_location": None,
            "driver_location_coords": None,
            "user_first_name": profile.get("first_name", ""),
            "user_last_name": profile.get("last_name", ""),
        }

    @staticmethod
    def _blank_profile() -> dict[str, Any]:
        return {"picture_url": None, "first_name": "", "last_name": "", "country_code": "US"}

    def _record_diagnostic(
        self,
        started: float,
        received: datetime,
        status: int | None,
        raw_orders: list[dict[str, Any]],
        error_type: str | None = None,
    ) -> None:
        """Append one bounded, coordinate-free tracking observation."""
        try:
            order_records = [self._diagnostic_order(order, received) for order in raw_orders]
            active_keys = {item["order_id_hash"] for item in order_records}
            self._previous_courier_diagnostics = {
                key: value
                for key, value in self._previous_courier_diagnostics.items()
                if key in active_keys
            }
            record: dict[str, Any] = {
                "poll_timestamp_utc": received.isoformat(),
                "request_duration_ms": round((time.monotonic() - started) * 1000, 2),
                "http_status": status,
                "active_order_count": len(raw_orders),
                "polling_interval_seconds": self.update_interval.total_seconds() if self.update_interval else None,
                "consecutive_http_429": self._request_policy.rate_limits,
                "rate_limit_backoff_seconds": self.rate_limit_backoff_seconds,
                "orders": order_records,
            }
            if error_type:
                record["error_type"] = error_type
            self._tracking_diagnostics.append(record)
            _LOGGER.debug(
                "Uber Eats poll: status=%s latency_ms=%s orders=%s interval_s=%s backoff_s=%s",
                status,
                record["request_duration_ms"],
                len(raw_orders),
                record["polling_interval_seconds"],
                record["rate_limit_backoff_seconds"],
            )
        except Exception as err:
            _LOGGER.debug("Tracking diagnostic skipped: %s", type(err).__name__)

    def _diagnostic_order(self, order: dict[str, Any], received: datetime) -> dict[str, Any]:
        order_key = self._short_order_id(order.get("uuid"))
        courier, _location, normalized = select_courier(order.get("backgroundFeedCards"))
        raw_points = courier.get("pathPoints") if courier else None
        raw_points = raw_points if isinstance(raw_points, list) else []
        epochs = [epoch_milliseconds(point) for point in raw_points]
        epochs = sorted(epoch for epoch in epochs if epoch is not None)
        intervals = [right - left for left, right in zip(epochs, epochs[1:])]
        latest = epochs[-1] if epochs else None
        previous = self._previous_courier_diagnostics.get(order_key, {})
        current_epochs = set(epochs)
        latest_point = normalized[-1] if normalized else courier
        current_coordinate = coordinates(latest_point)
        self._previous_courier_diagnostics[order_key] = {
            "latest_epoch": latest,
            "epochs": current_epochs,
            "coordinates": current_coordinate,
        }
        return {
            "order_id_hash": order_key,
            "courier_entity_exists": courier is not None,
            "courier_entity_keys": sorted(str(key) for key in courier) if courier else [],
            "path_points_exists": bool(courier and "pathPoints" in courier),
            "path_points_count": len(raw_points),
            "first_path_point_epoch": epochs[0] if epochs else None,
            "latest_path_point_epoch": latest,
            "latest_path_point_age_ms": round(received.timestamp() * 1000 - latest, 2) if latest else None,
            "path_point_interval_ms": {
                "min": round(min(intervals), 2) if intervals else None,
                "max": round(max(intervals), 2) if intervals else None,
                "average": round(statistics.fmean(intervals), 2) if intervals else None,
                "median": round(statistics.median(intervals), 2) if intervals else None,
            },
            "courier_course": self._safe_scalar(value_for_keys(courier, "course")),
            "courier_heading": self._safe_scalar(value_for_keys(courier, "heading")),
            "latest_path_point_course": self._safe_scalar(value_for_keys(latest_point, "course")),
            "latest_path_point_heading": self._safe_scalar(value_for_keys(latest_point, "heading")),
            "latest_courier_epoch": latest,
            "latest_courier_epoch_changed": latest != previous.get("latest_epoch") if previous.get("latest_epoch") is not None else None,
            "new_path_point_epochs": len(current_epochs - previous.get("epochs", set())),
            "movement_from_previous_sample_m": self._movement(previous.get("coordinates"), current_coordinate),
        }

    @staticmethod
    def _short_order_id(value: Any) -> str | None:
        return hashlib.sha256(str(value).encode()).hexdigest()[:12] if value else None

    @staticmethod
    def _safe_scalar(value: Any) -> Any:
        return value if value is None or isinstance(value, (str, int, float, bool)) else None

    @staticmethod
    def _movement(start, end) -> float | None:
        if start is None or end is None:
            return None
        lat1, lon1 = map(math.radians, start)
        lat2, lon2 = map(math.radians, end)
        a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
        return round(12_742_000 * math.asin(math.sqrt(min(1.0, a))), 2)


__all__ = ["UberEatsCoordinator"]
