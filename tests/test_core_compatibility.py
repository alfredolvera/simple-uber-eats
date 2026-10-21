"""New synthetic cases from functional compatibility observations only."""

import unittest
from datetime import UTC
from http.cookies import SimpleCookie
from unittest.mock import patch

from test_core_contract import NOW, Http, Response, envelope, model, session, wire


class BrowserCompatibility(unittest.TestCase):
    def test_browser_only_cookie_shape_and_whitespace(self):
        for source in (
            "sid=bad; uev2.id.session=valid-session",
            "sid=QA.valid; uev2.id.session=invalid",
        ):
            with (
                self.subTest(source=source),
                self.assertRaises(session.CookieInputError),
            ):
                session.WebSession.parse(source)
        parsed = session.WebSession.parse(
            "sid=  QA.valid  ; uev2.id.session=  valid-session  "
        )
        self.assertEqual(
            parsed.as_entry_data(), {"sid": "QA.valid", "session_id": "valid-session"}
        )
        # Server rotations and already-stored opaque credentials are independent.
        self.assertEqual(
            session.WebSession("opaque", "opaque").rotate({"sid": "new"}).sid, "new"
        )

    def test_auth_input_utf8_byte_limit(self):
        prefix = "sid=QA.a; uev2.id.session=b-session; ignored="
        for suffix in ("x" * (512 * 1024), "é" * (256 * 1024)):
            with (
                self.subTest(multibyte=suffix[0] != "x"),
                self.assertRaises(session.CookieInputError),
            ):
                session.WebSession.parse(prefix + suffix)
        boundary = prefix + "x" * (512 * 1024 - len(prefix))
        self.assertEqual(session.WebSession.parse(boundary).sid, "QA.a")

    def test_unpaired_surrogate_rejected_with_generic_error(self):
        for value in (
            "sid=QA.secret\ud800; uev2.id.session=b-session",
            "sid=QA.a; uev2.id.session=b-session; ignored=\udfff",
        ):
            with self.subTest(value=ascii(value)):
                with self.assertRaises(session.CookieInputError) as caught:
                    session.WebSession.parse(value)
                self.assertNotIn("secret", str(caught.exception))
        with self.assertRaises(session.CookieInputError):
            session.WebSession("a\ud800", "b")

    def test_curl_comment_is_not_an_actual_header(self):
        with self.assertRaises(session.CookieInputError):
            session.WebSession.parse(
                "curl https://example.invalid/ # -H 'Cookie: sid=QA.example; uev2.id.session=example-session'"
            )
        value = session.WebSession.parse(
            "curl https://example.invalid -H 'Cookie: sid=QA.has#hash; uev2.id.session=b-session' # ignored"
        )
        self.assertEqual(value.sid, "QA.has#hash")

    def test_hash_inside_shell_word_is_literal(self):
        for source in (
            "curl https://example.invalid/#fragment -H 'Cookie: sid=QA.a; uev2.id.session=b-session'",
            r"curl https://example.invalid -H Cookie:\ sid=QA.has#hash\;\ uev2.id.session=b-session",
        ):
            with self.subTest(source=source):
                parsed = session.WebSession.parse(source)
                self.assertEqual(parsed.session_id, "b-session")
                self.assertIn(parsed.sid, ("QA.a", "QA.has#hash"))

    def test_config_option_is_inert_and_never_read(self):
        for option in (
            "--config /etc/passwd",
            "--config=/etc/passwd",
            "-K /etc/passwd",
        ):
            with (
                self.subTest(option=option),
                patch("builtins.open", side_effect=AssertionError("No file reads")),
            ):
                value = session.WebSession.parse(
                    "curl https://example.invalid "
                    + option
                    + " -H 'Cookie: sid=QA.a; uev2.id.session=b-session'"
                )
                self.assertEqual(
                    value.cookie_header(), "sid=QA.a; uev2.id.session=b-session"
                )
        # An option-looking config filename must remain a filename, never a header.
        with self.assertRaises(session.CookieInputError):
            session.WebSession.parse(
                "curl https://example.invalid --config '--cookie=sid=QA.a; uev2.id.session=b-session'"
            )

    def test_opaque_cookie_content_remains_valid(self):
        item = session.WebSession.parse(
            'sid="QA.opaque /=?#:+"; uev2.id.session=abc%2Bdef-session'
        )
        self.assertEqual(item.sid, "QA.opaque /=?#:+")
        self.assertEqual(SimpleCookie(item.cookie_header())["sid"].value, item.sid)


class DeliveryCompatibility(unittest.TestCase):
    def test_status_whitespace_normalized_case_and_punctuation_preserved(self):
        for raw, expected in (
            ("  Almost   there  ", "Almost there"),
            ("  Heading your way\n", "Heading your way"),
            ("\tOn\nTHE\u00a0way! ", "On THE way!"),
        ):
            for field in ("titleSummary", "timelineSummary"):
                status = (
                    {"titleSummary": {"summary": {"text": raw}}}
                    if field == "titleSummary"
                    else {"timelineSummary": raw}
                )
                with self.subTest(raw=raw, field=field):
                    result = wire.decode_orders(
                        envelope({"feedCards": [{"status": status}]}), NOW
                    ).primary
                    self.assertEqual(result.status, expected)

    def test_dotted_meridiem_and_inherited_range(self):
        for label, expected in (
            ("1:15 p.m.", NOW.replace(hour=13, minute=15)),
            ("12:20–12:35 p.m.", NOW.replace(minute=35)),
            ("11:55 p.m.–12:10 a.m.", NOW.replace(day=20, hour=0, minute=10)),
        ):
            now = NOW.replace(hour=23, minute=55) if "a.m." in label else NOW
            with self.subTest(label=label):
                delivery = wire.decode_orders(
                    envelope({"feedCards": [{"status": {"title": label}}]}), now
                ).primary
                self.assertEqual(delivery.eta, expected)

    def test_numeric_string_coordinates_and_timestamp_units(self):
        for multiplier in (1, 1000, 1000000, 1000000000):
            point = {
                "latitude": "20.25",
                "longitude": "-100.5",
                "timestamp": str(int(NOW.timestamp()) * multiplier),
            }
            with self.subTest(multiplier=multiplier):
                result = wire.decode_orders(
                    envelope(
                        {
                            "backgroundFeedCards": [
                                {
                                    "mapEntity": [
                                        {"type": "COURIER", "pathPoints": [point]}
                                    ]
                                }
                            ]
                        }
                    ),
                    NOW,
                ).primary
                self.assertEqual(result.courier_point, model.GeoPoint(20.25, -100.5))
                self.assertEqual(result.route[0].at, NOW.timestamp())
        for value in ("nan", "inf", "-inf", "", "true", True, False):
            with self.subTest(invalid=value):
                result = wire.decode_orders(
                    envelope(
                        {
                            "backgroundFeedCards": [
                                {
                                    "mapEntity": [
                                        {
                                            "type": "COURIER",
                                            "pathPoints": [
                                                {
                                                    "lat": value,
                                                    "lon": "30",
                                                    "time": str(NOW.timestamp()),
                                                }
                                            ],
                                        }
                                    ]
                                }
                            ]
                        }
                    ),
                    NOW,
                ).primary
                self.assertIsNone(result.courier_point)


class AuthenticationCompatibility(unittest.IsolatedAsyncioTestCase):
    async def test_http_auth_status_distinguished_from_protocol_rejection(self):
        self.assertIsNone(model.AuthRejected().http_status)
        for status in (401, 403):
            gateway = wire.Gateway(
                Http(Response(status)), session.WebSession("a", "b"), UTC
            )
            with self.assertRaises(model.AuthRejected) as caught:
                await gateway.read_orders()
            self.assertEqual(caught.exception.http_status, status)
        gateway = wire.Gateway(
            Http(Response(payload={"error": {"code": "INVALID_TOKEN"}})),
            session.WebSession("a", "b"),
            UTC,
        )
        with self.assertRaises(model.AuthRejected) as caught:
            await gateway.read_orders()
        self.assertIsNone(caught.exception.http_status)


class ProfileEndpointCompatibility(unittest.IsolatedAsyncioTestCase):
    async def test_profile_uses_browser_profile_endpoint(self):
        http = Http(
            Response(payload={"data": {"firstName": "Ada", "lastName": "Lovelace"}})
        )
        gateway = wire.Gateway(http, session.WebSession("opaque", "opaque"), UTC)
        self.assertEqual(await gateway.account_label(), "Ada Lovelace")
        self.assertEqual(
            http.calls[0][0],
            "https://www.ubereats.com/_p/api/getUserV1?localeCode=en-US",
        )
        self.assertEqual(http.calls[0][1]["json"], {})
