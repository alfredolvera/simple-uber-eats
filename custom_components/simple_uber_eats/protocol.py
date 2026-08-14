"""Uber web protocol primitives with no Home Assistant dependencies."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from http.cookies import SimpleCookie
from typing import Any, Iterable, Mapping

ACTIVE_ORDERS_URL = "https://www.ubereats.com/api/getActiveOrdersV1"
USER_PROFILE_URL = "https://www.ubereats.com/_p/api/getUserV1"

REQUEST_HEADERS = {
    "Content-Type": "application/json",
    "X-CSRF-Token": "x",
}

IDLE_INTERVAL = timedelta(seconds=60)
ACTIVE_INTERVAL = timedelta(seconds=15)
TRACKING_INTERVAL = timedelta(seconds=10)
RATE_LIMIT_INTERVALS = (60, 120, 300)

CONNECTION_UNKNOWN = "unknown"
CONNECTION_CONNECTED = "connected"
CONNECTION_AUTHENTICATION_FAILED = "authentication_failed"
CONNECTION_TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"


class CookieValidationError(ValueError):
    """A cookie string cannot provide the required Uber credentials."""

    def __init__(self, translation_key: str) -> None:
        super().__init__(translation_key)
        self.translation_key = translation_key


@dataclass(frozen=True, slots=True)
class SessionCredentials:
    """Credentials and the browser-cookie context retained around them."""

    sid: str
    session_id: str
    cookies: tuple[tuple[str, str], ...]

    @classmethod
    def from_cookie_header(cls, raw_header: str) -> SessionCredentials:
        if not isinstance(raw_header, str) or not raw_header.strip():
            raise CookieValidationError("cookie_too_short")

        pairs = tuple(_cookie_pairs(raw_header))
        values = dict(pairs)
        sid = values.get("sid", "")
        session_id = values.get("uev2.id.session", "")
        if not sid:
            raise CookieValidationError("sid_not_found")
        if not sid.startswith("QA."):
            raise CookieValidationError("invalid_sid")
        if not session_id:
            raise CookieValidationError("session_not_found")
        if "-" not in session_id:
            raise CookieValidationError("invalid_session")
        return cls(sid=sid, session_id=session_id, cookies=pairs)

    @classmethod
    def from_stored(
        cls, sid: str, session_id: str, full_cookie: str | None
    ) -> SessionCredentials:
        pairs = tuple(_cookie_pairs(full_cookie or ""))
        return cls(sid=sid, session_id=session_id, cookies=pairs)

    def header(self) -> str:
        """Build a header with current credentials and preserved browser cookies."""
        ordered: list[tuple[str, str]] = []
        replaced: set[str] = set()
        credential_values = {
            "sid": self.sid,
            "uev2.id.session": self.session_id,
        }
        for name, value in self.cookies:
            if name in credential_values:
                if name not in replaced:
                    ordered.append((name, credential_values[name]))
                    replaced.add(name)
            else:
                ordered.append((name, value))
        for name in ("sid", "uev2.id.session"):
            if name not in replaced:
                ordered.append((name, credential_values[name]))
        return "; ".join(f"{name}={value}" for name, value in ordered)

    def rotated(self, response_cookies: Mapping[str, object]) -> SessionCredentials:
        """Return credentials updated from response cookies, preserving all others."""
        sid = _response_cookie_value(response_cookies, "sid") or self.sid
        session_id = (
            _response_cookie_value(response_cookies, "uev2.id.session")
            or self.session_id
        )
        return SessionCredentials(sid, session_id, self.cookies)


@dataclass(slots=True)
class RequestPolicy:
    """Connection classification and bounded retry counters."""

    state: str = CONNECTION_UNKNOWN
    authentication_failures: int = 0
    rate_limits: int = 0

    def observe_http_failure(self, status: int) -> bool:
        """Classify an HTTP failure; return true when reauth is confirmed."""
        if status in (401, 403):
            self.authentication_failures += 1
            if self.authentication_failures >= 3:
                self.state = CONNECTION_AUTHENTICATION_FAILED
                return True
        else:
            self.authentication_failures = 0
        if status == 429:
            self.rate_limits += 1
        self.state = CONNECTION_TEMPORARILY_UNAVAILABLE
        return False

    def observe_temporary_failure(self) -> None:
        """A transport/server/schema problem is not an authentication verdict."""
        self.state = CONNECTION_TEMPORARILY_UNAVAILABLE

    def observe_valid_success(self) -> None:
        """A valid 200 response conclusively restores connectivity and backoff."""
        self.state = CONNECTION_CONNECTED
        self.authentication_failures = 0
        self.rate_limits = 0

    def observe_http_success(self) -> None:
        """A 200 interrupts a run of transport-level 401/403 responses."""
        self.authentication_failures = 0

    def observe_authentication_failure(self) -> None:
        self.state = CONNECTION_AUTHENTICATION_FAILED

    @property
    def rate_limit_interval(self) -> timedelta | None:
        return rate_limit_delay(self.rate_limits) if self.rate_limits else None


def _cookie_pairs(raw_header: str) -> Iterable[tuple[str, str]]:
    """Read browser-cookie pairs while tolerating non-RFC browser exports."""
    parsed = SimpleCookie()
    try:
        parsed.load(raw_header)
    except Exception:
        parsed = SimpleCookie()
    if parsed:
        yield from ((name, morsel.value) for name, morsel in parsed.items())
        return
    for fragment in raw_header.split(";"):
        name, separator, value = fragment.partition("=")
        if separator and name.strip():
            yield name.strip(), value.strip()


def _response_cookie_value(cookies: Mapping[str, object], name: str) -> str | None:
    item = cookies.get(name)
    value = getattr(item, "value", item)
    return value if isinstance(value, str) and value else None


def locale_for_timezone(time_zone: str) -> str:
    """Return the locale selector accepted by the Uber web endpoints."""
    return "au" if time_zone.startswith("Australia/") else "us"


def next_poll_interval(order_count: int, has_courier: bool) -> timedelta:
    """Choose the coordinator cadence from the last valid order response."""
    if order_count == 0:
        return IDLE_INTERVAL
    return TRACKING_INTERVAL if has_courier else ACTIVE_INTERVAL


def rate_limit_delay(consecutive_responses: int) -> timedelta:
    """Return bounded delay for a positive consecutive-429 count."""
    index = min(max(consecutive_responses, 1), len(RATE_LIMIT_INTERVALS)) - 1
    return timedelta(seconds=RATE_LIMIT_INTERVALS[index])


def rotated_entry_data(
    current: Mapping[str, Any], credentials: SessionCredentials
) -> dict[str, Any]:
    """Copy config-entry data while replacing only credential fields."""
    return {
        **current,
        "sid": credentials.sid,
        "session_id": credentials.session_id,
        "full_cookie": credentials.header(),
    }
