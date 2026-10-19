# Simple Uber Eats 4.0.0

This release replaces the runtime with a newly authored implementation from a
recorded functional specification. Separate implementation contexts received
protocol and compatibility requirements without predecessor source or tests.
The corrected [provenance record](PROVENANCE.md) documents the specification
author's prior exposure, implementer access reports and review corrections.
It supersedes the unsupported implication that the historical 3.x development
process had established source isolation.

- Typed order snapshots and entry-scoped entity identity, with migration
  preserving existing entity IDs, custom names, devices and labels.
- Native sensors, ETA timestamps, restaurant maps and locally buffered courier
  tracking, with adaptive polling and authentication recovery retained.
- Isolated account cookie storage and validated session rotation.
- Courier phone and picture attributes independently opt-in through Options.
- Explicit legacy-account import and safe registry adoption when the source
  account is unloaded and its registry entries are conflict-free.

Validation includes 72 automated tests against synthetic responses and real
Home Assistant 2026.8 APIs, 199 black-box compatibility observations against
3.1.2, Ruff and Python compilation. Publication is gated on successful HACS,
hassfest and integration-test workflows for the exact release commit.
See [the implementation evidence](docs/reimplementation) for reproducible
commands, review findings and their corrections.
