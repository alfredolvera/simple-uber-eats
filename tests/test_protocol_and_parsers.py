"""Behavioral specification for Uber protocol and response normalization."""
from __future__ import annotations

from datetime import datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).parents[1]
PACKAGE = ROOT / "custom_components" / "simple_uber_eats"

# Load pure modules without importing the Home Assistant integration package.
namespace = types.ModuleType("clean_simple_uber_eats")
namespace.__path__ = [str(PACKAGE)]
sys.modules.setdefault("clean_simple_uber_eats", namespace)


def load(name: str):
    spec = spec_from_file_location(
        f"clean_simple_uber_eats.{name}", PACKAGE / f"{name}.py"
    )
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


protocol = load("protocol")
parsers = load("parsers")
api = load("api")
presentation = load("presentation")


class FakeResponse:
    def __init__(self, status, body, cookies=None):
        self.status = status
        self._body = body
        self.cookies = cookies or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, *, json, headers):
        self.calls.append((url, json, headers))
        return self.responses.pop(0)


class CookieTests(unittest.TestCase):
    SID = "QA.EXAMPLE-0123456789"
    SESSION = "00000000-0000-0000-0000-000000000000"
    CANONICAL = (
        "sid=QA.EXAMPLE-0123456789; "
        "uev2.id.session=00000000-0000-0000-0000-000000000000"
    )

    def cookie(self) -> str:
        return (
            "theme=dark; analytics_id=FAKE-ANALYTICS; "
            f"sid={self.SID}; uev2.id.session={self.SESSION}; "
            "uev2.loc=FAKE-LOCATION; jwt-session=FAKE-JWT"
        )

    def parse(self, value: str):
        return protocol.SessionCredentials.from_authentication_input(value)

    def assert_credentials(self, value: str):
        credentials = self.parse(value)
        self.assertEqual(self.SID, credentials.sid)
        self.assertEqual(self.SESSION, credentials.session_id)
        self.assertEqual(self.CANONICAL, credentials.header())
        return credentials

    def assert_error(self, value: str, expected: str):
        with self.assertRaises(protocol.CookieValidationError) as ctx:
            self.parse(value)
        self.assertEqual(expected, ctx.exception.translation_key)

    def test_raw_cookie_inputs_are_minimized(self):
        self.assert_credentials(self.CANONICAL)
        credentials = self.assert_credentials(self.cookie())
        for unrelated in (
            "theme",
            "analytics_id",
            "uev2.loc",
            "jwt-session",
        ):
            self.assertNotIn(unrelated, credentials.header())

    def test_standalone_cookie_header_is_case_insensitive(self):
        self.assert_credentials(f"Cookie: {self.cookie()}")
        self.assert_credentials(f"  cookie  :  {self.cookie()}")

    def test_firefox_multiline_copy_as_curl(self):
        copied = f"""curl 'https://www.ubereats.com/_p/api/getActiveOrdersV1?fake=1' \\
  -X POST \\
  -H 'Accept: application/json' \\
  -H 'Cookie: {self.cookie()}' \\
  --data-raw '{{"cookie_note":"not a header"}}'"""
        self.assert_credentials(copied)

    def test_chromium_double_quoted_and_long_header_forms(self):
        double_quoted = (
            'curl "https://www.ubereats.com/api/getActiveOrdersV1" '
            '-H "content-type: application/json" '
            f'-H "cookie: {self.cookie()}" --data-raw "{{}}"'
        )
        long_form = (
            "curl 'https://www.ubereats.com/api/getActiveOrdersV1' "
            f"--header 'Cookie: {self.cookie()}'"
        )
        long_equals = (
            "curl 'https://www.ubereats.com/api/getActiveOrdersV1' "
            f"--header='Cookie: {self.cookie()}'"
        )
        self.assert_credentials(double_quoted)
        self.assert_credentials(long_form)
        self.assert_credentials(long_equals)

    def test_request_header_block_with_irrelevant_headers(self):
        headers = f"""POST /_p/api/getActiveOrdersV1 HTTP/2
Host: www.ubereats.com
Content-Type: application/json
Cookie: {self.cookie()}
Referer: https://www.ubereats.com/store/example?cookie=not-a-header
Authorization: FAKE-NOT-USED"""
        self.assert_credentials(headers)

    def test_cookie_header_can_appear_in_middle_of_long_curl(self):
        copied = (
            "curl 'https://www.ubereats.com/_p/api/getActiveOrdersV1' "
            "-H 'Accept: */*' -H 'X-Fake: before' "
            f"-H 'Cookie: {self.cookie()}' "
            "-H 'Referer: https://www.ubereats.com/?cookie=fake' "
            "-H 'X-Fake: after' --data-raw '{\"value\":\"cookie: fake\"}'"
        )
        self.assert_credentials(copied)

    def test_curl_url_and_body_are_never_mined_for_cookie_headers(self):
        cookie_text = self.CANONICAL
        cases = (
            "curl 'https://www.ubereats.com/?Cookie%3A=" + cookie_text + "'",
            (
                "curl 'https://www.ubereats.com/api/getActiveOrdersV1' "
                f"--data-raw \"request body says -H 'Cookie: {cookie_text}'\""
            ),
            (
                "curl 'https://www.ubereats.com/api/getActiveOrdersV1' "
                f"--data-raw 'first line\nCookie: {cookie_text}\nlast line'"
            ),
        )
        for copied in cases:
            with self.subTest(copied=copied):
                self.assert_error(copied, "malformed_session_input")

    def test_identical_candidates_are_accepted_and_conflicts_rejected(self):
        identical = f"Cookie: {self.CANONICAL}\nCookie: {self.CANONICAL}"
        conflicting = (
            f"Cookie: {self.CANONICAL}\n"
            "Cookie: sid=QA.DIFFERENT; "
            "uev2.id.session=11111111-1111-1111-1111-111111111111"
        )
        partially_invalid = (
            f"Cookie: {self.CANONICAL}\n"
            f"Cookie: sid={self.SID}; uev2.id.session=invalid"
        )
        self.assert_credentials(identical)
        self.assert_error(conflicting, "ambiguous_session_input")
        self.assert_error(partially_invalid, "ambiguous_session_input")

    def test_missing_empty_malformed_and_unrelated_input_errors(self):
        cases = (
            ("", "session_input_not_found"),
            (
                f"Cookie: uev2.id.session={self.SESSION}; theme=dark",
                "sid_not_found",
            ),
            (f"Cookie: sid={self.SID}; theme=dark", "session_not_found"),
            (
                f"Cookie: sid=not-valid; uev2.id.session={self.SESSION}",
                "invalid_sid",
            ),
            (
                f"Cookie: sid={self.SID}; uev2.id.session=notvalid",
                "invalid_session",
            ),
            (
                "curl 'https://www.ubereats.com/' "
                f"-H 'Cookie: sid={self.SID}\nINJECTED; "
                f"uev2.id.session={self.SESSION}'",
                "invalid_sid",
            ),
            (
                "curl 'https://www.ubereats.com/' -H 'Cookie "
                f"sid={self.SID}; uev2.id.session={self.SESSION}'",
                "malformed_session_input",
            ),
            ("ordinary prose mentioning a cookie jar", "session_input_not_found"),
            (
                "https://www.ubereats.com/?cookie=sid%3DQA.EXAMPLE",
                "session_input_not_found",
            ),
        )
        for raw, expected in cases:
            with self.subTest(expected=expected):
                self.assert_error(raw, expected)

    def test_cookie_values_keep_common_encoded_characters_and_equals(self):
        raw = (
            "sid=QA.EXAMPLE_~%2B%2F-0123; "
            "uev2.id.session=00000000-0000-0000-0000-000000000000%3D%3D; "
            "other=fake"
        )
        credentials = self.parse(raw)
        self.assertEqual("QA.EXAMPLE_~%2B%2F-0123", credentials.sid)
        self.assertEqual(
            "00000000-0000-0000-0000-000000000000%3D%3D",
            credentials.session_id,
        )
        self.assertEqual(
            "sid=QA.EXAMPLE_~%2B%2F-0123; "
            "uev2.id.session=00000000-0000-0000-0000-000000000000%3D%3D",
            credentials.header(),
        )

    def test_quoted_required_values_and_unrelated_duplicates(self):
        credentials = self.parse(
            f'foo=A; foo=B; sid="{self.SID}"; '
            f'uev2.id.session="{self.SESSION}"'
        )
        self.assertEqual(self.CANONICAL, credentials.header())

    def test_required_cookie_duplicate_semantics(self):
        self.assert_credentials(
            f"sid={self.SID}; sid={self.SID}; "
            f"uev2.id.session={self.SESSION}; "
            f"uev2.id.session={self.SESSION}"
        )
        conflicts = (
            f"sid={self.SID}; sid=QA.DIFFERENT; "
            f"uev2.id.session={self.SESSION}",
            f"sid={self.SID}; uev2.id.session={self.SESSION}; "
            "uev2.id.session=different-session",
        )
        for value in conflicts:
            with self.subTest(value=value):
                self.assert_error(value, "ambiguous_session_input")

    def test_curl_shell_like_arguments_remain_inert_text(self):
        cases = (
            (
                "curl https://example.invalid/$(touch /tmp/pwned) "
                f"-H 'Cookie: {self.CANONICAL}'"
            ),
            (
                "curl `touch /tmp/pwned` "
                f"-H 'Cookie: {self.CANONICAL}'"
            ),
            f"curl -H 'Cookie: {self.CANONICAL}' --data @/etc/passwd",
            f"curl --config /etc/passwd -H 'Cookie: {self.CANONICAL}'",
        )
        for value in cases:
            with self.subTest(value=value):
                self.assert_credentials(value)

    def test_commented_and_lowercase_short_headers_are_not_candidates(self):
        cases = (
            (
                "curl https://example.invalid/ # "
                f"-H 'Cookie: {self.CANONICAL}'"
            ),
            f"curl -h 'Cookie: {self.CANONICAL}'",
        )
        for value in cases:
            with self.subTest(value=value):
                self.assert_error(value, "malformed_session_input")

    def test_authentication_input_size_boundary(self):
        prefix = f"{self.CANONICAL}; padding="
        for size in (
            protocol.MAX_AUTHENTICATION_INPUT_BYTES - 1,
            protocol.MAX_AUTHENTICATION_INPUT_BYTES,
        ):
            value = prefix + "x" * (size - len(prefix))
            with self.subTest(size=size):
                self.assertEqual(size, len(value.encode("utf-8")))
                self.assert_credentials(value)

        oversized = prefix + "x" * (
            protocol.MAX_AUTHENTICATION_INPUT_BYTES + 1 - len(prefix)
        )
        self.assert_error(oversized, "session_input_too_large")

    def test_unpaired_unicode_surrogate_is_malformed_input(self):
        self.assert_error(f"{self.CANONICAL}\ud800", "malformed_session_input")

    def test_rotation_variants(self):
        credentials = self.parse(self.cookie())
        sid = credentials.rotated({"sid": "QA.rotated"})
        session = credentials.rotated({"uev2.id.session": "new-session"})
        both = credentials.rotated({"sid": "QA.two", "uev2.id.session": "two-session"})
        self.assertEqual(("QA.rotated", credentials.session_id), (sid.sid, sid.session_id))
        self.assertEqual((credentials.sid, "new-session"), (session.sid, session.session_id))
        self.assertEqual(("QA.two", "two-session"), (both.sid, both.session_id))
        self.assertEqual(
            "sid=QA.two; uev2.id.session=two-session", both.header()
        )

        stored = protocol.rotated_entry_data(
            {"account_name": "A", "time_zone": "UTC", "sid": "old"}, both
        )
        self.assertEqual("A", stored["account_name"])
        self.assertEqual("UTC", stored["time_zone"])
        self.assertEqual("QA.two", stored["sid"])
        self.assertEqual("two-session", stored["session_id"])
        self.assertEqual(
            "sid=QA.two; uev2.id.session=two-session", stored["full_cookie"]
        )

    def test_existing_full_cookie_is_ignored_and_normalized(self):
        credentials = protocol.SessionCredentials.from_stored(
            self.SID,
            self.SESSION,
            self.cookie(),
        )
        self.assertEqual(self.CANONICAL, credentials.header())

    def test_poll_and_backoff_policy(self):
        self.assertEqual(60, protocol.next_poll_interval(0, False).total_seconds())
        self.assertEqual(15, protocol.next_poll_interval(1, False).total_seconds())
        self.assertEqual(10, protocol.next_poll_interval(1, True).total_seconds())
        self.assertEqual([60, 120, 300, 300], [protocol.rate_limit_delay(i).total_seconds() for i in range(1, 5)])

    def test_connection_policy_distinguishes_auth_and_temporary_failures(self):
        policy = protocol.RequestPolicy()
        policy.observe_temporary_failure()
        self.assertEqual(protocol.CONNECTION_TEMPORARILY_UNAVAILABLE, policy.state)
        self.assertFalse(policy.observe_http_failure(401))
        self.assertFalse(policy.observe_http_failure(403))
        self.assertTrue(policy.observe_http_failure(401))
        self.assertEqual(protocol.CONNECTION_AUTHENTICATION_FAILED, policy.state)
        policy.observe_valid_success()
        self.assertEqual(protocol.CONNECTION_CONNECTED, policy.state)

    def test_success_resets_rate_limit_backoff(self):
        policy = protocol.RequestPolicy()
        for expected in (60, 120, 300, 300):
            self.assertFalse(policy.observe_http_failure(429))
            self.assertEqual(expected, policy.rate_limit_interval.total_seconds())
        policy.observe_valid_success()
        self.assertEqual(0, policy.rate_limits)
        self.assertIsNone(policy.rate_limit_interval)


class ApiClientTests(unittest.IsolatedAsyncioTestCase):
    def credentials(self):
        raw = f"sid=QA.original; uev2.id.session=old-session; theme=dark; padding={'x' * 30}"
        return protocol.SessionCredentials.from_cookie_header(raw)

    async def test_active_order_request_and_both_cookie_rotations(self):
        session = FakeSession(
            [FakeResponse(200, {"data": {"orders": []}}, {"sid": "QA.new", "uev2.id.session": "new-session"})]
        )
        client = api.UberEatsApiClient(session, self.credentials(), "America/Mexico_City")
        result = await client.active_orders()
        self.assertEqual(200, result.status)
        self.assertEqual(("QA.new", "new-session"), (result.credentials.sid, result.credentials.session_id))
        url, payload, headers = session.calls[0]
        self.assertIn("getActiveOrdersV1?localeCode=us", url)
        self.assertEqual("America/Mexico_City", payload["timezone"])
        self.assertEqual(
            "sid=QA.original; uev2.id.session=old-session", headers["Cookie"]
        )

    async def test_non_success_does_not_attempt_json_decode(self):
        class NoJsonResponse(FakeResponse):
            async def json(self):
                raise AssertionError("JSON must not be read for this response")

        session = FakeSession([NoJsonResponse(429, None)])
        result = await api.UberEatsApiClient(session, self.credentials(), "UTC").active_orders()
        self.assertEqual(429, result.status)
        self.assertIsNone(result.body)

    async def test_old_entry_sends_only_authoritative_minimum_cookie(self):
        historical = (
            "analytics=FAKE; sid=QA.STALE; location=FAKE; "
            "uev2.id.session=stale-session; browser_id=FAKE"
        )
        credentials = protocol.SessionCredentials.from_stored(
            "QA.authoritative",
            "authoritative-session",
            historical,
        )
        session = FakeSession([FakeResponse(200, {"data": {"orders": []}})])
        await api.UberEatsApiClient(session, credentials, "UTC").active_orders()

        self.assertEqual(
            "sid=QA.authoritative; uev2.id.session=authoritative-session",
            session.calls[0][2]["Cookie"],
        )


class ParserTests(unittest.TestCase):
    NOW = datetime.fromisoformat("2026-08-13T19:00:00-06:00")

    def order(self, status=None, background=None, store_location=None):
        return {
            "uuid": "order-1",
            "feedCards": [{"status": status or {"currentProgress": 2, "title": "7:30 PM"}}],
            "contacts": [{"type": "COURIER", "title": "Courier", "phoneNumber": "123"}],
            "activeOrderOverview": {"title": "Restaurant"},
            "backgroundFeedCards": background or [],
            "orderInfo": {"storeInfo": {"location": store_location or {}}},
        }

    def test_visible_status_wins_and_only_whitespace_changes(self):
        for text in ("Picking up your order...", "Heading your way"):
            order = self.order({"currentProgress": 3, "timelineSummary": f"  {text}\n"})
            parsed = parsers.parse_order(order, now=lambda: self.NOW)
            self.assertEqual(text, parsed["order_status"])
            self.assertEqual(text, parsed["order_status_description"])
            self.assertEqual("en route", parsed["order_stage"])

    def test_status_fallback_and_unknown(self):
        parsed = parsers.parse_order(self.order(), now=lambda: self.NOW)
        self.assertEqual("picked up", parsed["order_status"])
        unknown = parsers.parse_order(self.order({"currentProgress": 99}), now=lambda: self.NOW)
        self.assertEqual("unknown", unknown["order_status"])

    def test_title_summary_status_fallback(self):
        status = {"titleSummary": {"summary": {"text": "  Almost   there  "}}}
        parsed = parsers.parse_order(self.order(status), now=lambda: self.NOW)
        self.assertEqual("Almost there", parsed["order_status"])

    def test_title_summary_is_primary_and_preserves_live_text(self):
        for visible in (
            "Picking up your order…",
            "Heading your way…",
            "Oops, finding another delivery person...",
        ):
            with self.subTest(visible=visible):
                status = {
                    "currentProgress": 2,
                    "timelineSummary": "Secondary timeline text",
                    "titleSummary": {"summary": {"text": visible}},
                }
                parsed = parsers.parse_order(self.order(status), now=lambda: self.NOW)
                self.assertEqual(visible, parsed["order_status"])

    def test_store_map_location_and_store_info_fallback(self):
        cards = [{"mapEntity": [{"type": "STORE", "latitude": 19.4, "longitude": -99.1}]}]
        mapped = parsers.parse_order(self.order(background=cards), now=lambda: self.NOW)
        fallback = parsers.parse_order(self.order(store_location={"latitude": 20, "longitude": -100}), now=lambda: self.NOW)
        self.assertEqual({"lat": 19.4, "lon": -99.1}, mapped["store_location"])
        self.assertEqual({"lat": 20.0, "lon": -100.0}, fallback["store_location"])

    def test_invalid_store_coordinates_are_ignored(self):
        for location in ({"latitude": 91, "longitude": 0}, {"latitude": 0, "longitude": -181}, {"latitude": "bad", "longitude": 1}, {"latitude": 0, "longitude": 0}):
            with self.subTest(location=location):
                parsed = parsers.parse_order(self.order(store_location=location), now=lambda: self.NOW)
                self.assertIsNone(parsed["store_location"])

    def test_map_entities_across_cards_and_freshest_courier(self):
        cards = [
            {"mapEntity": [{"type": "STORE", "latitude": 1, "longitude": 2}, {"type": "COURIER", "latitude": 3, "longitude": 4, "pathPoints": [{"latitude": 3, "longitude": 4, "epoch": 100}]}]},
            {"mapEntity": [{"type": "EATER", "latitude": 5, "longitude": 6}, {"type": "COURIER", "latitude": 7, "longitude": 8, "pathPoints": [{"latitude": 7, "longitude": 8, "epoch": 200}]}]},
        ]
        parsed = parsers.parse_order(self.order(background=cards), now=lambda: self.NOW)
        self.assertEqual(7.0, parsed["driver_location_lat"])
        self.assertEqual(2, len(parsers.raw_active_orders({"data": {"orders": [self.order(), self.order()]}})))

    def test_path_points_normalize_sort_and_deduplicate(self):
        points = parsers.normalize_path_points([
            {"latitude": 1, "longitude": 2, "epoch": 2},
            {"latitude": 3, "longitude": 4, "epoch": 1},
            {"latitude": 5, "longitude": 6, "epoch": 2},
            {"latitude": 91, "longitude": 0, "epoch": 3},
            {"latitude": 0, "longitude": 0, "epoch": "bad"},
        ])
        self.assertEqual([1000, 2000], [point["epoch"] for point in points])
        self.assertEqual(5.0, points[-1]["latitude"])

    def test_eta_range_uses_end_and_rolls_midnight(self):
        parsed = parsers.parse_eta("7:30 PM – 7:40 PM", self.NOW)
        self.assertEqual((19, 40), (parsed.hour, parsed.minute))
        rolled = parsers.parse_eta("12:10 AM", self.NOW)
        self.assertEqual(14, rolled.day)

    def test_malformed_envelopes(self):
        for value in (None, {}, {"data": []}, {"data": {"orders": {}}}):
            with self.subTest(value=value), self.assertRaises(parsers.MalformedUberResponse):
                parsers.raw_active_orders(value)

    def test_zero_orders_and_auth_code(self):
        self.assertEqual([], parsers.raw_active_orders({"data": {"orders": []}}))
        self.assertEqual("SESSION_EXPIRED", parsers.auth_error_code({"error": {"code": "SESSION_EXPIRED"}}))
        self.assertIsNone(parsers.auth_error_code({"error": {"code": "SERVER_BUSY"}}))
        self.assertIsNone(parsers.parse_profile({"data": {}}, require_logged_in=True))

    def test_restaurant_entity_projection_and_completion_cleanup(self):
        active = {
            "orders": [{"restaurant_name": "R", "store_location": {"lat": 1.5, "lon": -2.5}}]
        }
        self.assertEqual(
            {"active_orders_count": 1, "latitude": 1.5, "longitude": -2.5},
            presentation.restaurant_attributes(active),
        )
        self.assertEqual(
            {"active_orders_count": 0},
            presentation.restaurant_attributes({"orders": []}),
        )
        self.assertIsNone(presentation.primary_order({"orders": []}))


if __name__ == "__main__":
    unittest.main()
