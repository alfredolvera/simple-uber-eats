"""Account-local headers on a Home Assistant managed shared connection pool."""

from aiohttp import DummyCookieJar
from homeassistant.helpers.aiohttp_client import async_create_clientsession


def create_session(hass):
    """Callers must detach in finally or on entry unload; never close HA's pool."""
    return async_create_clientsession(
        hass,
        auto_cleanup=False,
        connector_owner=False,
        cookie_jar=DummyCookieJar(),
    )
