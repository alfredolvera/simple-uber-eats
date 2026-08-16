"""Uber web protocol primitives with no Home Assistant dependencies."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import re
from typing import Any, Mapping

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
MAX_AUTHENTICATION_INPUT_BYTES = 512 * 1024

CONNECTION_UNKNOWN = "unknown"
CONNECTION_CONNECTED = "connected"
CONNECTION_AUTHENTICATION_FAILED = "authentication_failed"
CONNECTION_TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"


class CookieValidationError(ValueError):
    """Authentication input cannot provide the required Uber credentials."""

    def __init__(self, translation_key: str) -> None:
        super().__init__(translation_key)
        self.translation_key = translation_key


@dataclass(frozen=True, slots=True)
class SessionCredentials:
    """The minimum Uber Eats credentials retained by the integration."""

    sid: str
    session_id: str

    @classmethod
    def from_cookie_header(cls, raw_header: str) -> SessionCredentials:
        """Parse a raw Cookie header and discard every unrelated cookie."""
        if not isinstance(raw_header, str) or not raw_header.strip():
            raise CookieValidationError("session_input_not_found")

        values = _cookie_pairs(raw_header)
        sid = values.get("sid")
        session_id = values.get("uev2.id.session")
        if sid is None:
            raise CookieValidationError("sid_not_found")
        sid = sid.strip()
        if not sid.startswith("QA.") or _contains_control_characters(sid):
            raise CookieValidationError("invalid_sid")
        if session_id is None:
            raise CookieValidationError("session_not_found")
        session_id = session_id.strip()
        if "-" not in session_id or _contains_control_characters(session_id):
            raise CookieValidationError("invalid_session")
        return cls(sid=sid, session_id=session_id)

    @classmethod
    def from_authentication_input(cls, raw_input: str) -> SessionCredentials:
        """Extract credentials from copied cURL, headers, or raw cookies.

        The input is parsed strictly as text. It is never executed, evaluated,
        or passed to a shell.
        """
        if not isinstance(raw_input, str):
            raise CookieValidationError("session_input_not_found")
        if len(raw_input) > MAX_AUTHENTICATION_INPUT_BYTES:
            raise CookieValidationError("session_input_too_large")
        try:
            input_size = len(raw_input.encode("utf-8"))
        except UnicodeEncodeError as err:
            raise CookieValidationError("malformed_session_input") from err
        if input_size > MAX_AUTHENTICATION_INPUT_BYTES:
            raise CookieValidationError("session_input_too_large")
        if not raw_input.strip():
            raise CookieValidationError("session_input_not_found")

        text = raw_input.strip()
        candidates: list[str] = []

        curl_input = _looks_like_curl(text)
        if curl_input:
            candidates.extend(_curl_cookie_candidates(text))
        else:
            # Copied request-header blocks and standalone Cookie headers.
            header_text = re.split(r"\r?\n[ \t]*\r?\n", text, maxsplit=1)[0]
            candidates.extend(
                match.group("value").strip()
                for match in _COOKIE_HEADER_LINE_PATTERN.finditer(header_text)
            )

        # Preserve raw-cookie compatibility only when the complete input has
        # cookie-pair structure; never mine arbitrary URLs, bodies, or prose.
        if not candidates and _looks_like_raw_cookie_header(text):
            candidates.append(text)

        if not candidates:
            error = (
                "malformed_session_input"
                if _looks_like_copied_request(text)
                else "session_input_not_found"
            )
            raise CookieValidationError(error)

        parsed: list[SessionCredentials] = []
        errors: list[CookieValidationError] = []
        for candidate in candidates:
            try:
                parsed.append(cls.from_cookie_header(candidate))
            except CookieValidationError as err:
                errors.append(err)

        if len(candidates) == 1 and errors:
            raise errors[0]
        if (
            errors
            or not parsed
            or any(credentials != parsed[0] for credentials in parsed[1:])
        ):
            raise CookieValidationError("ambiguous_session_input")
        return parsed[0]

    @classmethod
    def from_stored(
        cls, sid: str, session_id: str, full_cookie: str | None
    ) -> SessionCredentials:
        """Load existing entries while ignoring historical extra cookies."""
        del full_cookie
        return cls(sid=sid, session_id=session_id)

    def header(self) -> str:
        """Build the canonical minimum Cookie header."""
        return f"sid={self.sid}; uev2.id.session={self.session_id}"

    def rotated(self, response_cookies: Mapping[str, object]) -> SessionCredentials:
        """Return credentials updated from response cookies."""
        sid = _response_cookie_value(response_cookies, "sid") or self.sid
        session_id = (
            _response_cookie_value(response_cookies, "uev2.id.session")
            or self.session_id
        )
        return SessionCredentials(sid, session_id)


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


_COOKIE_NAME_PATTERN = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
_COOKIE_HEADER_LINE_PATTERN = re.compile(
    r"^[ \t]*cookie[ \t]*:[ \t]*(?P<value>[^\r\n]*)$",
    re.IGNORECASE | re.MULTILINE,
)


def _cookie_pairs(raw_header: str) -> dict[str, str]:
    """Read only required cookie pairs and reject conflicting duplicates."""
    required_names = {"sid", "uev2.id.session"}
    pairs: dict[str, str] = {}
    for fragment in raw_header.split(";"):
        fragment = fragment.strip()
        if not fragment:
            continue
        name, separator, value = fragment.partition("=")
        name = name.strip()
        if not separator or not _COOKIE_NAME_PATTERN.fullmatch(name):
            raise CookieValidationError("malformed_session_input")
        if name not in required_names:
            continue
        value = _normalized_cookie_value(value.strip())
        if name in pairs and pairs[name] != value:
            raise CookieValidationError("ambiguous_session_input")
        pairs[name] = value
    return pairs


def _normalized_cookie_value(value: str) -> str:
    """Remove optional Cookie-header quoting without interpreting its contents."""
    if not value.startswith('"') and not value.endswith('"'):
        return value
    if len(value) < 2 or not (value.startswith('"') and value.endswith('"')):
        raise CookieValidationError("malformed_session_input")
    unquoted = value[1:-1]
    if '"' in unquoted:
        raise CookieValidationError("malformed_session_input")
    return unquoted


def _contains_control_characters(value: str) -> bool:
    """Reject values that could create an invalid outbound HTTP header."""
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _looks_like_raw_cookie_header(value: str) -> bool:
    """Return whether the complete value has cookie-pair structure."""
    if "\n" in value or "\r" in value:
        return False
    fragments = [fragment.strip() for fragment in value.split(";") if fragment.strip()]
    if not fragments:
        return False
    names: set[str] = set()
    for fragment in fragments:
        name, separator, _cookie_value = fragment.partition("=")
        name = name.strip()
        if not separator or not _COOKIE_NAME_PATTERN.fullmatch(name):
            return False
        names.add(name)
    return bool({"sid", "uev2.id.session"} & names)


def _curl_cookie_candidates(value: str) -> list[str]:
    """Extract Cookie values from actual cURL header arguments only."""
    tokens = _curl_tokens(value)
    if tokens is None:
        return []
    candidates: list[str] = []
    for index, token in enumerate(tokens):
        header: str | None = None
        if token in ("-H", "--header"):
            if index + 1 < len(tokens):
                header = tokens[index + 1]
        elif token.startswith("--header="):
            header = token.partition("=")[2]
        if header is None:
            continue
        name, separator, header_value = header.partition(":")
        if separator and name.strip().casefold() == "cookie":
            candidates.append(header_value.strip())
    return candidates


def _curl_tokens(value: str) -> list[str] | None:
    """Tokenize copied cURL text without evaluating any shell behavior."""
    value = re.sub(r"\\\r?\n[ \t]*", " ", value)
    tokens: list[str] = []
    current: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(value):
        character = value[index]
        if quote is None:
            if character.isspace():
                if current:
                    tokens.append("".join(current))
                    current = []
            elif character == "#" and not current:
                break
            elif character in ("'", '"'):
                quote = character
            elif character == "\\" and index + 1 < len(value):
                index += 1
                current.append(value[index])
            else:
                current.append(character)
        elif character == quote:
            quote = None
        elif quote == '"' and character == "\\" and index + 1 < len(value):
            index += 1
            current.append(value[index])
        else:
            current.append(character)
        index += 1
    if quote is not None:
        return None
    if current:
        tokens.append("".join(current))
    return tokens


def _looks_like_curl(value: str) -> bool:
    """Return whether the pasted text starts with a cURL command."""
    return bool(re.match(r"^[ \t]*(?:curl|curl\.exe)(?:\s|$)", value, re.IGNORECASE))


def _looks_like_copied_request(value: str) -> bool:
    """Return whether text appears intended as copied request data."""
    stripped = value.lstrip().casefold()
    if stripped.startswith(("http://", "https://")):
        return False
    return (
        _looks_like_curl(value)
        or "\n" in value
        or "\r" in value
        or bool(
            re.search(
                r"(?:^|\s)(?:-H|--header)(?:\s|=)", value
            )
        )
        or bool(
            re.search(
                r"^[ \t]*[a-z0-9-]+[ \t]*:",
                value,
                re.IGNORECASE | re.MULTILINE,
            )
        )
    )


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
