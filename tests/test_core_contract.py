"""Synthetic cases authored from SPEC.md; never production captures."""

import importlib
import sys
import types
import unittest
from datetime import UTC, datetime
from http.cookies import SimpleCookie
from pathlib import Path

# Load core without executing the concurrently authored HA package initializer.
ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "replacement_core"
if PACKAGE not in sys.modules:
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(ROOT / "custom_components/simple_uber_eats")]
    sys.modules[PACKAGE] = package
model = importlib.import_module(PACKAGE + ".model")
session = importlib.import_module(PACKAGE + ".web_session")
wire = importlib.import_module(PACKAGE + ".wire")
motion = importlib.import_module(PACKAGE + ".motion")
NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


def envelope(order=None):
    return {"data": {"orders": [] if order is None else [order]}}


class Cookies(unittest.TestCase):
    def test_formats_and_repr(self):
        for value in (
            "sid=QA.secret; uev2.id.session=other-session; ignored=no",
            "Host: example.test\nCookie: sid=QA.secret; uev2.id.session=other-session",
            "curl 'https://example.test' -H 'Cookie: sid=QA.secret; uev2.id.session=other-session'",
            "curl https://example.test -b 'sid=QA.secret; uev2.id.session=other-session'",
        ):
            item = session.WebSession.parse(value)
            self.assertEqual(
                item.as_entry_data(),
                {"sid": "QA.secret", "session_id": "other-session"},
            )
            self.assertEqual(
                item.cookie_header(), "sid=QA.secret; uev2.id.session=other-session"
            )
            self.assertNotIn("secret", repr(item))
            self.assertNotIn("other", repr(item))

    def test_quoted_and_encoded(self):
        item = session.WebSession.parse('sid="QA.a=b"; uev2.id.session=x%2By-session')
        self.assertEqual(item.sid, "QA.a=b")
        self.assertEqual(item.session_id, "x%2By-session")

    def test_untrusted_fields_and_invalid_input(self):
        for value in (
            "curl 'https://x/?sid=QA.a;uev2.id.session=b-session'",
            "curl https://x --data 'sid=QA.a;uev2.id.session=b-session'",
            "sid=QA.a; sid=QA.b; uev2.id.session=c-session",
            "sid=; uev2.id.session=c-session",
            "curl https://x -b @private",
            "sid=QA.a\r\nInjected: x; uev2.id.session=b-session",
            "sid=QA.a\x00; uev2.id.session=b-session",
        ):
            with (
                self.subTest(value=value),
                self.assertRaises(session.CookieInputError) as caught,
            ):
                session.WebSession.parse(value)
            self.assertNotIn(value, str(caught.exception))

    def test_cookie_data_cannot_be_reinterpreted_as_option(self):
        for source in (
            "curl https://x --data '--cookie=sid=QA.a; uev2.id.session=b-session'",
            "curl https://x --json '-bsid=QA.a; uev2.id.session=b-session'",
            "curl https://x --url '--cookie=sid=QA.a; uev2.id.session=b-session'",
        ):
            with (
                self.subTest(source=source),
                self.assertRaises(session.CookieInputError),
            ):
                session.WebSession.parse(source)

    def test_quoted_value_roundtrip(self):
        item = session.WebSession.parse('sid="QA.a b"; uev2.id.session="c;d-session"')
        self.assertEqual(item.sid, "QA.a b")
        self.assertEqual(item.session_id, "c;d-session")
        parsed = SimpleCookie(item.cookie_header())
        self.assertEqual(parsed["sid"].value, "QA.a b")
        self.assertEqual(parsed["uev2.id.session"].value, "c;d-session")

    def test_quoted_whitespace_is_trimmed_at_browser_boundary(self):
        item = session.WebSession.parse(
            'sid=" QA.leading trailing "; uev2.id.session="x\\y-session"'
        )
        self.assertEqual(item.sid, "QA.leading trailing")
        self.assertEqual(item.session_id, "xy-session")

    def test_rotation_atomic_validated_and_partial(self):
        old = session.WebSession("a", "b")
        cookies = SimpleCookie("sid=new")
        self.assertEqual(old.rotate(cookies).sid, "new")
        self.assertEqual(old.rotate(cookies).session_id, "b")
        with self.assertRaises(session.CookieInputError):
            old.rotate({"sid": "bad\nvalue"})
        self.assertEqual(old.sid, "a")


class Decoder(unittest.TestCase):
    def test_empty_and_bad_envelope(self):
        self.assertIsNone(wire.decode_orders(envelope(), NOW).primary)
        for value in (None, {}, {"data": {}}, {"data": {"orders": None}}):
            with self.assertRaises(model.ServiceUnavailable):
                wire.decode_orders(value, NOW)

    def test_protocol_auth(self):
        for value in (
            {"error": {"code": "SESSION_EXPIRED"}},
            {"data": {"isLoggedIn": False}},
        ):
            with self.assertRaises(model.AuthRejected):
                wire.decode_orders(value, NOW)

    def test_visible_status_courier_restaurant(self):
        order = {
            "uuid": "one",
            "activeOrderOverview": {"title": "Cafe"},
            "contacts": [
                None,
                {"type": "COURIER", "title": "Rider", "formattedPhoneNumber": "+123"},
            ],
            "feedCards": [
                None,
                {
                    "status": {
                        "currentProgress": 3,
                        "title": "12:20–12:35 PM",
                        "titleSummary": {"summary": {"text": "On the way!"}},
                    }
                },
                {"type": "courier", "courier": [{"iconUrl": "https://x/photo"}]},
            ],
            "orderInfo": {
                "storeInfo": {"location": {"LATITUDE": 20, "LONGITUDE": -100}}
            },
        }
        primary = wire.decode_orders(envelope(order), NOW).primary
        self.assertEqual(primary.status, "On the way!")
        self.assertEqual(primary.eta, NOW.replace(minute=35))
        self.assertEqual(primary.restaurant_point, model.GeoPoint(20, -100))
        self.assertEqual(
            (primary.restaurant, primary.courier, primary.courier_phone),
            ("Cafe", "Rider", "+123"),
        )
        self.assertEqual(primary.courier_photo, "https://x/photo")

    def test_first_status_object_is_authoritative(self):
        primary = wire.decode_orders(
            envelope(
                {"feedCards": [{"status": {}}, {"status": {"currentProgress": 5}}]}
            ),
            NOW,
        ).primary
        self.assertEqual(primary.status, "unknown")

    def test_freshest_courier_dedup_and_invalid_fixes(self):
        epoch = NOW.timestamp()
        order = {
            "backgroundFeedCards": [
                None,
                {
                    "mapEntity": [
                        None,
                        {
                            "type": "COURIER",
                            "pathPoints": [
                                {"epochMs": epoch * 1000, "lat": 10, "lng": 20}
                            ],
                        },
                        {
                            "type": "COURIER",
                            "pathPoints": [
                                None,
                                {
                                    "timestamp": epoch + 1,
                                    "latitude": 11,
                                    "longitude": 21,
                                },
                                {"epochMillis": epoch * 1000, "lat": 10, "lon": 20},
                                {"time": (epoch + 1) * 1000000, "lat": 12, "lon": 22},
                                {"time": float("inf"), "lat": 10, "lon": 20},
                                {"time": epoch + 9, "lat": True, "lon": 20},
                                {"time": epoch + 9, "lat": 0, "lon": 0},
                            ],
                        },
                    ]
                },
            ]
        }
        result = wire.decode_orders(envelope(order), NOW).primary
        self.assertEqual(result.courier_point, model.GeoPoint(12, 22))
        self.assertEqual(len(result.route), 2)
        self.assertEqual(tuple(f.at for f in result.route), (epoch, epoch + 1))

    def test_coordinate_without_route(self):
        result = wire.decode_orders(
            envelope(
                {
                    "backgroundFeedCards": [
                        {"mapEntity": [{"type": "COURIER", "lat": 20, "lon": 30}]}
                    ]
                }
            ),
            NOW,
        ).primary
        self.assertEqual(result.courier_point, model.GeoPoint(20, 30))
        self.assertEqual(result.route, ())

    def test_eta_midnight_dst_and_meridiem(self):
        from zoneinfo import ZoneInfo

        def eta(title, now):
            return wire.decode_orders(
                envelope({"feedCards": [{"status": {"title": title}}]}), now
            ).primary.eta

        self.assertEqual(
            eta("11:55 PM–12:10 AM", NOW.replace(hour=23, minute=55)),
            NOW.replace(day=20, hour=0, minute=10),
        )
        self.assertEqual(eta("12:10–12:30 PM", NOW), NOW.replace(minute=30))
        self.assertIsNone(eta("25:30", NOW))
        self.assertEqual(eta("6:30 PM", NOW), NOW.replace(hour=18, minute=30))
        self.assertIsNone(eta("11:50 AM", NOW))
        zone = ZoneInfo("America/New_York")
        gap = datetime(2026, 3, 8, 1, 50, tzinfo=zone)
        self.assertIsNone(eta("2:15 AM", gap))

    def test_fallback_and_malformed_nested(self):
        result = wire.decode_orders(
            envelope(
                {
                    "feedCards": [
                        {
                            "status": {
                                "currentProgress": 4,
                                "timelineSummary": {"text": "Almost there."},
                            }
                        }
                    ],
                    "contacts": {},
                    "backgroundFeedCards": "bad",
                }
            ),
            NOW,
        ).primary
        self.assertEqual(result.status, "Almost there.")
        result = wire.decode_orders(
            envelope({"feedCards": [{"status": {"currentProgress": 2}}]}), NOW
        ).primary
        self.assertEqual(result.status, "picked up")


class Routes(unittest.TestCase):
    def delivery(self, fixes=(), reference="a", point=None):
        return model.Delivery(
            reference=reference,
            route=tuple(model.Fix(t, model.GeoPoint(x, 20)) for t, x in fixes),
            courier_point=point,
        )

    def test_interpolation_freeze_and_reset(self):
        buffer = motion.RouteBuffer()
        self.assertIsNone(buffer.position(100))
        buffer.ingest(self.delivery([(100, 10), (120, 20)]))
        self.assertEqual(buffer.position(122), model.GeoPoint(15, 20))
        self.assertEqual(buffer.position(999), model.GeoPoint(20, 20))
        self.assertEqual(buffer.position(120), model.GeoPoint(20, 20))
        buffer.ingest(self.delivery([], reference="b"))
        self.assertIsNone(buffer.position(999))
        buffer.ingest(self.delivery([(100, 10)]))
        buffer.reset()
        self.assertIsNone(buffer.position(999))

    def test_gap_retains_position_and_late_samples_merge(self):
        buffer = motion.RouteBuffer()
        buffer.ingest(self.delivery([(100, 10), (120, 20)]))
        buffer.ingest(self.delivery())
        self.assertEqual(buffer.position(127), model.GeoPoint(17.5, 20))
        buffer.ingest(self.delivery([(110, 12), (120, 20), (140, 30)]))
        self.assertEqual(buffer.position(142), model.GeoPoint(25, 20))
        buffer.ingest(None)
        self.assertIsNone(buffer.position(200))

    def test_long_gap_holds_then_resumes_contiguous_segment(self):
        buffer = motion.RouteBuffer()
        buffer.ingest(self.delivery([(100, 10), (120, 20)]))
        self.assertEqual(buffer.position(150), model.GeoPoint(20, 20))
        buffer.ingest(self.delivery([(300, 40)]))
        self.assertEqual(buffer.position(305), model.GeoPoint(20, 20))
        buffer.ingest(self.delivery([(310, 50)]))
        self.assertEqual(buffer.position(317), model.GeoPoint(45, 20))

    def test_current_point_does_not_invent_timestamps(self):
        buffer = motion.RouteBuffer()
        buffer.ingest(self.delivery(point=model.GeoPoint(5, 6)))
        self.assertEqual(buffer.position(100), model.GeoPoint(5, 6))
        buffer.ingest(self.delivery())
        self.assertEqual(buffer.position(200), model.GeoPoint(5, 6))


class Response:
    def __init__(self, status=200, payload=None, cookies=None, headers=None):
        self.status, self.payload = status, payload
        self.cookies, self.headers = cookies or {}, headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class Http:
    def __init__(self, response):
        self.response, self.calls = response, []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class Gateways(unittest.IsolatedAsyncioTestCase):
    async def test_request_and_success(self):
        http = Http(Response(payload=envelope(), cookies=SimpleCookie("sid=rotated")))
        gateway = wire.Gateway(http, session.WebSession("a", "b"), UTC)
        self.assertIsNone((await gateway.read_orders()).primary)
        self.assertEqual(gateway.credentials.sid, "rotated")
        url, args = http.calls[0]
        self.assertTrue(url.endswith("getActiveOrdersV1?localeCode=en-US"))
        self.assertEqual(
            args["json"],
            {"orderUuid": None, "timezone": "UTC", "showAppUpsellIllustration": True},
        )
        self.assertEqual(args["headers"]["cookie"], "sid=a; uev2.id.session=b")
        self.assertEqual(args["timeout"].total, 20)

    async def test_failures_and_rotation(self):
        for status, exception in (
            (401, model.AuthRejected),
            (403, model.AuthRejected),
            (429, model.Throttled),
            (500, model.ServiceUnavailable),
        ):
            gateway = wire.Gateway(
                Http(
                    Response(
                        status, cookies={"sid": "new"}, headers={"Retry-After": "120"}
                    )
                ),
                session.WebSession("a", "b"),
                UTC,
            )
            with self.assertRaises(exception) as caught:
                await gateway.read_orders()
            self.assertEqual(gateway.credentials.sid, "new")
            if status == 429:
                self.assertEqual(caught.exception.retry_after, 120)
        gateway = wire.Gateway(
            Http(TimeoutError("secret")), session.WebSession("a", "b"), UTC
        )
        with self.assertRaises(model.ServiceUnavailable) as caught:
            await gateway.read_orders()
        self.assertNotIn("secret", str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)

    async def test_profile_optional_errors_and_auth(self):
        for payload, expected in (
            ({"data": {"firstName": "Ada", "lastName": "Lovelace"}}, "Ada Lovelace"),
            ({}, None),
            (ValueError("secret"), None),
        ):
            gateway = wire.Gateway(
                Http(Response(payload=payload)), session.WebSession("a", "b"), UTC
            )
            self.assertEqual(await gateway.account_label(), expected)
        gateway = wire.Gateway(Http(Response(500)), session.WebSession("a", "b"), UTC)
        self.assertIsNone(await gateway.account_label())
        gateway = wire.Gateway(Http(Response(401)), session.WebSession("a", "b"), UTC)
        with self.assertRaises(model.AuthRejected):
            await gateway.account_label()
