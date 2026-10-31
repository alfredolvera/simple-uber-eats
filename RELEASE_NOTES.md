# Simple Uber Eats 4.0.2

Fixes missing session-renewal prompts and explains how to connect or renew an Uber Eats account directly in the setup screen.

- Detects Uber's HTTP 200 `NO_USER_UUID` failure response, including both string and numeric `400` codes, as a rejected session. Home Assistant can now request reauthentication rather than retry indefinitely as if the service were temporarily unavailable.
- Adds six numbered steps for signing in, opening Developer Tools, selecting Network, finding the request, copying it as cURL, and pasting it into Home Assistant.
- Includes keyboard alternatives, help when the request does not appear, clearer field labels and errors, and Spanish translations.

If your session has expired, open **Settings → Devices & services → Simple Uber Eats → Reauthenticate** and follow the instructions to paste a fresh request. No active order is required. This update cannot renew an expired session without you signing in again.

Validation: all 83 automated tests pass with the project's pinned Home Assistant 2026.8.0 environment. Ruff, Python compilation and translation-key validation pass.
