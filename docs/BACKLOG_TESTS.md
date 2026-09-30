# Backlog cleanup validation — 2026-09-30

The final Linux Python 3.11 checker results are recorded individually in
[BACKLOG_TEST_RESULTS.json](BACKLOG_TEST_RESULTS.json): **43 checks, 43 PASS**.
Python compile of `admin`, `bot`, `scripts` and `git diff --check` also pass.

The related suites include all `check_ai_task*.py` scripts, storage recovery
history, resource-pack version enforcement tests, Minecraft status/diagnostics,
managed deployment, deploy safety, backup scripts, NetherNet, lead anchors,
cosmetics apply, NaritaBridge and YouTube Cookie monitoring. The new P1c-2B
maintenance suite has 21 tests and disposition suite has 25 tests, without skips
on Linux. The local backup restore suite passes 49 tests with one isolated
PostgreSQL integration test skipped because that runtime is not installed;
GitHub CI provisions PostgreSQL 16 and explicitly runs that integration test.
Windows-only skips are not treated as proof of Linux descriptor/flock behavior.

Initial failures and final disposition:

| Failure | Cause and resolution | Final result |
| --- | --- | --- |
| deploy archive validation / diagnostics datetime | Initial WSL Python 3.10 was older than the project's Python 3.11 runtime and CI. Rechecked in an isolated Python 3.11 environment. | PASS |
| reference metadata change fixture | A same-size write could share the filesystem timestamp tick. Fixture now changes reference bytes and explicitly advances mtime. No collector checks were weakened. | PASS, 55 tests |
| v2 stage ownership stress fixture | One initial run on the older runtime hit the strict `stage_predates_operation` boundary. The full v2 suite subsequently passed on Python 3.11; the production identity/timestamp checks remain intact. | PASS |
| intermediate new disposition fixture helper | A helper directly indexed the deliberately cleared recovery authority. Changed the helper to inspect absent authority safely. | PASS, 25 tests |
| document whitespace | CRLF in newly written documents caused intermediate whitespace failures. Normalized changed files to LF. | PASS |

Fixture deletion runs use `TemporaryDirectory` only. No production backup,
release, image, staging, receipt or recovery archive was deleted. CI results are
available on the replacement PR; local test success alone is not proof of
production activation, restoration or Minecraft client QA.
