# Simple Uber Eats

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/alfredolvera/simple-uber-eats)](https://github.com/alfredolvera/simple-uber-eats/releases)
[![Maintenance: Active](https://img.shields.io/badge/maintenance-active-brightgreen.svg)](https://github.com/alfredolvera/simple-uber-eats)

A lightweight Home Assistant custom integration for tracking active Uber Eats orders using native Home Assistant entities.

## Overview

Simple Uber Eats is designed around Home Assistant's native entity model. It has no custom sidebar panel, custom Lovelace frontend, built-in TTS, or order-history database/UI. Current order data is exposed through standard sensors, binary sensors, and a device tracker so it can be used with normal dashboards and automations.

This integration is unofficial and uses Uber's web endpoints rather than a public supported API.

## Features

- Account connectivity monitoring
- Active-order detection
- Uber's visible order status text, with a deterministic fallback
- Authoritative timezone-aware ETA timestamp without per-second state writes
- Restaurant coordinates for direct use in the native Map card
- Restaurant and courier entities with clean display names
- Native courier `device_tracker`
- Smooth courier movement at approximately 1 Hz using buffered interpolation
- No extra Uber requests for smooth movement
- Adaptive API polling:
  - 60 seconds with no active order
  - 15 seconds with an active order but no valid courier
  - 10 seconds with an active order and valid courier
- Bounded HTTP 429 backoff: 60, 120, then 300 seconds
- Session/cookie rotation
- Reconfigure and reauthentication flows
- Sanitized downloadable diagnostics
- Multiple independent accounts/config entries

## Entities

| Name | Platform | Purpose |
| --- | --- | --- |
| Account connected | `binary_sensor` | Distinguishes a successful connection, confirmed authentication failure, and temporary unavailability |
| Active order | `binary_sensor` | Indicates whether Uber currently reports an active order |
| Order status | `sensor` | Uber's visible status text for the primary active order |
| ETA | `sensor` | Estimated arrival timestamp reported by Uber |
| Restaurant | `sensor` | Restaurant name with active latitude/longitude attributes |
| Courier | `sensor` | Assigned courier name |
| Courier | `device_tracker` | Latest displayed courier position for Home Assistant maps |

When Uber supplies visible status wording, the integration preserves that text,
including its casing and punctuation. Examples include `Picking up your
order…`, `Heading your way…`, and `Oops, finding another delivery person...`.
If visible wording is absent, the deterministic fallback values are:

- `preparing`
- `picked up`
- `en route`
- `arriving`
- `delivered`
- `unknown`

Completion is authoritative when Uber no longer returns an active order.

The ETA state is Uber's timezone-aware estimated arrival timestamp. It updates
only when the coordinator receives new order data; the integration does not
run a per-second ETA timer or publish per-second ETA state changes. The same
authoritative value remains available in the `arrival_time` attribute.
Dashboards can calculate a local countdown from this timestamp if desired.

Fresh installations display the retained entities as **Restaurant**,
**Courier**, and **ETA**. Their released unique IDs remain stable. During a 2.x
import, Home Assistant's public registry migration API preserves existing entity
IDs and user registry settings when the legacy entities are unloaded. If that
safe migration is unavailable, Home Assistant creates new-domain registry
entries instead; users can manually rename those entity IDs after removing the
old integration.

For accounts with multiple simultaneous orders, the entities represent the first active order returned by Uber. Each config entry has its own independent entity set.

## Smooth courier tracking

Courier movement uses this local pipeline:

```text
Uber API
  -> courier_path_points
  -> local 12-second playback buffer
  -> timestamp-based interpolation
  -> device_tracker updates approximately once per second
```

Uber is **not** polled once per second. The coordinator fetches at the adaptive intervals above, and the tracker locally interpolates only between real, timestamped Uber telemetry points. It never dead-reckons beyond the newest real point. If buffered data runs out, the tracker freezes safely and resumes when newer telemetry arrives.

Before a courier is assigned, the tracker remains idle without inventing a location.

## Standard Home Assistant Map card

Use the native courier tracker directly in Home Assistant's standard Map card:

```yaml
type: map
entities:
  - entity: device_tracker.example
    label_mode: icon
```

`label_mode: icon` displays the entity icon instead of a text or initial marker. Replace `device_tracker.example` with the courier tracker created for your account.

The Restaurant sensor can also be added directly to a Map card while an order
is active because it exposes validated `latitude` and `longitude` attributes.
Those attributes are removed when the order completes.

## Installation

Simple Uber Eats 3.0 requires Home Assistant 2026.8 or newer.

### HACS custom repository

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=alfredolvera&repository=simple-uber-eats&category=integration)

1. Open HACS in Home Assistant.
2. Select **Integrations** and open the menu in the upper-right corner.
3. Select **Custom repositories**.
4. Add `https://github.com/alfredolvera/simple-uber-eats` as an **Integration** repository.
5. Search for **Simple Uber Eats** and install it.
6. Restart Home Assistant.
7. Go to **Settings → Devices & services → Add integration** and search for **Simple Uber Eats**.

### Manual installation

1. Download the source from [alfredolvera/simple-uber-eats](https://github.com/alfredolvera/simple-uber-eats).
2. Copy `custom_components/simple_uber_eats/` into Home Assistant's `/config/custom_components/` directory.
3. Restart Home Assistant.
4. Add **Simple Uber Eats** from **Settings → Devices & services**.

## Upgrading from 2.x

Version 3.0 changes the Home Assistant integration domain from `uber_eats` to
`simple_uber_eats`. It creates a new config entry because Home Assistant does
not support changing the domain of an existing entry in place.

1. Install or update to Simple Uber Eats 3.0 and restart Home Assistant.
2. Do not delete the legacy `uber_eats` config entry yet.
3. Add **Simple Uber Eats** from **Settings → Devices & services**.
4. Select the detected legacy account, confirm the import, and let the saved
   session pass the normal active-order and profile validation.
5. Verify the new `simple_uber_eats` entry and its entities.
6. Remove the legacy `uber_eats` config entry only after verification.
7. Remove an obsolete `/config/custom_components/uber_eats/` directory if it
   still exists, then restart Home Assistant.

The import copies only the account name, timezone, required session/cookie
fields, and existing options. It never shows the saved cookie in the flow and
does not automatically delete the legacy entry. Multiple legacy accounts are
presented for explicit selection and can be imported one at a time.

When the old entities are not loaded, Home Assistant 2026.8's public entity and
device registry APIs can move them to the new integration while preserving the
entity ID, user name, disabled state, icon, unique ID, and device association.
If the old integration is still loaded or registry ownership is ambiguous, the
safe migration is skipped and new registry entries are created. In that case,
remove the old integration after verifying 3.0 and manually rename the new
entity IDs if dashboards or automations need the previous names. Simple Uber
Eats never edits `.storage` or mutates config-entry domains.

## Authentication

Simple Uber Eats connects using an existing signed-in Uber Eats browser session.
It does not ask for your Uber email, password, or two-factor authentication
code. This is an unofficial integration using Uber's web endpoints, not an
official Uber authentication method or public API.

The integration needs the complete value of a browser request's `Cookie`
header. Use a desktop browser and follow the instructions for your browser
below.

### 1. Sign in to Uber Eats

1. Open [https://www.ubereats.com/](https://www.ubereats.com/).
2. Sign in to the Uber Eats account you want to connect.
3. Wait for the site to load fully and confirm that you are signed in.
4. Keep the Uber Eats tab open.

### 2. Find an authenticated request

`getActiveOrdersV1` is the preferred request because it is one of the
operations Simple Uber Eats uses to retrieve active orders.

#### Chrome, Edge, Brave, or another Chromium browser

1. Press **F12** or **Ctrl+Shift+I**. On macOS, press
   **Option+Command+I**.
2. Select the **Network** tab.
3. Keep Developer Tools open and reload the Uber Eats page.
4. Enter `getActiveOrdersV1` in the Network filter box.
5. Select the request named **getActiveOrdersV1**.
6. Open **Headers** and scroll to **Request Headers**.
7. Find **Cookie** and copy its complete value.

If the browser abbreviates or formats the headers, use **view source** in the
Request Headers section. If `getActiveOrdersV1` does not appear, clear the
filter, reload again, and look for an authenticated `ubereats.com` request such
as `getUserV1`.

#### Firefox

1. Press **F12** or **Ctrl+Shift+I**. On macOS, press
   **Option+Command+I**.
2. Select **Network** and reload the Uber Eats page.
3. Filter for `getActiveOrdersV1` and select the request.
4. Open **Headers** and expand **Request Headers**.
5. Find **Cookie** and copy its complete value.
6. Use the **Raw** view if the complete unformatted header is not visible.

#### Safari on macOS

If the Develop menu is hidden:

1. Open **Safari → Settings → Advanced**.
2. Enable **Show features for web developers**.

Then:

1. Return to the signed-in Uber Eats page.
2. Choose **Develop → Show Web Inspector**.
3. Open **Network** and reload Uber Eats.
4. Find and select `getActiveOrdersV1`.
5. Inspect its request headers, find **Cookie**, and copy the complete value.

### 3. Paste the complete Cookie value

Paste only the Cookie header's value into the **Uber Eats Cookie header**
field. Its general shape is:

```text
cookie1=value1; cookie2=value2; cookie3=value3; ...
```

Do not include the `Cookie:` label, add quotation marks, or copy only one
individual cookie. The complete value must include, among other cookies:

```text
sid=...
uev2.id.session=...
```

Simple Uber Eats validates the browser session before saving the account. It
extracts the required session values locally and preserves cookie rotation
returned by Uber.

### Authentication troubleshooting

- **`getActiveOrdersV1` is missing:** Make sure Developer Tools was open and
  the Network tab was recording before reloading. Confirm you are signed in,
  clear the filter, and look for `getUserV1` or another request made directly
  to `www.ubereats.com`.
- **`sid` is missing:** You probably copied only part of the header or chose
  the wrong request. Copy the complete Cookie request-header value from an
  authenticated `ubereats.com` request.
- **`uev2.id.session` is missing:** Copy the complete Cookie header, not an
  individual cookie from browser storage.
- **Uber rejected the session:** Return to Uber Eats and confirm you are still
  signed in. If needed, sign out and back in, reload the page, copy a fresh
  Cookie header, and try again.

Use **Reconfigure** on the config entry to replace credentials proactively.
When the integration confirms that authentication has expired, Home Assistant
starts its reauthentication flow and asks for a fresh Cookie header.

### Cookie security

Treat the Cookie header like a password: it represents your signed-in Uber Eats
browser session. Do not share it, post it in GitHub issues, include it in
screenshots or logs, or send it in support messages. Simple Uber Eats never
needs your Uber password or two-factor authentication code.

## Troubleshooting

- **Account connected is unavailable:** Home Assistant has not received a conclusive response, or a temporary network, rate-limit, or server problem occurred. This is not the same as invalid credentials.
- **Account connected is off:** Authentication has been conclusively rejected. Complete the reauthentication flow with a fresh browser cookie string.
- **Courier tracker is idle:** An order may not yet have an assigned courier or usable courier telemetry.
- **Courier marker stops moving:** The tracker does not extrapolate. It freezes when real telemetry runs out and resumes when new real points arrive.
- **ETA is unavailable:** Uber has not supplied a valid ETA, or the reported time falls outside the bounded plausible delivery window. The entity updates when newer coordinator data arrives.
- **More detail is needed:** Open the integration's config entry in **Settings → Devices & services**, select the menu, and download diagnostics. The diagnostic payload includes sanitized polling and tracking telemetry without authentication secrets or raw courier coordinates.

## Privacy and security

- Credentials and cookies are stored locally in your Home Assistant config entry and sent only as required to Uber's endpoints.
- Downloadable diagnostics omit cookies, authentication headers, session identifiers, names, addresses, and raw courier coordinates.
- Smooth playback remains local after Uber telemetry has been received and creates no extra Uber requests.
- This project is unofficial and is not affiliated with, endorsed by, or supported by Uber.

## Development

Simple Uber Eats 3.0 is maintained by **alfredolvera**.

The 3.0 implementation was developed with **OpenAI Codex**, which performed code implementation and refactoring under human direction, review, and live testing.

Architecture decisions, requirements, testing against real Uber Eats orders, and release decisions were directed and validated by alfredolvera.

## Credits / History

This project originated from [zodyking's Uber Eats order tracker](https://github.com/zodyking/uber-eats-order-tracker). [Jwsoat](https://github.com/Jwsoat) later maintained and improved the integration, including important authentication and session work.

The current project is maintained by [alfredolvera](https://github.com/alfredolvera). Version 3.0 is an independently reimplemented native-entity architecture with adaptive polling, sanitized diagnostics, and smooth native courier tracking. See [PROVENANCE.md](PROVENANCE.md) for the concise release-tree provenance statement.

## License

Simple Uber Eats 3.x is available under the [MIT License](LICENSE).

## Maintainer

Maintained by [alfredolvera](https://github.com/alfredolvera).
