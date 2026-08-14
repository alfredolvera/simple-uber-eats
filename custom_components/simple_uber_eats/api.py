"""Small asynchronous client for the Uber Eats web endpoints."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .protocol import (
    ACTIVE_ORDERS_URL,
    REQUEST_HEADERS,
    USER_PROFILE_URL,
    SessionCredentials,
    locale_for_timezone,
)


@dataclass(slots=True)
class UberResponse:
    """HTTP result plus any credentials rotated by the server."""

    status: int
    body: Any
    credentials: SessionCredentials


class UberEatsApiClient:
    """Perform only the two web requests used by the integration."""

    def __init__(self, session, credentials: SessionCredentials, time_zone: str) -> None:
        self._session = session
        self.credentials = credentials
        self._time_zone = time_zone

    async def active_orders(self) -> UberResponse:
        payload = {
            "orderUuid": None,
            "timezone": self._time_zone,
            "showAppUpsellIllustration": True,
        }
        return await self._post(ACTIVE_ORDERS_URL, payload)

    async def user_profile(self) -> UberResponse:
        return await self._post(USER_PROFILE_URL, {})

    async def _post(self, base_url: str, payload: dict[str, Any]) -> UberResponse:
        url = f"{base_url}?localeCode={locale_for_timezone(self._time_zone)}"
        headers = {**REQUEST_HEADERS, "Cookie": self.credentials.header()}
        async with self._session.post(url, json=payload, headers=headers) as response:
            self.credentials = self.credentials.rotated(response.cookies)
            body = await response.json() if response.status == 200 else None
            return UberResponse(response.status, body, self.credentials)
