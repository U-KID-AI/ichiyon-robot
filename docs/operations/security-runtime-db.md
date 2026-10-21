# Runtime database role

PostgreSQL's bootstrap role (OID 10) cannot lose SUPERUSER. Keep the existing
DATABASE_URL/POSTGRES_USER/password for the fixed migration and backup helper.
The three application containers use RUNTIME_DATABASE_URL when provisioned.
This connection has its own generated credential and is not a member of the
bootstrap role. Existing credentials are never rotated or copied to that role.

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
