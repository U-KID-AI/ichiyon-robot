# Runtime database role

PostgreSQL's bootstrap role (OID 10) cannot lose SUPERUSER. Keep a dedicated
administrator DATABASE_URL/POSTGRES_USER for the fixed migration and backup helper.
The three application containers use RUNTIME_DATABASE_URL when provisioned.
This connection has its own generated credential and is not a member of the
bootstrap role. Provisioning this runtime role does not rotate existing
credentials or copy them to that role. Administrator rotation is a separate,
explicitly authorized maintenance operation described below.

Run `scripts/provision_runtime_database.py --output-env-file <new-private-file>`
once in an approved maintenance operation, after a validated database backup,
using the existing administrator DATABASE_URL. The helper creates
`ichiyon_runtime`, grants table CRUD, sequence usage and schema usage, and sets
default grants for future objects created by that migration owner. It denies
schema creation, migration-ledger writes, role creation and server file access.
It does not change table owners or business data. It refuses to overwrite an
existing role or output file, and does not print credentials.

Protect the new fragment as mode 0600. Add its RUNTIME_DATABASE_URL entry to the
existing protected deployment environment without changing other entries. Keep
the original DATABASE_URL for migrations: `ai_task_deploy_remote.sh` passes the
original environment to the one-shot migration container, whereas Compose
passes only the resolved runtime connection to Bot/Admin. Do not load the
administrator connection into those application containers. Recreate the apps
through the normal Runner release, then verify their role flags and CRUD.

For rollback, omit the newly added RUNTIME_DATABASE_URL setting and let the
existing release helper restore the previous apps. Retain the new role and
protected fragment for investigation; do not rotate existing credentials or
drop objects automatically. A failed provisioning leaves its private fragment
for reconciliation, so inspect role existence before retrying with a new file.

Only loopback host publication of PostgreSQL is configured. Remote operators
use the existing authenticated SSH path and a loopback-only local forward.
Container-to-container `db:5432` access and existing database volumes are kept.
The PostgreSQL minor upgrade and port-binding change require a separate
backed-up Compose maintenance operation; the app helper deliberately preserves
infrastructure. Never infer that changing Compose updated an already-running DB.

## Application process identity

The app Compose services accept APP_UID/APP_GID. The compatibility default is
the existing root identity; provisioned production/staging use their verified
persistent-data owner, 1001:1001. Check the actual host UID/GID rather than
assuming 1000, and verify data/images writes and read-only secret access before
setting these non-secret environment entries. Do not recursively chown user
data or change credential permissions to make startup pass. Image build and
the one-shot migration helper retain their existing operator execution path.

The application image was exercised as UID/GID 1001 with auth, uploads/TLS,
SSRF, voice crypto/playback, Minecraft media generation and notification tests.
The numeric storage notification state is atomically replaced in its writable
parent, so its historical root-owned file does not need an ownership change.
Use the normal Runner release for activation and its previous-release rollback
if health fails; retain the original APP_UID/APP_GID settings for rollback.

## Explicit administrator credential rotation

Inventory current app connections, migration settings, backup commands, Runner,
scheduled jobs, and operator configuration before using
`scripts/rotate_database_admin.py`. It is an explicit maintenance command, not
an automatic startup migration. Runtime app connections must already use their
independent role. The reviewed backup helper uses the local database socket;
the Runner control plane uses its existing API credentials.

Run on the target host as the authorized operator with `--env-file` for every
active administrator settings file, `--app-container` for each app,
`--db-container`, `--lock` pointing to the existing deployment lock, and
`--backup-root` pointing to existing protected backup storage. Without
`--execute`, the command only checks readiness. A busy release lock is never
deleted or bypassed. Do not run concurrently with an operator workflow that
does not honor that same lock.

With `--execute`, it creates and validates a custom-format DB backup, stores
protected before/pending settings, generates an independent random password,
updates only the administrator connection/password entries, and sends a SCRAM
verifier through stdin. It verifies new administrator authentication, explicit
old-password rejection, runtime reconnections, and unchanged container IDs,
start times and restart counts. No DB/app container is recreated. A failed
verification restores the credential and settings through the local socket;
concurrent file changes are retained for reconciliation.

The old POSTGRES_PASSWORD value in an existing DB container's initialization
environment is not the active database password after rotation. It becomes a
revoked value. The next planned infrastructure recreation reads the updated
protected settings; do not restart the DB solely to remove that stale value.

Keep backups restricted to their operator. Historical application rollback must
use the current administrator credential, not blindly restore an obsolete .env
from an archive. A deliberate full database/authentication rollback needs its
own maintenance plan. Never put before/pending credential files or raw dumps in
Git, CI artifacts, a PR, or an audit report.
