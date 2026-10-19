# Simple Uber Eats 4.0 replacement specification

Date: 2026-10-19. Goal: replace all Python implementation and tests with newly
authored code using this specification, without consulting predecessor source.
The user authorizes completing the replacement without contacting upstream.

## Information boundary

Implementers work only in `/tmp/simple-uber-eats-v4-new`. Do not read the old
repository, Git history, old tests, sibling projects, upstream repositories,
or prior Codex sessions. This specification's author has inspected predecessor
source and an earlier audit. This is a functional/interface specification, not
a claim that the specification author was unexposed. Report all material read.
Use official Home Assistant/Python/aiohttp documentation when necessary.
Never claim isolation beyond these recorded context and working-directory rules.

## Product requirements

Unofficial Home Assistant integration, domain `simple_uber_eats`, version 4.0.0,
Python 3.14 / Home Assistant 2026.8 minimum. No extra runtime dependencies.
Native entities per account: account connected and active order binary sensors;
order status, ETA timestamp, restaurant, courier sensors; courier GPS tracker.
First active order is primary; expose active order count. Keep Uber's visible
status wording/case/punctuation. ETA is an aware datetime, no ticking sensor.
Restaurant exposes latitude/longitude while active. No invented GPS position.
Courier telephone/photo attributes default off; enable by options flow, with a
recorder warning. Diagnostics must use a strict whitelist of counts/timings,
never include credentials, names, phone, photos, addresses, coordinates, payloads.
Multiple entries independent. No raw request/response logs or exception text
containing credentials. Unload cleans listeners/timers. Reauth/reconfigure work.

Polling: no orders 60s; order without GPS 15s; order with valid courier GPS 10s.
HTTP 429 retry 60/120/300s capped; no per-second network calls. Distinguish auth
(HTTP 401/403 or protocol auth rejection) from timeout/5xx/malformed data.
Explicit protocol rejection triggers ConfigEntryAuthFailed immediately. Three consecutive
HTTP401/403 polls trigger ConfigEntryAuthFailed, including across startup retries;
the first two are temporary failures. Successful or nonauth temporary responses
reset that count. Other temporary failure uses UpdateFailed.
Setup must succeed if active orders validates but optional profile is unavailable.
Store only two necessary cookie values; persist server Set-Cookie rotations.
Tracker local 12s delayed interpolation across timestamped real samples; freeze
at newest known sample, do not extrapolate; no movement before first coordinate;
reset on order change/completion/unload; retain safe position during telemetry
gap. Order-independent timestamp deduplication; finite/range validation.

## External protocol facts

POST https://www.ubereats.com/api/getActiveOrdersV1?localeCode=en-US
JSON body: orderUuid=null, timezone=IANA name, showAppUpsellIllustration=true.
Optional POST https://www.ubereats.com/_p/api/getUserV1?localeCode=en-US with {}.
Headers: content-type application/json; x-csrf-token x; origin and referer
https://www.ubereats.com (referer ends slash); cookie composed solely of `sid`
and `uev2.id.session`. HTTP client uses an HA-created account-local aiohttp session with DummyCookieJar
and the HA shared connector, timeout 20s. Never share cookie storage across accounts.
Flow probes detach their session in finally; entry sessions detach on failed setup
and unload without closing the shared connector. This adjusts the original shared
session wording after independent review demonstrated aiohttp jar cookie override.
Cookie input accepts raw Cookie values, copied request headers, POSIX Copy as
cURL (parse tokens, NEVER execute). Only real header/-b fields count, never URL
or request body. Required cookies nonempty/control-free; conflicting duplicate
values reject; quoted values and encoded values preserve content. Reject cookie
file references and header injection. Store sid and session_id fields, not copy.
Server rotations are independently optional and must validate before persistence.

Envelope: object `data` with list `orders`. Empty list is authoritative completion;
missing/wrong envelope is temporary error, not completion. Auth errors may appear
at `error.code` as UNAUTHORIZED, SESSION_EXPIRED, INVALID_TOKEN, or at
`data.isLoggedIn=false`. Profile `data.firstName`, `lastName`, `isLoggedIn`.
Order fields: `uuid`; `activeOrderOverview.title` restaurant name; `contacts`
list with type COURIER, title courier name, formattedPhoneNumber / phoneNumber.
`feedCards` list: first object containing `status` supplies currentProgress,
title (ETA label), titleSummary.summary.text or timelineSummary (string or
object .text) for visible status; type `courier` card has `courier` list whose
first item has iconUrl. Progress 0/1 preparing, 2 picked up, 3 en route,
4 arriving, 5 delivered, unknown otherwise. These are product fallback labels.
`backgroundFeedCards` list contains `mapEntity` lists; type STORE/EATER/COURIER.
Coordinates may be latitude/longitude, lat/lon/lng (case/underscore insensitive).
Fallback store coordinates at `orderInfo.storeInfo.location`.
COURIER has `pathPoints`: timestamped coordinate objects, optional course.
Timestamp aliases epoch/epochMs/epochMillis/timestamp/timestampMs/time, convert
seconds/ms/us/ns to seconds. Reject booleans, nonfinite, out of bounds, (0,0).
When several couriers exist choose freshest valid path timestamp (later response
item breaks tie); ignore irrelevant malformed nested values without crashing.
ETA title contains local HH:MM, optional AM/PM, possibly a range (use last time,
inherit meridiem). Infer nearest plausible aware instant around now in configured
ZoneInfo; accept 5min past through 12h future, handle midnight and DST roundtrip;
invalid times/DST gaps/outside window => None. Locale remains en-US deliberately.

## New module and shared API contract

Core implementer owns `model.py`, `web_session.py`, `wire.py`, `motion.py` and
`tests/test_core_*.py`. No HA imports in these core modules; aiohttp import fine.
All models dataclasses frozen, slots; defaults allow `Delivery()` and `Snapshot()`.
model.py:
- GeoPoint(latitude: float, longitude: float)
- Fix(at: float, point: GeoPoint) -- at UTC epoch seconds
- Delivery(reference: str='', status: str='unknown', eta: datetime|None=None,
  restaurant: str|None=None, restaurant_point: GeoPoint|None=None,
  courier: str|None=None, courier_point: GeoPoint|None=None,
  courier_phone: str|None=None, courier_photo: str|None=None,
  route: tuple[Fix,...]=())
- Snapshot(deliveries: tuple[Delivery,...]=()); property primary -> Delivery|None
- AuthRejected(Exception) with optional http_status: int|None (None means protocol rejection), ServiceUnavailable(Exception),
  Throttled(ServiceUnavailable) with retry_after: float|None; errors secret-free.
web_session.py:
- CookieInputError(ValueError), safe generic message
- WebSession(sid: str, session_id: str), frozen; repr hides both fields
- WebSession.parse(text: str)->WebSession; .cookie_header()->str;
  .rotate(cookies: Mapping)->WebSession; .as_entry_data()->dict[str,str]
wire.py:
- decode_orders(payload: object, now: datetime)->Snapshot
- Gateway(http_session, credentials: WebSession, timezone: str),
  writable .credentials: WebSession; async .read_orders()->Snapshot;
  async .account_label()->str|None (optional profile probe)
  error classification as above; rotations retained even for 429/5xx.
motion.py:
- RouteBuffer(delay: float=12.0); .ingest(delivery: Delivery|None)->None;
  .position(now: float)->GeoPoint|None; .reset()->None
- .ingest accepts new snapshots, merges same-order fixes, bounds stored history,
  .position respects monotonic display within a route and no extrapolation.

HA implementer owns all remaining Python modules, new translations/icons JSON,
and `tests/test_ha_*.py`. Imports only the above core contract (or new HA files).
Suggested new runtime modules `runtime.py` for coordinator and HTTP management,
`account.py` for entity base/registry upgrade; required HA-named platform files
__init__, config_flow, sensor, binary_sensor, device_tracker, diagnostics.
Use typed Snapshot throughout, no old dictionary data schema or old constructor.
Core/HA tests use stdlib unittest, local import loader if needed, do not copy
old tests. Real HA pytest tests are encouraged if HA runtime available.

## Compatibility facts (not implementation instructions)

Existing domain is `simple_uber_eats`, existing config entry version 1. Keys
sid/session_id/account_name/time_zone/full_cookie/legacy_entry_id may exist.
On entry upgrade to version 2, minimize stored credentials, remove full_cookie,
preserve account_name/time_zone, validate timezone. Reject malformed credentials
safely (reauth). Do not silently move entries from different integration domains.
New entity IDs use entry_id plus role; migrate same-entry HA entity registry
unique_ids with public registry API so entity_id and dashboards remain intact.
Old unique IDs: `uber_eats_` + account_name with spaces replaced underscores + `_`
+ role. Old roles: account_connected, active_order, order_status, driver_eta,
restaurant_name, driver_name, driver_tracker. New translation keys/names must
retain user labels Account connected, Active order, Order status, ETA,
Restaurant, Courier. Account connected false on confirmed auth rejection,
unavailable for temporary failures; active order false for valid empty snapshot.

## Observable compatibility additions from review

These facts were supplied by the exposed parent after black-box behavior checks;
implementers received no earlier source/test code. Status text normalizes whitespace
only. Browser session input is at most 512 KiB UTF-8, rejects invalid Unicode,
has QA. sid prefix and hyphen-containing session id, with values trimmed. Server
rotations remain opaque valid values. POSIX comments are inert; --config/-K
arguments may be ignored without opening any file. Numeric coordinate/time strings
and dotted meridiem labels are accepted.

Native sensor and active binary attributes use exact key active_orders_count.
Status without order is None; status attributes include order_id and description
when visible wording exists. ETA includes arrival_time aware datetime. Courier
placeholders Unknown/No Driver Assigned are None; opted-in photo key is picture_url.
Account connected retains connection_state and attempt/success ISO timestamps.
Devices retain name account_name + Uber Eats suffix. Tracker is idle without
GPS, otherwise status/unknown; exposes tracking_active, order_active, position_mode,
courier, restaurant, order_status, order_id, eta ISO, latest real GPS/timestamp.
Preserve the shared Uber Eats label by union, never replace user labels.

Explicit optional legacy import validates a selected uber_eats-domain config entry
into a new simple_uber_eats entry, keeping the old entry intact. Adopt old entity
IDs/devices only when the complete selected set is unloaded and unconflicted;
use current public HA registry methods, otherwise leave old registries intact.
Legacy full_cookie/cookie fallback may be parsed locally but never persisted.

## Verification and evidence

Write new tests first and record observed initial failures, then implement.
Record all consulted docs and tool commands in evidence/<core|ha>-author.txt.
Compile all Python; run all new tests; validate JSON; run current hassfest/HACS
if practical. No live order or HA deployment claim without actual validation.
After authoring freeze, parent reviews code/tests, checks packaging and docs,
and records full SHA-256 manifest before any follow-up corrections.
Archive historical audit evidence separately from distributable runtime.
Do not publish, push, contact upstream, or claim HACS approval.

## Publication authorization

After final review, the user explicitly authorized publishing version 4.0.0 and
responding to the HACS review. The earlier no-publication boundary applied during
authoring and is superseded for this release only. No upstream permission request
was authorized or sent.
