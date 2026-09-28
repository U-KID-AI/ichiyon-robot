"""Shared JSON history producer. Production remains on the legacy full scope.

Destination policy is code-owned, never an environment variable or task path.
The future archive transition is deliberately plan-only: redirecting producers
requires a host mount, an archive publisher and a proven delta/restore protocol.
"""
from datetime import datetime
import hashlib
import os
from pathlib import Path
import stat
import uuid

DATA_ROOT = Path(__file__).resolve().parent.parent / 'data'
RECOVERY_ROOT = Path('/home/ubuntu/ichiyon-recovery-archives')
SOURCES = frozenset(('quotes.json', 'reactions.json', 'ng_words.json', 'kuji.json'))
MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024


def migration_plan():
    return {'mode': 'plan-only', 'writer': 'legacy-full-scope',
            'legacy_root': str(DATA_ROOT / 'backups'), 'recovery_root': RECOVERY_ROOT.as_posix(),
            'actions': [], 'migration_allowed': False,
            'required': ['fixed_host_mount', 'atomic_archive_publisher',
                         'every_snapshot_captured', 'source_archive_restore_equality',
                         'legacy_full_backup_and_reader_retained'],
            'rollback': 'keep legacy producer destination and full persistence scope'}


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns)


def _directory(path):
    if (path.is_symlink() or not stat.S_ISDIR(path.lstat().st_mode)
            or path.resolve(strict=True) != path.absolute()):
        raise ValueError('history_directory_invalid')


def _sync_directory(path):
    if os.name != 'nt':
        fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def write_legacy_snapshot(path):
    """Snapshot one allowlisted live JSON into the fixed historical directory.

    Returns only digest/size/name metadata. Missing live JSON is a normal no-op.
    Callers preserve their prior best-effort behavior and never log its contents.
    """
    path = Path(path).absolute()
    data = DATA_ROOT.absolute()
    if path.parent != data or path.name not in SOURCES:
        raise ValueError('history_source_outside_fixed_scope')
    # Immutable deployment mounts DATA_ROOT itself; no externally chosen root.
    _directory(data)
    try:
        before = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_SNAPSHOT_BYTES:
        raise ValueError('history_source_invalid')
    fd = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as stream:
        if _identity(os.fstat(stream.fileno())) != _identity(before):
            raise ValueError('history_source_changed')
        payload = stream.read(MAX_SNAPSHOT_BYTES + 1)
        if (_identity(os.fstat(stream.fileno())) != _identity(before)
                or _identity(path.lstat()) != _identity(before) or len(payload) != before.st_size):
            raise ValueError('history_source_changed')
    root = data / 'backups'
    # App containers run as root while the host backup runs as ubuntu. Preserve
    # the legacy traversal/read contract; the host shared root itself is 0700.
    root.mkdir(mode=0o755, exist_ok=True)
    _directory(root)
    root_identity = _identity(root.lstat())[:3]
    operation = uuid.uuid4().hex
    pending = root / ('.snapshot-' + operation + '.tmp')
    # Unique suffix prevents updates within one second overwriting history.
    name = datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + operation + '_' + path.name
    output = root / name
    fd = os.open(str(pending), os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                 getattr(os, 'O_NOFOLLOW', 0), 0o644)
    with os.fdopen(fd, 'wb') as stream:
        os.chmod(pending, 0o644)
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    _directory(root)
    if _identity(root.lstat())[:3] != root_identity:
        raise ValueError('history_directory_changed')
    # The UUID is also the O_EXCL pending name, so normal concurrent writers
    # cannot share a publication path. Refuse any existing destination,
    # including a dangling symlink, before renaming within this trusted root.
    try:
        output.lstat()
    except FileNotFoundError:
        pass
    else:
        raise ValueError('history_destination_exists')
    # Rename leaves one directory entry even if interrupted immediately after
    # publication. A link/unlink sequence can leave two hardlinks, which the
    # full persistence backup reader must reject.
    os.rename(str(pending), str(output))
    _sync_directory(root)
    return {'name': name, 'size': len(payload), 'sha256': hashlib.sha256(payload).hexdigest(),
            'destination': 'legacy-full-scope'}
