# Simple Uber Eats 4.0.1

Fixes blocking time zone loading reported during HACS review.

- Resolves time zones through `await dt_util.async_get_time_zone(name)` and
  passes the resolved object into `Gateway`; the gateway performs no zone lookup.
- Covers setup, initial configuration, reauthentication, reconfiguration,
  legacy import and entry migration, including invalid-credential migration.
- Preserves persisted IANA names, request time zones, local ETA conversion and
  invalid-zone fallback to the Home Assistant zone, then UTC. Invalid submitted
  zones remain validation errors.

Validation: 80 automated tests with Home Assistant 2026.8.0, including eight new
regression tests that reject synchronous zone loading on the event loop with
cold caches. Ruff and Python compilation also pass. Publication is gated on
successful HACS, hassfest and integration-test workflows for this commit.
