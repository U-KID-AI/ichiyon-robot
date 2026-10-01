"""Lossless split producer and FULL-to-split migration rehearsal support.

The deployed FULL producer remains the default. This module never changes an
existing backup, deletes source history, or activates production split mode.
Callers must hold the deployment lock and provision the trusted recovery root.
Every invocation inventories *all* live files, including new historical deltas.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tarfile
import uuid

import ai_task_backup as backup


def historical(name):
    return name == 'data/backups' or name.startswith('data/backups/')


def source_inventory(shared):
    """No links, external hardlinks, special files, or mount traversal."""
    shared = backup.directory(shared)
    if sys.platform == 'linux':
        # st_dev alone misses bind mounts on the same filesystem.
        for line in Path('/proc/self/mountinfo').read_text(encoding='utf-8').splitlines():
            fields = line.split()
            backup.need(len(fields) >= 10 and '-' in fields, 'mount_table_invalid')
            mount = Path(re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), fields[4]))
            backup.need(mount != shared and shared not in mount.parents, 'source_mount_crossing')
    device = shared.stat().st_dev
    records, identities, modes = [], {}, {}

    def visit(path):
        name = path.relative_to(shared).as_posix()
        backup.canonical(name)
        before = path.lstat()
        backup.need(before.st_dev == device and not path.is_mount(), 'source_mount_crossing')
        identities[name] = backup.signature(before)
        modes[name] = stat.S_IMODE(before.st_mode)
        if stat.S_ISDIR(before.st_mode):
            backup.directory(path)
            records.append(dict(path=name, type='directory', size=0, sha256=None))
            for child in sorted(path.iterdir()):
                visit(child)
        else:
            backup.need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1,
                        'source_link_or_special_file')
            records.append(dict(path=name, type='file', size=before.st_size, sha256=backup.digest(path)))
        backup.need(backup.signature(path.lstat()) == backup.signature(before), 'source_changed')

    for name in backup.SCOPE:
        visit(shared / name)
    # assets is an implicit parent in GNU tar's assets/images scope. Record it
    # explicitly so both forms reconstruct the same directory tree.
    assets = backup.directory(shared / 'assets')
    info = assets.lstat()
    backup.need(info.st_dev == device and not assets.is_mount(), 'source_mount_crossing')
    records.append(dict(path='assets', type='directory', size=0, sha256=None))
    identities['assets'] = backup.signature(info)
    modes['assets'] = stat.S_IMODE(info.st_mode)
    backup.need(any(row['path'] == 'data/backups' and row['type'] == 'directory' for row in records),
                'historical_root_required')
    for name, identity in identities.items():
        backup.need(backup.signature((shared / name).lstat()) == identity, 'source_changed')
    return dict(inventory=sorted(records, key=lambda row: row['path']), identities=identities, modes=modes)


class HashSink:
    def __init__(self):
        self.value = hashlib.sha256()

    def write(self, data):
        self.value.update(data)
        return len(data)

    def flush(self):
        pass


def write_tar(stream, shared, snapshot, recovery):
    """Deterministic metadata deduplicates unchanged payload across generations.

    Modes remain intact; mtime/uid/gid/names are normalized, never used as proof
    of deletion eligibility. Byte/type/path inventory is the v2 restore contract.
    """
    shared = Path(shared)
    with tarfile.open(fileobj=stream, mode='w|', format=tarfile.PAX_FORMAT) as archive:
        for row in snapshot['inventory']:
            if historical(row['path']) != recovery:
                continue
            path = shared / row['path']
            backup.need(backup.signature(path.lstat()) == snapshot['identities'][row['path']], 'source_changed')
            member = tarfile.TarInfo(row['path'])
            member.mode = snapshot['modes'][row['path']]
            member.size = row['size']
            member.type = tarfile.DIRTYPE if row['type'] == 'directory' else tarfile.REGTYPE
            if row['type'] == 'file':
                with backup.regular(path) as source:
                    archive.addfile(member, source)
            else:
                archive.addfile(member)
            backup.need(backup.signature(path.lstat()) == snapshot['identities'][row['path']], 'source_changed')


def verify_archive(root, archive_id, expected):
    root = backup.directory(backup.directory(root) / archive_id)
    before = backup.signature(root.lstat())
    backup.need({p.name for p in root.iterdir()} == {'recovery.tar', 'checksums.sha256', 'READY'},
                'recovery_members_invalid')
    backup.need(backup.content(root / 'READY', 0) == b'', 'recovery_ready_invalid')
    backup.need(backup.checksums(root, {'recovery.tar'})['recovery.tar'] == archive_id,
                'recovery_identity_invalid')
    backup.need(backup.archive_inventory(root / 'recovery.tar', recovery=True) == expected,
                'recovery_live_inventory_mismatch')
    backup.need(backup.signature(root.lstat()) == before, 'recovery_archive_changed')
    return archive_id


def publish_recovery(shared, snapshot, recovery_root):
    """Publish once per content digest; incomplete stages are retained on error."""
    root = backup.directory(recovery_root)
    expected = [row for row in snapshot['inventory'] if historical(row['path'])]
    sink = HashSink()
    write_tar(sink, shared, snapshot, True)
    archive_id = sink.value.hexdigest()
    destination = root / archive_id
    if destination.exists() or destination.is_symlink():
        return verify_archive(root, archive_id, expected)
    stage = root / ('.pending-' + uuid.uuid4().hex)
    stage.mkdir(mode=0o700)
    with backup.private_output(stage / 'recovery.tar') as stream:
        write_tar(stream, shared, snapshot, True)
        stream.flush()
        os.fsync(stream.fileno())
    backup.need(backup.digest(stage / 'recovery.tar') == archive_id, 'recovery_source_changed')
    backup.need(backup.archive_inventory(stage / 'recovery.tar', recovery=True) == expected,
                'recovery_live_inventory_mismatch')
    backup.need(source_inventory(shared) == snapshot, 'source_changed')
    backup.write_metadata(stage / 'checksums.sha256', (archive_id + '  recovery.tar\n').encode('ascii'))
    backup.write_metadata(stage / 'READY', b'')
    backup.sync_directory(stage)
    # Caller serialization is mandatory. Never replace a nonempty published
    # archive; os.rename refuses it. Existing symlinks are rejected above.
    backup.need(not destination.exists() and not destination.is_symlink(), 'recovery_publish_collision')
    stage.rename(destination)
    backup.sync_directory(root)
    return verify_archive(root, archive_id, expected)


def write_split_backup(path, target_sha, *, shared_root=backup.SHARED_ROOT,
                       backups_root=backup.BACKUPS_ROOT, releases_root=backup.RELEASES_ROOT,
                       recovery_root=backup.RECOVERY_ROOT, dump_validator=backup.list_dump):
    """Finalize a new three-input stage, preserving the complete live inventory.

    Unlike converting old backups in place, this produces one new split backup.
    Production activation requires the separately recorded FULL/split/DB/remote
    restore rehearsal. No caller-supplied inventory or exclusion is accepted.
    """
    path, root = backup.stage_directory(path, target_sha, backups_root)
    binding = (backup.directory_binding(root), backup.directory_binding(path))
    inputs = {'previous', 'infra.json', 'production.dump'}
    backup.need({p.name for p in path.iterdir()} == inputs, 'split_writer_inputs_invalid')
    before = {name: backup.signature((path / name).lstat()) for name in inputs}
    for name in sorted(inputs):
        backup.sync_input(path / name)
    snapshot = source_inventory(shared_root)
    archive_id = publish_recovery(shared_root, snapshot, recovery_root)
    with backup.private_output(path / 'persistence.tar') as stream:
        write_tar(stream, shared_root, snapshot, False)
        stream.flush()
        os.fsync(stream.fileno())
    live = backup.archive_inventory(path / 'persistence.tar', split=True)
    backup.need(live == [row for row in snapshot['inventory'] if not historical(row['path'])],
                'live_inventory_mismatch')
    previous = backup.content(path / 'previous', 1024).decode('utf-8').rstrip('\n').rsplit('/', 1)[-1]
    manifest = dict(format='ichiyon-production-backup', version=2, target_release=target_sha,
                    previous_release=previous, persistence=dict(include=backup.SCOPE[:], exclude=['data/backups']),
                    recovery=dict(archive_id=archive_id), inventory=snapshot['inventory'])
    encoded = (json.dumps(manifest, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')
    backup.need(len(encoded) <= backup.WRITER_MANIFEST_LIMIT, 'metadata_too_large')
    backup.write_metadata(path / 'manifest.json', encoded)
    backup.write_metadata(path / 'checksums.sha256', ''.join(backup.digest(path / name) + '  ' + name + '\n'
                          for name in backup.V2_CHECKSUM_FILES).encode('ascii'))
    proof = backup._validate_backup(path, Path(releases_root), Path(recovery_root), dump_validator,
                                    target_sha=target_sha, ready_required=False)
    backup.need(source_inventory(shared_root) == snapshot, 'source_changed')
    backup.need(before == {name: backup.signature((path / name).lstat()) for name in inputs}, 'backup_writer_input_changed')
    backup.need(binding == (backup.directory_binding(root), backup.directory_binding(path)), 'backup_stage_changed')
    backup.sync_directory(path)
    ready_identity = None
    try:
        with backup.private_output(path / 'READY') as stream:
            ready_identity = backup.opened_signature(os.fstat(stream.fileno()))
            stream.flush()
            os.fsync(stream.fileno())
        backup.sync_directory(path)
        backup.need(binding == (backup.directory_binding(root), backup.directory_binding(path)), 'backup_stage_changed')
        backup.need(source_inventory(shared_root) == snapshot, 'source_changed')
        backup.need(before == {name: backup.signature((path / name).lstat()) for name in inputs},
                    'backup_writer_input_changed')
    except BaseException:
        if ready_identity is not None:
            try:
                if (binding == (backup.directory_binding(root), backup.directory_binding(path))
                        and backup.opened_signature((path / 'READY').lstat()) == ready_identity):
                    (path / 'READY').unlink()  # Only this invocation's completion marker.
                    backup.sync_directory(path)
            except (OSError, backup.BackupError):
                pass
        raise
    proof['metadata_sha256']['READY'] = hashlib.sha256(b'').hexdigest()
    return proof
