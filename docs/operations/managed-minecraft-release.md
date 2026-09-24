# Managed Minecraft Release

## Route And Proof

The normal runner now uses the existing app `DeployConfig`, then the existing
Runner API URL/token to reach `/internal/minecraft-release/{merge_sha}`. No BDS
SSH credentials are needed. The app authenticates with the existing runner token
and verifies its immutable `REVISION` before any DB read or Control API request.

Order: exact merge SHA app deployment (existing backup, migration, app/Bot health)
-> running app revision and Control API connectivity -> existing catalog `records`
(builtins minus tombstones plus production DB assets) -> existing `pack_zip`
-> existing `cosmetics_control` POST -> operation polling -> terminal proof.
The existing apply service owns backup, lock, stop/start, byte verification,
world pack references, increasing versions and rollback. None is reimplemented
in the runner. `cosmetics-applications/active.json` and the legacy adapter's guard
are unchanged. There is no raw Git-pack/SSH fallback, including on 404/503.

Completion requires all of:

- Same immutable app SHA throughout and same operation UUID throughout.
- `status=succeeded`; acceptance/HTTP 202 is never completion.
- Generated expected digest equals operation `catalog_digest`, `active_digest`
  and a fresh catalog digest read from production DB plus current builtins.
- `installed` is the boolean `true`.
- Container `state=running`, `health=healthy`, `bridge.responding=true` from
  Control API `/status`. Despite its historical name, this last field runs
  `mc-monitor status-bedrock`, proving Bedrock UDP response, not Script polling.

The catalog digest includes each stable ID and public asset fingerprint, which
includes the skin/accessory/poster blobs and metadata. A changed or missing DB
asset during application fails preservation proof. Intentional deleted assets
remain deleted. The existing apply transaction verifies staged versus installed
file bytes before publishing success. The completion summary retains SHA, UUID,
digest, DB preservation and runtime health instead of discarding the child proof.

Operation IDs are deterministic for SHA + catalog digest + optional attempt UUID.
Idle reconciliation runs the app deployment health/reconciliation first, then
rechecks a successful managed operation without rebuilding packs or restarting
BDS. App/source/API/health failure yields no completion proof. A superseding Web
application is a conflict, never another task's success. Interrupted operations
remain with the existing Control API recovery mechanism and retain backups.

## Parent Deployment Invocation

Run only after merge, from the merged trusted installation and its existing
deployment environment. This command changes production; it is not an offline
test and was not run during implementation:

```powershell
python scripts/ai_task_production_release.py --merge-sha <exact-40-character-merge-sha>
```

The helper always deploys/verifies the app first, then generates DB-managed packs
in that app. It needs the same `AI_TASK_RUNNER_DEPLOY_*` values as the normal app
adapter, plus `AI_TASK_RUNNER_API_BASE_URL`, `AI_TASK_RUNNER_API_TOKEN`,
`AI_TASK_RUNNER_ID`, `AI_TASK_RUNNER_REPO_ROOT`, `AI_TASK_RUNNER_WORKTREE_ROOT`.
It does not load dotenv, print tokens, create runners or select hosts from task
text. API requests have a 180-second bound and operation polling a 30-minute
bound. Timeouts/lease loss do not cancel a server transaction or prove rollback.

After inspecting a terminal failure/recovery and retained backups, retry with a
new attempt UUID. Reuse that UUID when observing/reissuing the same attempt:

```powershell
python scripts/ai_task_production_release.py --merge-sha <exact-merge-sha> --attempt <new-uuid>
```

Default retries of a failed deterministic operation preserve and report that
failure; they do not erase history or silently restart it. After an explicitly
named retry, continue to use its attempt UUID for reconciliation of that release.
The Control API exposes only the latest operation; if an old UUID is no longer
latest, inspect its retained operation directory before choosing a new attempt.

## Existing Windows Runner

Confirmed launcher: `C:/Users/syoub/.ichiyon-ai-runner/run.ps1`; existing repository
`C:/プロジェクト/ichiyon-robot-ai`, ID `windows-prod-runner-1`. The launcher already
sets the API base to `http://127.0.0.1:18765` through its SSH tunnel to Bot port
8000 and obtains its token through DPAPI. No launcher configuration change or new
secret is required. BDS SSH variables, if present, are no longer used by the normal
runner. App deployment SSH settings remain in use unchanged.

The parent updates that existing installation to the merged version while
preserving its dirty tree, then uses its existing launcher (do not create a
second service/task). Inspect active runner work first; do not replace code under
an active task. Server/app first, runner second:

```powershell
& 'C:/Users/syoub/.ichiyon-ai-runner/run.ps1'
```

This starts the existing normal `--once` loop, not a one-shot release command.
For the helper, use the existing protected environment setup and tunnel; the
launcher's child-process environment is not automatically inherited by a separate
PowerShell window. Never print/decrypt the DPAPI token into logs or CLI arguments.

## Verification Boundaries

Production was not modified or inspected live by these offline tests. The parent
reported app startup SHA `1b5dda5d09f9c66cab158d413eb067f9ed39fa33` and all three live
pack versions `1.1.21`; neither is proof of this future merged release. Do not
hardcode version 21/22: the existing apply service increments above both the
generated revision and installed version. Reconcile migrations through the fixed
app deployment; keep all backups and existing DB/world data.

The current Control API does not expose installed manifest versions or world
references in `/cosmetics`. It updates/verifies those through its existing apply
transaction, but the runner summary must not invent independently observed
version numbers. Parent post-deployment checks must record actual three pack
versions/world references, app/Bot startup and migration, installed runner version,
Script/NaritaBridge polling, content logs and new errors. Bedrock UDP response is
not proof that Script HTTP polling is alive. A failed or recovered operation is
not successful deployment even when the server becomes healthy again.

## Offline Checks

```text
python scripts/check_ai_task_minecraft_deploy_managed.py
python scripts/check_ai_task_minecraft_deploy.py
python scripts/check_ai_task_minecraft_runtime.py
python scripts/check_ai_task_deploy.py
python scripts/check_ai_task_local_runner.py
python scripts/check_minecraft_cosmetics_apply.py
```
