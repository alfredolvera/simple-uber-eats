"""Config and credential-recovery flows for Simple Uber Eats."""
from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Mapping

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import UberEatsApiClient
from .const import (
    CONF_ACCOUNT_NAME,
    CONF_COOKIE,
    CONF_FULL_COOKIE,
    CONF_LEGACY_ENTRY_ID,
    CONF_SESSION_ID,
    CONF_SID,
    CONF_TIME_ZONE,
    DOMAIN,
    LEGACY_DOMAIN,
)
from .parsers import MalformedUberResponse, parse_profile, raw_active_orders
from .protocol import CookieValidationError, SessionCredentials
from .timezones import selectable_time_zones

_LOGGER = logging.getLogger(__name__)

CONF_CONFIRM_IMPORT = "confirm_import"
CONF_LEGACY_SELECTION = "legacy_entry"
UBER_EATS_HOST = "www.ubereats.com"
UBER_EATS_URL = "https://www.ubereats.com/"


async def _probe_active_orders(
    hass, credentials: SessionCredentials, time_zone: str
) -> bool:
    """Validate credentials using the normal active-order operation."""
    client = UberEatsApiClient(async_get_clientsession(hass), credentials, time_zone)
    response = await client.active_orders()
    if response.status != 200:
        return False
    try:
        raw_active_orders(response.body)
    except MalformedUberResponse:
        return False
    return True


async def _probe_profile(
    hass, credentials: SessionCredentials, time_zone: str
) -> dict[str, Any] | None:
    """Read identity data used to title a newly created entry."""
    client = UberEatsApiClient(async_get_clientsession(hass), credentials, time_zone)
    response = await client.user_profile()
    return (
        parse_profile(response.body, require_logged_in=True)
        if response.status == 200
        else None
    )


def _cookie_credentials(raw: str) -> tuple[SessionCredentials | None, str | None]:
    try:
        return SessionCredentials.from_cookie_header(raw), None
    except CookieValidationError as err:
        return None, err.translation_key


class UberEatsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Manage initial setup, reconfiguration, and reauthentication."""

    VERSION = 2

    def __init__(self) -> None:
        super().__init__()
        self._legacy_entry_id: str | None = None
        self._skip_legacy_import = False

    def _available_legacy_entries(self) -> list[config_entries.ConfigEntry]:
        """Return deterministic legacy candidates not imported or duplicated."""
        current_entries = self.hass.config_entries.async_entries(DOMAIN)
        imported_ids = {
            entry.data.get(CONF_LEGACY_ENTRY_ID) for entry in current_entries
        }
        current_sids = {
            entry.data.get(CONF_SID)
            for entry in current_entries
            if entry.data.get(CONF_SID)
        }
        current_sessions = {
            entry.data.get(CONF_SESSION_ID)
            for entry in current_entries
            if entry.data.get(CONF_SESSION_ID)
        }
        candidates = [
            entry
            for entry in self.hass.config_entries.async_entries(LEGACY_DOMAIN)
            if entry.entry_id not in imported_ids
            and entry.data.get(CONF_SID) not in current_sids
            and entry.data.get(CONF_SESSION_ID) not in current_sessions
        ]
        return sorted(
            candidates, key=lambda entry: (entry.title.casefold(), entry.entry_id)
        )

    def _current_credentials_exist(self, credentials: SessionCredentials) -> bool:
        return any(
            entry.data.get(CONF_SID) == credentials.sid
            or entry.data.get(CONF_SESSION_ID) == credentials.session_id
            for entry in self.hass.config_entries.async_entries(DOMAIN)
        )

    @staticmethod
    def _legacy_credentials(
        entry: config_entries.ConfigEntry,
    ) -> SessionCredentials | None:
        sid = entry.data.get(CONF_SID)
        session_id = entry.data.get(CONF_SESSION_ID)
        full_cookie = entry.data.get(CONF_FULL_COOKIE)
        if not isinstance(sid, str) or not isinstance(session_id, str):
            return None
        if full_cookie is not None and not isinstance(full_cookie, str):
            return None
        try:
            stored = SessionCredentials.from_stored(sid, session_id, full_cookie)
            return SessionCredentials.from_cookie_header(stored.header())
        except CookieValidationError:
            return None

    async def async_step_user(self, user_input=None) -> FlowResult:
        if user_input is None and not self._skip_legacy_import:
            legacy_entries = self._available_legacy_entries()
            if len(legacy_entries) == 1:
                self._legacy_entry_id = legacy_entries[0].entry_id
                return await self.async_step_legacy_import()
            if legacy_entries:
                return await self.async_step_legacy_select()
        return await self._async_step_manual(user_input)

    async def async_step_legacy_select(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Select one legacy account without exposing its credentials."""
        candidates = self._available_legacy_entries()
        by_id = {entry.entry_id: entry for entry in candidates}
        if user_input is not None:
            selected = user_input.get(CONF_LEGACY_SELECTION)
            if selected in by_id:
                self._legacy_entry_id = selected
                return await self.async_step_legacy_import()

        title_counts = Counter(entry.title for entry in candidates)
        choices = {
            entry.entry_id: (
                entry.title
                if title_counts[entry.title] == 1
                else f"{entry.title} ({entry.entry_id[-6:]})"
            )
            for entry in candidates
        }
        return self.async_show_form(
            step_id="legacy_select",
            data_schema=vol.Schema(
                {vol.Required(CONF_LEGACY_SELECTION): vol.In(choices)}
            ),
            errors={"base": "legacy_entry_unavailable"} if user_input is not None else {},
        )

    async def async_step_legacy_import(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm and validate a supported copy of one legacy config entry."""
        candidates = {
            entry.entry_id: entry for entry in self._available_legacy_entries()
        }
        legacy_entry = candidates.get(self._legacy_entry_id)
        if legacy_entry is None:
            return self.async_abort(reason="legacy_entry_unavailable")

        if user_input is None:
            return self.async_show_form(
                step_id="legacy_import",
                data_schema=vol.Schema(
                    {vol.Required(CONF_CONFIRM_IMPORT, default=True): bool}
                ),
                description_placeholders={"account_name": legacy_entry.title},
            )
        if not user_input.get(CONF_CONFIRM_IMPORT, False):
            self._skip_legacy_import = True
            self._legacy_entry_id = None
            return await self._async_step_manual(None)

        credentials = self._legacy_credentials(legacy_entry)
        configured_time_zone = self.hass.config.time_zone or "UTC"
        time_zone = legacy_entry.data.get(CONF_TIME_ZONE, configured_time_zone)
        if not isinstance(time_zone, str):
            time_zone = configured_time_zone
        valid = False
        profile = None
        if credentials is not None and not self._current_credentials_exist(credentials):
            try:
                valid = await _probe_active_orders(self.hass, credentials, time_zone)
                profile = (
                    await _probe_profile(self.hass, credentials, time_zone)
                    if valid
                    else None
                )
            except Exception as err:
                _LOGGER.debug("Legacy credential probe failed: %s", type(err).__name__)
        if not valid or profile is None or credentials is None:
            return self.async_show_form(
                step_id="legacy_import",
                data_schema=vol.Schema(
                    {vol.Required(CONF_CONFIRM_IMPORT, default=True): bool}
                ),
                errors={"base": "legacy_invalid_credentials"},
                description_placeholders={"account_name": legacy_entry.title},
            )

        profile_title = " ".join(
            part for part in (profile["first_name"], profile["last_name"]) if part
        )
        account_name = legacy_entry.data.get(CONF_ACCOUNT_NAME)
        if not isinstance(account_name, str) or not account_name.strip():
            account_name = legacy_entry.title or profile_title or "Uber Eats Account"
        title = legacy_entry.title or account_name
        return self.async_create_entry(
            title=title,
            data={
                CONF_SID: credentials.sid,
                CONF_SESSION_ID: credentials.session_id,
                CONF_FULL_COOKIE: credentials.header(),
                CONF_ACCOUNT_NAME: account_name,
                CONF_TIME_ZONE: time_zone,
                CONF_LEGACY_ENTRY_ID: legacy_entry.entry_id,
            },
            options=dict(legacy_entry.options),
        )

    async def _async_step_manual(self, user_input=None) -> FlowResult:
        errors: dict[str, str] = {}
        configured_time_zone = self.hass.config.time_zone or "UTC"
        if user_input is not None:
            selected_time_zone = user_input.get(CONF_TIME_ZONE, configured_time_zone)
            raw_cookie = user_input.get(CONF_COOKIE, "").strip()
            if selected_time_zone != configured_time_zone:
                errors[CONF_TIME_ZONE] = "invalid_time_zone"
            credentials, cookie_error = _cookie_credentials(raw_cookie)
            if cookie_error:
                errors[CONF_COOKIE] = cookie_error
            if not errors and credentials is not None:
                try:
                    valid = await _probe_active_orders(
                        self.hass, credentials, configured_time_zone
                    )
                    profile = (
                        await _probe_profile(self.hass, credentials, configured_time_zone)
                        if valid
                        else None
                    )
                except Exception as err:
                    _LOGGER.debug("Credential probe failed: %s", type(err).__name__)
                    valid, profile = False, None
                if valid and profile is not None:
                    if self._current_credentials_exist(credentials):
                        return self.async_abort(reason="already_configured")
                    title = " ".join(
                        part
                        for part in (profile["first_name"], profile["last_name"])
                        if part
                    ) or "Uber Eats Account"
                    return self.async_create_entry(
                        title=title,
                        data={
                            CONF_SID: credentials.sid,
                            CONF_SESSION_ID: credentials.session_id,
                            CONF_FULL_COOKIE: raw_cookie,
                            CONF_ACCOUNT_NAME: title,
                            CONF_TIME_ZONE: configured_time_zone,
                        },
                    )
                errors["base"] = "invalid_credentials"

        zones = selectable_time_zones(configured_time_zone)
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_TIME_ZONE, default=configured_time_zone): vol.In(zones),
                    vol.Required(CONF_COOKIE): str,
                }
            ),
            errors=errors,
            description_placeholders={
                "uber_eats_host": UBER_EATS_HOST,
                "uber_eats_url": UBER_EATS_URL,
            },
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self._async_credential_update("reconfigure", entry, user_input)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> FlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self._async_credential_update("reauth_confirm", entry, user_input)

    async def _async_credential_update(self, step_id, entry, user_input) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            raw_cookie = user_input.get(CONF_COOKIE, "").strip()
            credentials, cookie_error = _cookie_credentials(raw_cookie)
            if cookie_error:
                errors[CONF_COOKIE] = cookie_error
            elif credentials is not None:
                try:
                    valid = await _probe_active_orders(
                        self.hass, credentials, entry.data[CONF_TIME_ZONE]
                    )
                except Exception as err:
                    _LOGGER.debug("Credential update probe failed: %s", type(err).__name__)
                    valid = False
                if valid:
                    return self.async_update_reload_and_abort(
                        entry,
                        data={
                            **entry.data,
                            CONF_SID: credentials.sid,
                            CONF_SESSION_ID: credentials.session_id,
                            CONF_FULL_COOKIE: raw_cookie,
                        },
                    )
                errors["base"] = "invalid_credentials"
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema({vol.Required(CONF_COOKIE): str}),
            errors=errors,
            description_placeholders={
                "account_name": entry.data[CONF_ACCOUNT_NAME],
                "uber_eats_url": UBER_EATS_URL,
            },
        )
