# Storage alert delivery and split producer

The P0 admission formula, safety reserves, rejection rules, five-minute host
observation and P1c-2B cleanup contract are unchanged.

## Alert delivery

The primary bot persists its last successful delivery and per-filesystem/phase
capacity baseline in `data/storage-monitor-notification.json`.

| Observation | Delivery |
| --- | --- |
| OK to WARNING; severity or reason changes | Immediately |
| Same WARNING | Remind after 24 hours |
| Same CRITICAL | Remind after 6 hours |
| Available bytes or headroom falls by at least 1 GiB since last delivery | Immediately |
| WARNING/CRITICAL to OK | One `production storage RECOVERED` |
| Continued OK | None |

The deterioration baseline advances only after a successful send. This captures
cumulative deterioration, including increases in required capacity. It does not
change P0 classification. Transport failures persist an attempted timestamp and
retry no sooner than five minutes; failed recoveries preserve the last delivered
alert. A Discord acceptance followed by a process/filesystem failure remains an
uncertain delivery: exactly-once transport is not claimed.

Existing hourly-format notification state is accepted on upgrade, so deployment
does not restart the WARNING reminder clock. Malformed state fails closed and
logs `Storage notification unavailable` without treating each poll as first
startup. Restore its last known successful state after inspecting the file; do
not repeatedly delete it to force notifications. Numeric state is atomically
replaced, fsynced and readable by the existing host FULL backup.

## Split producer and production activation boundary

`scripts/ai_task_backup_split.py` supplies `write_split_backup()` for a new exact
backup stage containing only `production.dump`, `previous`, and `infra.json`.
It inventories the complete live scope, validates paths and file identities,
publishes historical `data/backups` to the existing content-addressed recovery
archive layout, and writes only live data to `persistence.tar`. It then validates
the union using the existing v2 validator. Every invocation captures additions
to history; an unchanged historical snapshot reuses the same verified archive.
It never modifies an existing backup, removes source history or prunes archives.

The recovery tar normalizes timestamps and owner names/IDs for deterministic
identity while preserving mode bits. Content, path and type must match the live
inventory before READY. If source content changes, the backup stays unpublished.
Interrupted stages remain for inspection. Distinct historical snapshots create
distinct archives; this is not per-file deduplication or archive garbage collection.

The deployment defaults to FULL. A fixed operator-issued receipt at
`/home/ubuntu/ichiyon-storage-evidence/split-backup-activation.json` can select
the split producer in the existing quiesced backup phase. Missing activation
keeps FULL; corrupt or incomplete activation fails before stopping apps.
The receipt requires the original FULL backup checksums, historical inventory
digest, published archive ID, rehearsal digest and all seven restoration facts.
The original FULL backup must also remain in `ichiyon-retention-pins.json`.
Archive and original backup bytes are rechecked on every invocation. Neither
an environment variable nor task text can enable exclusions.

Before issuing the activation receipt,
record live inventory, archive publication/checksum, FULL/split equality, actual
isolated PostgreSQL and persistence/history restore, current/previous release
consistency, independent off-host custody, peak capacity, and durable archive
retention. Synthetic booleans must never be issued as production proof. A receipt
checksum detects corruption; it is not a signature against the privileged
operator. The existing deployment lock serializes publication. The fixed
production recovery root remains `/home/ubuntu/ichiyon-recovery-archives`.

Tests: `check_ai_task_backup_split.py`, `check_ai_task_backup_restore.py` (with
`--postgres-bin` for two actual disposable DB producer/restore cases), and
`check_ai_task_storage_monitor.py`. Synthetic fixtures and real production-data
rehearsal must be reported separately.
