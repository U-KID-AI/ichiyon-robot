"""Read-only recovery separation plan at fixed trusted production paths.

No path arguments, apply switch, copying, moving, deletion or producer change.
An offline split fixture is not production migration or off-host restore proof.
"""
import argparse
import hashlib
import json
from pathlib import Path
import stat

from ai_task_backup import BackupError, RECOVERY_ROOT, SHARED_ROOT, canonical, digest, directory, signature


def source_inventory(root):
    root = directory(root)
    before = signature(root.lstat())
    records, identities = [], {}
    def visit(path):
        name = path.relative_to(root.parent.parent).as_posix()
        canonical(name)
        info = path.lstat()
        if info.st_dev != root.lstat().st_dev:
            raise BackupError('source_mount_crossing')
        identities[path] = signature(info)
        if stat.S_ISDIR(info.st_mode):
            directory(path)
            records.append(dict(path=name, type='directory', size=0, sha256=None))
            children = sorted(path.iterdir())
            for child in children:
                visit(child)
            if signature(path.lstat()) != signature(info):
                raise BackupError('source_changed')
        elif stat.S_ISREG(info.st_mode):
            records.append(dict(path=name, type='file', size=info.st_size, sha256=digest(path)))
            if signature(path.lstat()) != signature(info):
                raise BackupError('source_changed')
        else:
            raise BackupError('source_link_or_special_file')
    visit(root)
    if signature(root.lstat()) != before:
        raise BackupError('source_changed')
    if any(signature(path.lstat()) != observed for path, observed in identities.items()):
        raise BackupError('source_changed')
    return sorted(records, key=lambda r:r['path'])


def plan_recovery():
    result = dict(schema_version=1, mode='plan-only', source=str(SHARED_ROOT / 'data/backups'),
        recovery_root=str(RECOVERY_ROOT), production_scope_change_allowed=False,
        migration_allowed=False, actions=[], classification='NEEDS_REVIEW',
        blockers=['LIVE_JSON_SNAPSHOT_WRITERS_REQUIRE_REDIRECTION_OR_EVERY_BACKUP_DELTA',
                  'PRODUCTION_SOURCE_AND_RECOVERY_INVENTORY_EQUALITY_NOT_PROVEN',
                  'ISOLATED_PRODUCTION_RESTORE_AND_DURABLE_ARCHIVE_RETENTION_NOT_PROVEN'])
    try:
        records = source_inventory(SHARED_ROOT / 'data/backups')
        encoded = json.dumps(records, sort_keys=True, separators=(',', ':')).encode()
        result.update(source_inventory_sha256=hashlib.sha256(encoded).hexdigest(),
            entries=len(records), files=sum(r['type']=='file' for r in records),
            payload_bytes=sum(r['size'] for r in records),
            subtrees=sorted({r['path'].split('/')[2] for r in records if len(r['path'].split('/'))>2}))
    except (OSError, BackupError, ValueError):
        result['blockers'].append('SOURCE_INVENTORY_UNAVAILABLE_OR_CHANGED')
    return result


def main(argv=None):
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    print(json.dumps(plan_recovery(), sort_keys=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
