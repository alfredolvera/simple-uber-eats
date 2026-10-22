# Simple Uber Eats

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/alfredolvera/simple-uber-eats)](https://github.com/alfredolvera/simple-uber-eats/releases)
[![Maintenance: Active](https://img.shields.io/badge/maintenance-active-brightgreen.svg)](https://github.com/alfredolvera/simple-uber-eats)

A lightweight Home Assistant custom integration for tracking active Uber Eats orders using native Home Assistant entities.

![Simple Uber Eats in Home Assistant](docs/images/simple-uber-eats-overview.png)

## Overview

Simple Uber Eats is designed around Home Assistant's native entity model. Current order data is exposed through standard sensors, binary sensors, and a device tracker so it can be used with normal dashboards and automations.

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
**Courier**, and **ETA**. Version 4 migrates existing entity registry identifiers
to account-entry identifiers while preserving entity IDs, custom names and labels.

For accounts with multiple simultaneous orders, the entities represent the first active order returned by Uber. Each config entry has its own independent entity set.

## Dashboard example

![Uber Eats dashboard with map and restaurant name blurred for privacy](docs/images/uber-eats-dashboard.png)

This view shows the restaurant, Uber's order status, a large live ETA countdown, and a native map
with the restaurant, home zone, and courier. On narrow screens, the map moves
below the order information.

The screenshot shows an active order. The entire map and restaurant name are
blurred for privacy. Map data ©
[OpenStreetMap contributors](https://www.openstreetmap.org/copyright).

Install [`custom:button-card`](https://github.com/custom-cards/button-card) and
[`card-mod`](https://github.com/thomasloven/lovelace-card-mod) through HACS first.
The map is Home Assistant's built-in Map card; no map API key is required.

Create a dashboard, open its **Raw configuration editor**, and paste this
configuration. To add the view to an existing dashboard, append the item under
`views:` to its existing views instead. Replace every `YOUR_ACCOUNT` entity ID
with the corresponding entity from your Simple Uber Eats device, and change
`zone.home` if you want a different home zone.

The reusable configuration is also available as
[`docs/dashboards/uber-eats.yaml`](docs/dashboards/uber-eats.yaml).
Choose your own background and dark theme in Home Assistant. The view icon uses a built-in Material Design icon.

<details>
<summary>Full Uber Eats view YAML</summary>

```yaml
views:
- title: Uber Eats
  path: uber-eats
  icon: mdi:food-delivery
  type: panel
  cards:
  - type: custom:button-card
    show_name: false
    show_icon: false
    tap_action:
      action: none
    hold_action:
      action: none
    custom_fields:
      info:
        card:
          type: custom:button-card
          show_name: true
          name: |
            [[[[
              const restaurant =
                states['sensor.YOUR_ACCOUNT_uber_eats_restaurant']?.state;

              if (
                !restaurant ||
                restaurant === 'unknown' ||
                restaurant === 'unavailable'
              ) {
                return '';
              }

              return String(restaurant).replace(/\s+(🛒)\s*$/u, '\u00a0$1');
            ]]]]
          show_icon: false
          show_label: true
          label: |-
            [[[[
            const s = states['sensor.YOUR_ACCOUNT_uber_eats_eta'];
            const valid = s && !['unknown', 'unavailable', 'none', ''].includes(s.state);
            const target = valid ? Date.parse(s.state) : NaN;
            const seconds = Math.floor((target - Date.now()) / 1000);
            const text = !Number.isFinite(seconds) ? '—'
              : seconds <= 0 ? 'Arriving'
              : Math.floor(seconds / 60) + ':' + String(seconds % 60).padStart(2, '0');
            const family = 'Roboto, Arial, sans-serif';
            const ctx = document.createElement('canvas').getContext('2d');
            ctx.font = '650 100px ' + family;
            const metrics = ctx.measureText(text);
            const left = Math.max(0, metrics.actualBoundingBoxLeft || 0);
            const right = Math.max(metrics.width, metrics.actualBoundingBoxRight || 0);
            const width = Math.ceil(left + right) + 4;
            const ascent = Math.ceil(metrics.fontBoundingBoxAscent || 96);
            const descent = Math.ceil(metrics.fontBoundingBoxDescent || 24);
            const height = ascent + descent + 4;
            return '<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-label="' + text
              + '" width="100%" viewBox="0 0 ' + width + ' ' + height
              + '" preserveAspectRatio="xMinYMid meet" style="display:block;width:100%;height:auto;max-width:100%;color:inherit">'
              + '<text x="' + (left + 2) + '" y="' + (ascent + 2)
              + '" font-family="' + family + '" font-size="100" font-weight="650" letter-spacing="0" fill="currentColor">'
              + text + '</text></svg>';
            ]]]]
          entity: sensor.YOUR_ACCOUNT_uber_eats_eta
          triggers_update:
          - sensor.YOUR_ACCOUNT_uber_eats_eta
          - sensor.YOUR_ACCOUNT_uber_eats_restaurant
          - sensor.YOUR_ACCOUNT_uber_eats_order_status
          update_timer: 1000
          tap_action:
            action: none
          hold_action:
            action: none
          custom_fields:
            logo: <div class="uber-logo">Uber<br><span>Eats</span></div>
            status: |-
              [[[[
              const s = states['sensor.YOUR_ACCOUNT_uber_eats_order_status'];
              const raw = String(s?.state || '');
              const missing = !raw || ['unknown', 'none'].includes(raw);
              const unavailable = raw === 'unavailable';
              const text = unavailable ? 'Status unavailable' : missing ? 'No active order' : raw;
              const safe = text.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
              const color = missing || unavailable ? '#8b969d' : '#3fc889';
              return '<span class="status-dot" style="background:' + color + '"></span><span>' + safe + '</span>';
              ]]]]
            caption: Estimated time of arrival
          styles:
            card:
            - background: transparent
            - box-shadow: none
            - border: none
            - padding: '0'
            - height: 100%
            - overflow: visible
            - cursor: default
            grid:
            - grid-template-areas: '"logo" "n" "status" "l" "caption"'
            - grid-template-columns: minmax(0, 1fr)
            - grid-template-rows: repeat(5, min-content)
            - align-content: center
            - justify-items: start
            - row-gap: clamp(18px, 4dvh, 28px)
            - text-align: left
            - height: 100%
            name:
            - justify-self: start
            - text-align: left
            - white-space: normal
            - font-size: clamp(30px, 3.8vw, 46px)
            - font-weight: 600
            - line-height: '1.1'
            - overflow: visible
            label:
            - justify-self: stretch
            - width: 100%
            - max-width: 100%
            - min-width: '0'
            - box-sizing: border-box
            - padding: '0'
            - font-size: '0'
            - letter-spacing: '0'
            - line-height: '0'
            - overflow: hidden
            - white-space: nowrap
            - text-align: left
            custom_fields:
              logo:
              - justify-self: start
              - text-align: left
              status:
              - justify-self: start
              - white-space: normal
              - text-align: left
              - font-size: clamp(23px, 2.8vw, 36px)
              - line-height: '1.25'
              - display: flex
              - align-items: flex-start
              - gap: 10px
              caption:
              - justify-self: start
              - text-align: left
              - white-space: normal
              - font-size: clamp(20px, 2.4vw, 30px)
              - color: '#a7b1ba'
              - line-height: '1.2'
          extra_styles: |
            :host { display: block; height: 100%; container-type: inline-size; }
            .uber-logo { font-size: 40px; font-weight: 600; line-height: .95; letter-spacing: -1.5px; } .uber-logo span { color: #3fc889; } .status-dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; margin-top: .5em; } @media(max-width: 700px) { #container { row-gap: 16px !important; } }
      map:
        card:
          type: map
          entities:
          - entity: sensor.YOUR_ACCOUNT_uber_eats_restaurant
            name: Rest
            color: red
            label_mode: icon
            focus: false
          - entity: zone.home
          - entity: device_tracker.YOUR_ACCOUNT_uber_eats_courier
            color: green
            label_mode: icon
          geo_location_sources: []
          hours_to_show: 0.5
          default_zoom: 30
          auto_fit: true
          fit_zones: true
          show_zone_radius: false
          cluster: false
          theme_mode: dark
          scale_ruler: false
          card_mod:
            style: |
              :host { display: block; height: 100%; }
              ha-card { height: 100% !important; overflow: hidden; border-radius: 20px; border: 1px solid rgba(167,177,186,.2); }
              #root { height: 100% !important; padding-bottom: 0 !important; }
    styles:
      card:
      - background: rgba(5, 12, 15, .45)
      - box-shadow: none
      - border: none
      - border-radius: '0'
      - padding: 24px
      - height: calc(100vh - 8px)
      - height: calc(100dvh - 8px)
      - box-sizing: border-box
      - cursor: default
      grid:
      - grid-template-areas: '"info map"'
      - grid-template-columns: minmax(0, 35fr) minmax(0, 65fr)
      - grid-template-rows: minmax(0, 1fr)
      - gap: 24px
      - height: 100%
      custom_fields:
        info:
        - height: 100%
        - min-width: '0'
        - padding: 0 12px
        - box-sizing: border-box
        map:
        - height: 100%
        - min-width: '0'
    extra_styles: |-
      @media (max-width: 700px) {
        ha-card { height: auto !important; min-height: calc(100dvh - 8px); padding: 20px !important; }
        #container { grid-template-areas: 'info' 'map' !important; grid-template-columns: minmax(0, 1fr) !important; grid-template-rows: auto minmax(320px, 55dvh) !important; gap: 24px !important; }
        #info { height: auto !important; padding: 8px 0 !important; }
      }
```

</details>

`update_timer: 1000` updates the countdown locally in the browser approximately
once per second. The integration still exposes a stable ETA timestamp; this
card adds no per-second Home Assistant state writes or extra Uber API requests.
Nested button-card JavaScript templates deliberately use `[[[[ ... ]]]]` so
they are evaluated by the inner card.

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

## Authentication

Simple Uber Eats connects through an Uber Eats browser session that is already
signed in. The recommended method is to copy one browser request as cURL and
paste it into Home Assistant. You do not need to understand the request or
manually find individual cookies.

### Recommended setup: Copy as cURL

1. **Open a supported desktop browser.** Firefox, Chrome, Chromium, Edge, and
   Brave provide a direct Copy as cURL command. Safari users can use the
   alternative described below.
2. **Open Developer Tools.** Press **F12** where supported, or press
   **Ctrl+Shift+I** on Windows/Linux. On macOS, press **Command+Option+I**.
   You can also open Developer Tools from the browser's main menu.
3. **Open the Network tab.** Network shows requests made by the webpage. You
   do not need to understand or modify anything shown there.
4. **Keep Developer Tools open and visit
   [https://www.ubereats.com/](https://www.ubereats.com/).**
5. **Sign in normally if needed.** Enter your Uber password only on Uber's own
   website and complete any email OTP, SMS code, or two-factor verification
   only there. Simple Uber Eats never asks for your password or verification
   code.
6. **Reload the Uber Eats page once** while Network is open so the browser
   records a fresh set of requests.
7. **Use the Network filter box and search for `getActiveOrdersV1`.** Look for
   a request whose name contains that text. If it does not appear, clear the
   filter, reload, and try `getUserV1` as a fallback.
8. **Right-click the request.**
9. **Choose Copy → Copy as cURL.** Menu wording can differ slightly by browser;
   choose the Copy as cURL option from the request's context menu.
10. **Return to Home Assistant.**
11. **Open Settings → Devices & services → Add integration** and search for
    **Simple Uber Eats**.
12. **Paste the entire copied cURL into the Uber Eats session field.** Do not
    edit it, remove lines, or try to extract the Cookie header yourself. Simple
    Uber Eats locates the required session data automatically.
13. **Submit the form.** Parsing happens locally in Home Assistant. The
    integration extracts only `sid` and `uev2.id.session`, immediately discards
    all other request headers, cookies, URLs, and body data, and validates the
    two credentials directly with Uber Eats.
14. **Confirm setup completed.** Home Assistant creates a Simple Uber Eats
    device named for the connected account and adds its connectivity,
    active-order, status, ETA, restaurant, courier, and tracker entities.

### Browser-specific instructions

#### Firefox

1. Open Firefox.
2. Press **F12** (or **Ctrl+Shift+I**; on macOS,
   **Command+Option+I**).
3. Click **Network**.
4. Go to [ubereats.com](https://www.ubereats.com/).
5. Sign in to Uber Eats normally.
6. Reload the page if requests are not already visible.
7. Filter for `getActiveOrdersV1`.
8. Right-click the matching request.
9. Choose **Copy → Copy as cURL**.
10. Paste the entire result into the Home Assistant **Uber Eats session**
    field.

#### Chrome, Chromium, Edge, and Brave

1. Open the browser and press **F12** or **Ctrl+Shift+I** (on macOS,
   **Command+Option+I**).
2. Select **Network**.
3. Open [ubereats.com](https://www.ubereats.com/), sign in, and reload once.
4. Filter for `getActiveOrdersV1`.
5. Right-click the matching request.
6. Choose **Copy → Copy as cURL**. Some versions offer variants such as
   **Copy as cURL (bash)**; that format is accepted.
7. Paste the entire result into the Home Assistant **Uber Eats session**
   field.

#### Safari on macOS

Safari's available copy commands vary by version. If the Develop menu is not
visible, open **Safari → Settings → Advanced** and enable **Show features for
web developers**. Then choose **Develop → Show Web Inspector**, open
**Network**, reload Uber Eats, and select `getActiveOrdersV1`.

If the request context menu offers **Copy as cURL**, paste that complete result.
Otherwise, use **Copy Request Headers** if available, or copy the complete
`Cookie:` request-header line. All three formats are accepted by the same Home
Assistant field.

### Advanced alternative inputs

Copy as cURL is the easiest and recommended method. The **Uber Eats session**
field also accepts these fallback formats:

- a copied request-header block
- a complete `Cookie:` header line
- a raw cookie string

The input must contain both required values. This example is deliberately fake:

```text
sid=QA.EXAMPLE; uev2.id.session=00000000-0000-0000-0000-000000000000
```

Regardless of input format, only those two values are retained and sent in the
canonical minimum Cookie header. Response rotation of either value remains
supported.

### Session privacy and security

A copied browser request is sensitive. It can contain active session
credentials, location information, browser or device identifiers, and other
cookies. Treat it like a password:

- never post it in a GitHub issue
- never paste it into Discord, forums, or public chats
- never send it to another person
- never include it in screenshots or logs

The parser runs locally inside Home Assistant. Simple Uber Eats retains only
`sid` and `uev2.id.session`; every other copied header, cookie, URL, and body
value is discarded. Your Uber password, email OTP, SMS code, and two-factor
authentication code are never handled by the integration.

### Authentication troubleshooting

- **No `getActiveOrdersV1` request appears:** Make sure Network was open before
  reloading, reload `ubereats.com`, and confirm you are signed in. If necessary,
  clear the filter and try `getUserV1`.
- **The Network panel is empty:** Leave Developer Tools open and reload the
  webpage. Confirm the Network recording control is enabled.
- **The filter hides everything:** Clear the filter, reload, confirm requests
  appear, and then search again.
- **The copied request is rejected:** Copy it again without manually modifying
  it. Confirm the request came from `www.ubereats.com` and contains a Cookie
  request header.
- **The browser menu differs:** Open the request's context menu and look for
  **Copy as cURL** or **Copy Request Headers**. The complete `Cookie:` line is
  also accepted as a fallback.
- **The session expired:** Sign in to Uber Eats again, repeat Copy as cURL, and
  use **Reconfigure** or the Home Assistant reauthentication prompt to paste
  the fresh request.
- **Uber Eats is temporarily unavailable:** Wait and submit again. A timeout,
  rate limit, or server problem is reported separately from rejected
  credentials.

Use **Reconfigure** on the config entry to replace credentials proactively.
When the integration confirms that authentication has expired, Home Assistant
starts its reauthentication flow and asks for a fresh Uber Eats session.

## Troubleshooting

- **Account connected is unavailable:** Home Assistant has not received a conclusive response, or a temporary network, rate-limit, or server problem occurred. This is not the same as invalid credentials.
- **Account connected is off:** Authentication has been conclusively rejected. Complete the reauthentication flow with a fresh copied Uber Eats request.
- **Courier tracker is idle:** An order may not yet have an assigned courier or usable courier telemetry.
- **Courier marker stops moving:** The tracker does not extrapolate. It freezes when real telemetry runs out and resumes when new real points arrive.
- **ETA is unavailable:** Uber has not supplied a valid ETA, or the reported time falls outside the bounded plausible delivery window. The entity updates when newer coordinator data arrives.
- **More detail is needed:** Open the integration's config entry in **Settings → Devices & services**, select the menu, and download diagnostics. The diagnostic payload includes sanitized polling and tracking telemetry without authentication secrets or raw courier coordinates.

## Privacy and security

- Only `sid` and `uev2.id.session` are stored locally in the Home Assistant config entry and sent to Uber's endpoints as the minimum Cookie header.
- Courier phone and picture attributes are disabled by default. Enable either independently in the integration's **Options** if needed. Enabling them can record another person's details in Recorder and backups; disabling them does not erase previously recorded history.
- Each account uses isolated cookie storage while sharing Home Assistant's connection pool, so server cookies cannot replace another account's credentials.
- Downloadable diagnostics omit cookies, authentication headers, session identifiers, names, addresses, and raw courier coordinates.
- Smooth playback remains local after Uber telemetry has been received and creates no extra Uber requests.
- This project is unofficial and is not affiliated with, endorsed by, or supported by Uber.

## Development

Simple Uber Eats is maintained by **alfredolvera**.

Version 4 replaces the runtime and behavioral tests with a new implementation
authored by separate Codex agents from a recorded functional specification.
Implementers were instructed not to consult predecessor source or tests. The
specification author and reviewer had inspected the earlier implementation;
that exposure is explicitly recorded in [PROVENANCE.md](PROVENANCE.md).

The specification, author access reports, review corrections and verification
records are in [docs/reimplementation](docs/reimplementation). These describe
the actual process and its limits; earlier releases are not retroactively
represented as having been written in isolation.

Run the replacement tests with Python 3.14:

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/ruff check custom_components tests
```

Automated synthetic tests are separate from testing a real Uber session and
active order. See the verification record for what has actually been checked.

## Credits / History

This project originated from [zodyking's Uber Eats order tracker](https://github.com/zodyking/uber-eats-order-tracker). [Jwsoat](https://github.com/Jwsoat) later maintained and improved the integration, including important authentication and session work.

The current project is maintained by [alfredolvera](https://github.com/alfredolvera).
The development history and version 4 replacement process are documented in
[PROVENANCE.md](PROVENANCE.md). Historical credits do not imply that the
predecessor authors granted a license to their code.

## License

The version 4 implementation is offered under the [MIT License](LICENSE).
See [PROVENANCE.md](PROVENANCE.md) for the scope of that statement and the
unresolved provenance question concerning earlier releases.

## Maintainer

Maintained by [alfredolvera](https://github.com/alfredolvera).
