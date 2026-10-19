"""Cookie-only setup, renewal, reconfiguration, and private attribute options."""

from zoneinfo import available_timezones

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import DOMAIN, PHONE_OPTION, PHOTO_OPTION
from .http import create_session
from .model import AuthRejected, ServiceUnavailable
from .settings import clean_entry_data, valid_timezone
from .web_session import CookieInputError, WebSession
from .wire import Gateway

TIMEZONES = sorted(available_timezones())


class UberEatsConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 2
    MINOR_VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return UberEatsOptionsFlow()

    async def _credentials_step(self, step_id, user_input=None, entry=None):
        errors = {}
        defaults = entry.data if entry else {}
        timezone = valid_timezone(defaults.get("time_zone"), self.hass.config.time_zone)
        if user_input is not None:
            try:
                credentials = WebSession.parse(user_input["cookie"])
                submitted_zone = user_input["time_zone"]
                if submitted_zone not in TIMEZONES:
                    errors["time_zone"] = "invalid_timezone"
                else:
                    session = create_session(self.hass)
                    try:
                        gateway = Gateway(session, credentials, submitted_zone)
                        await gateway.read_orders()
                        try:
                            name = await gateway.account_label()
                        except (AuthRejected, ServiceUnavailable, TimeoutError):
                            name = None
                        data = {
                            **gateway.credentials.as_entry_data(),
                            "time_zone": submitted_zone,
                            "account_name": name
                            or defaults.get("account_name")
                            or "Uber Eats",
                        }
                    finally:
                        session.detach()
                    if entry is not None:
                        self.hass.data.get(DOMAIN, {}).get(
                            "http_auth_failures", {}
                        ).pop(entry.entry_id, None)
                        return self.async_update_reload_and_abort(
                            entry,
                            data=data,
                            reason="reauth_successful"
                            if step_id == "reauth_confirm"
                            else "reconfigure_successful",
                        )
                    return self.async_create_entry(
                        title=data["account_name"], data=data
                    )
            except CookieInputError:
                errors["cookie"] = "invalid_cookie"
            except AuthRejected:
                errors["base"] = "invalid_auth"
            except (ServiceUnavailable, TimeoutError):
                errors["base"] = "cannot_connect"
        schema = vol.Schema(
            {
                vol.Required("cookie"): selector.TextSelector(
                    selector.TextSelectorConfig(multiline=True)
                ),
                vol.Required("time_zone", default=timezone): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=TIMEZONES, mode=selector.SelectSelectorMode.DROPDOWN
                    )
                ),
            }
        )
        return self.async_show_form(step_id=step_id, data_schema=schema, errors=errors)

    async def async_step_user(self, user_input=None):
        if user_input is None and self.hass.config_entries.async_entries("uber_eats"):
            return self.async_show_menu(
                step_id="user", menu_options=["connect", "import_legacy"]
            )
        return await self._credentials_step("user", user_input)

    async def async_step_connect(self, user_input=None):
        return await self._credentials_step("connect", user_input)

    async def async_step_import_legacy(self, user_input=None):
        entries = self.hass.config_entries.async_entries("uber_eats")
        if not entries:
            return self.async_abort(reason="no_legacy_entries")
        errors = {}
        if user_input is not None:
            selected = next(
                (
                    item
                    for item in entries
                    if item.entry_id == user_input.get("legacy_entry_id")
                ),
                None,
            )
            if selected is None:
                return self.async_abort(reason="no_legacy_entries")
            try:
                try:
                    data = clean_entry_data(selected.data, self.hass.config.time_zone)
                except (CookieInputError, ValueError, TypeError):
                    raw = selected.data.get("full_cookie") or selected.data.get(
                        "cookie"
                    )
                    restored = WebSession.parse(raw)
                    data = clean_entry_data(
                        {**selected.data, **restored.as_entry_data()},
                        self.hass.config.time_zone,
                    )
                credentials = WebSession(data["sid"], data["session_id"])
                session = create_session(self.hass)
                try:
                    gateway = Gateway(session, credentials, data["time_zone"])
                    await gateway.read_orders()
                    data.update(gateway.credentials.as_entry_data())
                finally:
                    session.detach()
                data["legacy_entry_id"] = selected.entry_id
                return self.async_create_entry(
                    title=data.get("account_name") or selected.title or "Uber Eats",
                    data=data,
                )
            except (CookieInputError, ValueError, TypeError, AuthRejected):
                errors["base"] = "invalid_auth"
            except (ServiceUnavailable, TimeoutError):
                errors["base"] = "cannot_connect"
        schema = vol.Schema(
            {
                vol.Required("legacy_entry_id"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": entry.entry_id, "label": entry.title}
                            for entry in entries
                        ]
                    )
                )
            }
        )
        return self.async_show_form(
            step_id="import_legacy", data_schema=schema, errors=errors
        )

    async def async_step_reauth(self, entry_data):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        return await self._credentials_step(
            "reauth_confirm", user_input, self._get_reauth_entry()
        )

    async def async_step_reconfigure(self, user_input=None):
        return await self._credentials_step(
            "reconfigure", user_input, self._get_reconfigure_entry()
        )


class UberEatsOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    PHONE_OPTION: bool(user_input.get(PHONE_OPTION, False)),
                    PHOTO_OPTION: bool(user_input.get(PHOTO_OPTION, False)),
                },
            )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        PHONE_OPTION,
                        default=self.config_entry.options.get(PHONE_OPTION, False),
                    ): bool,
                    vol.Optional(
                        PHOTO_OPTION,
                        default=self.config_entry.options.get(PHOTO_OPTION, False),
                    ): bool,
                }
            ),
        )
