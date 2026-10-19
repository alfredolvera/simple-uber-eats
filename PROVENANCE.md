# Provenance and replacement record

## Why this record changed

The previous 3.x provenance statement described an independent reimplementation
without recording the author's source exposure. The HACS review on 2026-10-19
requested permission from the original author or evidence of an isolated
implementation process:

https://github.com/hacs/default/pull/9987#pullrequestreview-5567312921

Local development history shows an audit and replacement work in the predecessor
repository, which still contained the earlier implementation. That history does
not establish that the 3.x implementer worked without access to predecessor
source. We withdraw any implication that the old statement established that.
The maintainer chose a new implementation rather than requesting permission.

## Historical evidence

These commit IDs refer to the retained local development repository, not to
publicly available commits in this standalone repository:

| Date | Commit | Recorded event |
| --- | --- | --- |
| 2026-08-13 | `5b966094a1279d6a51e6935a73d8f04d07436858` | Audit classified inherited and uncertain components for replacement |
| 2026-08-13 | `9dbb595206615ba4c1967bc503856f8648bd5108` | New protocol components and behavioral tests |
| 2026-08-13 | `dec9b766d496b12d39e444d47d0cc70b62d7ae88` | Credential flow replacement |
| 2026-08-13 | `0d8d33e0b575ff8f2e11ef4017269f86265dc895` | Coordinator replacement |
| 2026-08-14 | `03205304f9e6776ee92abc8c1c79cce84d68cc0b` | MIT file and earlier provenance statement introduced |

The standalone public history starts at
`f1bd5dbda8e2c152e6373da5dc64225d81b27442`. Starting a new history and a
similarity scan are not evidence that the earlier implementer was unexposed.
This record does not retroactively characterize releases 3.x as isolated.

## Version 4 authoring boundary

The specification author/reviewer inspected the earlier implementation and
audit. Two implementers were started with fresh conversation contexts and
received the functional specification in [SPEC.md](docs/reimplementation/SPEC.md),
including protocol field names and persisted compatibility identifiers. They
were instructed to work in an initially empty temporary directory, without
reading the predecessor repository, tests, Git history, upstream integrations,
or earlier conversations. The task scopes, follow-up observations and implementer access reports
are retained in [the evidence directory](docs/reimplementation/evidence).

The separation is a documented agent-context and workspace procedure, not an
operating-system access restriction or a claim about a model's training data.
The specification author and final reviewer are not represented as unexposed.
Official Home Assistant, Python and aiohttp interfaces may be consulted.

The replacement uses typed delivery snapshots, a separate HTTP gateway and
credential value object, and entry-scoped entity identifiers. The old entity
identifier strings appear only in an explicit compatibility mapping for HA's
entity registry, to preserve users' dashboards and entity IDs during upgrade.
They are not the identity scheme for newly created entities.

The parent also ran the old implementation as a black box over synthetic test
inputs, and supplied observable compatibility requirements (without source or
test code) for follow-up corrections. This exposure and these corrections are
recorded rather than being represented as an entirely unexposed process.

All runtime Python modules and behavioral tests are newly authored for version
4. Earlier runtime modules are removed from the distributable integration.
Metadata, project artwork, and historical documentation are not represented as
newly authored code. Author output hashes and validation records accompany the
implementation; changes after the initial authoring boundary are documented.

## Scope and review status

This record supplies process evidence for review. It does not establish that
HACS has accepted the replacement, provide permission from upstream, or amend
the licensing status of predecessor repositories or earlier releases. The MIT
file describes the licensing offered for the new implementation; it does not
grant rights over other people's code.

The local Simple Uber Eats icon and logo are project artwork composed from
basic geometric vector shapes. Uber Eats and other third-party names and
trademarks belong to their respective owners. This unofficial community
project is not affiliated with, endorsed by, or supported by Uber.
