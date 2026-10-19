# Simple Uber Eats replacement implementation plan

Goal: complete the user's HACS review remediation without requesting permission
from upstream. Specification: SPEC.md. Python 3.14, Home Assistant 2026.8.

1. Core task: newly author typed models, minimized cookie handling, web protocol
decoder/client, buffered motion and independent synthetic protocol tests.
Observe tests fail before implementation and pass after. Work only in the empty
replacement directory; report consulted material and limitations.
2. HA task: independently author config lifecycle/flows/options, runtime,
registry migration, platforms, diagnostics, translations and HA tests against
the shared contract. Same source-access boundary, red/green evidence.
3. Parent review: freeze author output hashes; review correctness and independent
authoring records; fix through original implementers; test real HA APIs when
available. Verify timeout/auth/profile-failure/rate-limit/privacy/upgrade paths.
4. Integration: replace previous runtime and tests, preserving framework
metadata/assets/licenses. Update release metadata and truthful provenance,
document compatibility differences, test commands and unverified behavior.
5. Final review: complete full suite and packaging checks; report precisely what
is ready and what HACS/release/live validation still requires. No outreach.

Execution uses isolated-context agents because implementers must not inherit
the parent's exposure to predecessor source. Parent remains the exposed
specification author and final reviewer. This limit must be disclosed.
User directed completion without additional approval; proceed autonomously.

## Publication authorization

After final review, the user explicitly authorized publishing version 4.0.0 and
responding to the HACS review. The earlier no-publication boundary applied during
authoring and is superseded for this release only. No upstream permission request
was authorized or sent.
