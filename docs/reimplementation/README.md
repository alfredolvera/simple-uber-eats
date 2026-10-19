# Version 4 implementation evidence

The [provenance record](../../PROVENANCE.md) explains why the previous 3.x
statement was corrected and identifies the actual information boundary used
for version 4. The specification author and final reviewer had prior source
exposure; the implementation agents received fresh contexts and a functional
specification, with no predecessor source or tests.

- [Specification](SPEC.md) and [execution plan](PLAN.md).
- [Core author's access and correction record](evidence/core-author.txt).
- [HA author's access and correction record](evidence/ha-author.txt).
- [Independent review and verified corrections](evidence/independent-review.txt).
- [Runtime output fingerprints](evidence/AUTHORING_SHA256.json).
- [Final local test output](evidence/publication-tests.txt): 72 passed, exit 0.
- [Final local hassfest output](evidence/publication-hassfest.txt): one valid integration, zero invalid.
- [3.1.2 black-box compatibility comparison](evidence/compatibility-summary.txt): 199 observed calls, zero mismatches after corrections.

The test suite exercises synthetic protocol responses and installed Home
Assistant 2026.8 APIs. It includes independent tests for account cookie
isolation, retry persistence across startup attempts, DST ETA inference,
registry identity/label preservation and legacy device adoption. Review found
and corrected two migration/authentication issues before publication; the
records preserve failures as well as successful reruns.

Reproduce the main checks with Python 3.14:

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/ruff check custom_components tests
.venv/bin/python -m compileall -q custom_components tests
```

The root reviewer authored the real-registry fixture using a temporary HA
instance and read-only stores. Early fixture teardown timeouts are retained
in the evidence; the final combined suite exits normally after proper store
initialization. These fixture corrections did not change runtime code.

Protocol field names and compatibility identifiers in the specification are
interface facts. Historical observations were supplied as expected behavior,
not predecessor implementation snippets. These records describe the process
and verification; HACS retains the decision about catalog admission.
