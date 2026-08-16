# Changelog

## [3.1.0] - 2026-08-16

### Highlights

- Added browser **Copy as cURL** as the recommended authentication workflow. Setup, reauthentication, and reconfiguration also accept copied request headers, a complete Cookie header, or a raw cookie string.
- Reduced stored and outbound session data to the two credentials Simple Uber Eats requires: `sid` and `uev2.id.session`.
- Discarded all other copied browser cookies, location data, request headers, request bodies, URLs, and device-specific request content after local parsing.
- Automatically normalizes existing entries that contain historical full browser cookies to the minimal two-cookie representation.
- Improved validation errors so temporary Uber Eats, network, and server failures remain distinct from confirmed authentication rejection.
- Improved credential-rotation handling during setup, reauthentication, and reconfiguration so the final rotated credentials are retained.
- Expanded browser-specific authentication documentation and security guidance.
- Hardened the non-executing authentication parser and expanded its regression coverage.

## [3.0.1]

### Fixed

- Restored the ETA sensor state to Uber's authoritative timezone-aware arrival timestamp and removed the entity-owned one-second countdown task.
- Eliminated per-second ETA `async_write_ha_state()` calls that produced excessive Home Assistant Activity and `state_changed` events.
- Preserved the corrected five-minute recent-past tolerance, twelve-hour plausible-future bound, midnight rollover, final-time range selection, and rejection of implausible ETA values.
- Removed the per-second `seconds_remaining` attribute while retaining the authoritative `arrival_time` attribute.

## [3.0.0]

### Highlights

- Independently reimplemented the current integration around observable Uber behavior, documented Home Assistant/Python interfaces, tests, and live validation.
- Licensed the clean 3.x release tree under the MIT License.
- Changed the Home Assistant integration domain from `uber_eats` to `simple_uber_eats`, giving Simple Uber Eats its own specific namespace.
- Added an explicit legacy-import flow that validates and copies known 2.x account fields without displaying credentials or deleting the old entry.
- Uses Home Assistant 2026.8 public entity/device registry migration APIs when legacy entities are unloaded; otherwise it safely creates new-domain registry entries.
- Made Uber's visible `titleSummary` wording the primary Order status state, with internal progress mapping only as fallback.
- Added validated Restaurant latitude/longitude attributes for direct native Map-card use.
- Replaced the ETA timestamp state with a local `MM:SS` countdown while retaining the authoritative timezone-aware `arrival_time` attribute.
- Fixed false next-day ETA rollover when Uber reports the current minute and retained legitimate midnight rollover within a bounded delivery horizon.
- Replaced the static timezone selection catalog with the cached timezone set available from Python at runtime.
- Kept clean Restaurant, Courier, and ETA display names while preserving legacy unique IDs needed for registry continuity.
- Added original project-owned local Home Assistant branding.
- Removed the temporary active-order payload diagnostics after live validation.
- Preserved smooth courier playback, telemetry-gap recovery, adaptive polling, authentication, cookie rotation, and permanent sanitized tracking diagnostics.

### Breaking change

The integration domain changed from `uber_eats` to `simple_uber_eats`. Home
Assistant cannot change a config entry's domain in place, so 2.x users must add
Simple Uber Eats and confirm the detected legacy-account import before removing
the old entry. When public registry migration cannot safely run, new entity
registry entries are created and existing users may need to rename entity IDs.

In 2.x the ETA sensor state was a timestamp. In 3.0.0 its state is countdown text
in `MM:SS` form. Dashboards, templates, or automations that treated the ETA
state as a timestamp must use the `arrival_time` attribute or be adjusted for
the new countdown state.

## [2.0.1]

### Fixed

- Courier playback now abandons an obsolete telemetry segment when tracking resumes after a long interruption.
- Large timestamp gaps are no longer bridged by unrealistic straight-line interpolation.
- Recovery begins from the newest contiguous real samples while retaining the normal local playback delay.

## [2.0.0]

### Highlights

- Adopted the **Simple Uber Eats** name without changing the `uber_eats` domain or retained entity identifiers.
- Replaced the custom panel with a compact set of native Home Assistant entities.
- Added explicit account-connectivity and active-order binary sensors.
- Made ETA a timezone-aware timestamp and corrected the internal order-stage progression.
- Added adaptive 60/15/10-second polling and bounded 60/120/300-second rate-limit delays.
- Added sanitized diagnostics and more robust browser-session rotation, reconfiguration, and reauthentication.
- Added smooth native courier tracking from buffered real Uber path samples for the standard Map card.
- Removed reverse geocoding, the sidebar frontend, built-in TTS, nearby-driver triggers, historical order storage, statistics, and the custom WebSocket surface.
- Implementation and refactoring were performed with OpenAI Codex under alfredolvera's direction, review, and live testing.

### Breaking changes

Legacy panel, notification, history/statistics, and redundant location/order entities were retired. Existing native entity identifiers retained by 2.0 remain compatible.

## Legacy 1.x history

The 1.x series was the panel-oriented predecessor to Simple Uber Eats 2.x. Its
notable milestones included multiple-order display, account profile data,
historical order statistics, configurable TTS announcements, responsive panel
layouts, location maps, credential reconfiguration, and session-cookie
rotation. Later 1.x maintenance also improved panel navigation, mobile layout,
status presentation, and per-media-player notification settings.

Those panel, TTS, historical-order, reverse-geocoding, and custom WebSocket
features are intentionally absent from the current native-entity architecture.
