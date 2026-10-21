"""Independent reviewer checks using real framework/transport imports."""
import importlib
import unittest
from datetime import UTC, datetime
from functools import partial
from http.cookies import SimpleCookie
from unittest.mock import patch
from zoneinfo import ZoneInfo

import aiohttp
from yarl import URL

session_module = importlib.import_module('custom_components.simple_uber_eats.web_session')
wire = importlib.import_module('custom_components.simple_uber_eats.wire')


class ReviewChecks(unittest.IsolatedAsyncioTestCase):
    async def test_shared_cookie_jar_cannot_replace_per_account_credentials(self):
        captured = []

        class Captured(Exception):
            pass

        class Request(aiohttp.ClientRequest):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                captured.append(self.headers['Cookie'])
                raise Captured()

        from homeassistant.helpers import aiohttp_client
        http_module = importlib.import_module('custom_components.simple_uber_eats.http')
        connector = aiohttp.TCPConnector()
        # Use the real integration factory and real HA session helper. Only the
        # connector bootstrap and request class are substituted; no socket opens.
        factory = partial(aiohttp_client.async_create_clientsession, request_class=Request)
        with patch.object(aiohttp_client, '_async_get_connector', return_value=connector), patch.object(http_module, 'async_create_clientsession', factory):
            http = http_module.create_session(object())
        try:
            http.cookie_jar.update_cookies(
                SimpleCookie('sid=account_A; uev2.id.session=session_A; unrelated=private'),
                URL('https://www.ubereats.com/'),
            )
            gateway = wire.Gateway(http, session_module.WebSession('account_B', 'session_B'), UTC)
            with self.assertRaises(Captured):
                await gateway.read_orders()
        finally:
            http.detach()
            self.assertFalse(connector.closed)
            await connector.close()
        cookies = SimpleCookie(captured[0])
        self.assertEqual(cookies['sid'].value, 'account_B')
        self.assertEqual(cookies['uev2.id.session'].value, 'session_B')
        self.assertEqual(set(cookies), {'sid', 'uev2.id.session'})

    async def test_fall_back_eta_uses_future_fold(self):
        now = datetime(2026, 11, 1, 1, 50, tzinfo=ZoneInfo('America/New_York'), fold=0)
        decoded = wire.decode_orders({'data': {'orders': [{'feedCards': [{'status': {'title': '1:10 AM'}}]}]}}, now)
        self.assertEqual(decoded.primary.eta.fold, 1)
        self.assertEqual(decoded.primary.eta.timestamp() - now.timestamp(), 1200)
