"""Private, content-addressed recovery snapshots and bounded storage accounting.

This module never collects garbage or deletes an existing backup. Snapshots hold
references to immutable blobs; restores always create independent files. Live
packs and worlds must never be hardlinked to this store. The manifest is a commit
record: it is published only after every source and blob has been verified.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import time
import uuid


FORMAT = 'minecraft-cas-v1'
UBUNTU_WARNING_BYTES = 10_000_000_000
WINDOWS_WARNING_BYTES = 100_000_000_000
_HASH = re.compile(r'[0-9a-f]{64}')
_CHUNK = 1024 * 1024


class StorageIntegrityError(ValueError):
    pass


class StorageCapacityError(RuntimeError):
    def __init__(self, result):
        self.result = result
        super().__init__('Insufficient free space for a new large test environment')


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':')).encode('utf-8')


def _relative(value):
    if not isinstance(value, str) or not value or '\\' in value or ':' in value or '\x00' in value:
        raise StorageIntegrityError('Invalid relative snapshot path')
    parts = value.split('/')
    if any(part in ('', '.', '..') for part in parts) or PurePosixPath(value).is_absolute():
        raise StorageIntegrityError('Invalid relative snapshot path')
    return value


def _plain(path, *, missing=False):
    path = Path(os.path.abspath(path))
    for part in [*reversed(path.parents), path]:
        try:
            item = part.lstat()
        except FileNotFoundError:
            if missing:
                continue
            raise StorageIntegrityError('Snapshot path is missing: ' + str(part))
        if stat.S_ISLNK(item.st_mode) or getattr(item, 'st_file_attributes', 0) & 0x400:
            raise StorageIntegrityError('Links/reparse points are not allowed: ' + str(part))
    return path


def _regular(path):
    path = _plain(path)
    item = path.lstat()
    if not stat.S_ISREG(item.st_mode) or item.st_nlink != 1:
        raise StorageIntegrityError('A single-link regular file is required: ' + str(path))
    return item


def _identity(item):
    return (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns,
            stat.S_IMODE(item.st_mode), item.st_nlink)


def _digest(path, output=None):
    before = _regular(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
    digest = hashlib.sha256()
    size = 0
    with os.fdopen(descriptor, 'rb') as source:
        if _identity(os.fstat(source.fileno())) != _identity(before):
            raise StorageIntegrityError('Source changed before opening')
        for block in iter(lambda: source.read(_CHUNK), b''):
            digest.update(block)
            size += len(block)
            if output is not None:
                output.write(block)
        if _identity(os.fstat(source.fileno())) != _identity(before):
            raise StorageIntegrityError('Source changed while reading')
    if _identity(_regular(path)) != _identity(before) or size != before.st_size:
        raise StorageIntegrityError('Source changed while reading')
    return digest.hexdigest(), size, before


def _windows_security_api():
    import ctypes
    from ctypes import wintypes as types
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    advapi.OpenProcessToken.argtypes = [ctypes.c_void_p, types.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    advapi.GetTokenInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
                                         types.DWORD, ctypes.POINTER(types.DWORD)]
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(types.LPWSTR)]
    advapi.GetNamedSecurityInfoW.argtypes = [types.LPWSTR, ctypes.c_int, types.DWORD,
                                           ctypes.c_void_p, ctypes.c_void_p,
                                           ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
                                           ctypes.POINTER(ctypes.c_void_p)]
    advapi.GetNamedSecurityInfoW.restype = types.DWORD
    advapi.GetSecurityDescriptorControl.argtypes = [ctypes.c_void_p, ctypes.POINTER(types.WORD),
                                                   ctypes.POINTER(types.DWORD)]
    advapi.GetAclInformation.argtypes = [ctypes.c_void_p, ctypes.c_void_p, types.DWORD, ctypes.c_int]
    advapi.GetAce.argtypes = [ctypes.c_void_p, types.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        types.LPCWSTR, types.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    advapi.SetFileSecurityW.argtypes = [types.LPCWSTR, types.DWORD, ctypes.c_void_p]
    return ctypes, types, advapi, kernel


def _windows_sid_text(sid, api):
    ctypes, types, advapi, kernel = api
    text = types.LPWSTR()
    if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)):
        raise StorageIntegrityError('Cannot read a Windows store security identity')
    try:
        return text.value
    finally:
        kernel.LocalFree(ctypes.cast(text, ctypes.c_void_p))


def _windows_current_sid(api=None):
    api = api or _windows_security_api()
    ctypes, types, advapi, kernel = api
    token = ctypes.c_void_p()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise StorageIntegrityError('Cannot identify the Windows store owner')
    try:
        needed = types.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        if not needed.value:
            raise StorageIntegrityError('Cannot identify the Windows store owner')
        buffer = ctypes.create_string_buffer(needed.value)
        if not advapi.GetTokenInformation(token, 1, buffer, len(buffer), ctypes.byref(needed)):
            raise StorageIntegrityError('Cannot identify the Windows store owner')
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        return _windows_sid_text(sid, api)
    finally:
        kernel.CloseHandle(token)


def _verify_windows_private_acl(path):
    """Read the root DACL once; never repair an existing, permissive store ACL."""
    api = _windows_security_api()
    ctypes, types, advapi, kernel = api
    descriptor, dacl = ctypes.c_void_p(), ctypes.c_void_p()
    error = advapi.GetNamedSecurityInfoW(str(path), 1, 0x0004, None, None,
                                        ctypes.byref(dacl), None, ctypes.byref(descriptor))
    if error:
        raise StorageIntegrityError('Cannot verify the Windows private store ACL')
    try:
        control, revision = types.WORD(), types.DWORD()
        if (not advapi.GetSecurityDescriptorControl(descriptor, ctypes.byref(control), ctypes.byref(revision))
                or not control.value & 0x1000 or not dacl.value):
            raise StorageIntegrityError('Windows store ACL must disable inheritance')

        class AclSize(ctypes.Structure):
            _fields_ = [('count', types.DWORD), ('used', types.DWORD), ('free', types.DWORD)]

        size = AclSize()
        if not advapi.GetAclInformation(dacl, ctypes.byref(size), ctypes.sizeof(size), 2):
            raise StorageIntegrityError('Cannot verify the Windows private store ACL')
        allowed = {_windows_current_sid(api), 'S-1-5-18'}
        access = {sid: 0 for sid in allowed}
        for index in range(size.count):
            ace = ctypes.c_void_p()
            if not advapi.GetAce(dacl, index, ctypes.byref(ace)):
                raise StorageIntegrityError('Cannot verify the Windows private store ACL')
            header = ctypes.string_at(ace.value, 4)
            if header[0] != 0 or header[1] & 0x10 or int.from_bytes(header[2:4], 'little') < 12:
                raise StorageIntegrityError('Windows store ACL contains unsupported or inherited access')
            sid = _windows_sid_text(ace.value + 8, api)
            if sid not in allowed:
                raise StorageIntegrityError('Windows store ACL allows another principal')
            access[sid] |= ctypes.c_uint32.from_address(ace.value + 4).value
        if any(mask & 0x001F01FF != 0x001F01FF for mask in access.values()):
            raise StorageIntegrityError('Windows store ACL must grant its owner and SYSTEM full control')
    finally:
        kernel.LocalFree(descriptor)


def _set_new_windows_private_acl(path):
    api = _windows_security_api()
    ctypes, _, advapi, kernel = api
    sid = _windows_current_sid(api)
    sddl = 'D:P(A;OICI;FA;;;' + sid + ')(A;OICI;FA;;;SY)'
    descriptor = ctypes.c_void_p()
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, ctypes.byref(descriptor), None):
        raise StorageIntegrityError('Cannot build a private Windows store ACL')
    try:
        if not advapi.SetFileSecurityW(str(path), 0x80000004, descriptor):
            raise StorageIntegrityError('Cannot protect the new Windows store ACL')
    finally:
        kernel.LocalFree(descriptor)


def _private_directory(path):
    path = _plain(path, missing=True)
    created = not path.exists()
    path.mkdir(mode=0o700, exist_ok=True)
    if not path.is_dir():
        raise StorageIntegrityError('Store is not a directory')
    if os.name == 'nt' and created:
        # chmod does not restrict Windows ACLs. Protect only this newly created
        # store; descendants inherit access for its owner and SYSTEM.
        _set_new_windows_private_acl(path)
    elif os.name != 'nt' and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise StorageIntegrityError('Store directory must be private (0700)')
    return path


def _sync_directory(path):
    if os.name != 'nt':
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _atomic_json(path, value, *, replace=False):
    path = _plain(path, missing=True)
    _plain(path.parent)
    if path.exists() and not replace:
        raise StorageIntegrityError('Refusing to replace an existing snapshot')
    if path.exists():
        _regular(path)
    temporary = path.parent / ('.' + path.name + '.new-' + uuid.uuid4().hex)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(_canonical(value) + b'\n')
            output.flush()
            os.fsync(output.fileno())
        if path.exists() and not replace:
            raise StorageIntegrityError('Concurrent snapshot publication refused')
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()  # Only the uncommitted temporary file we created.


@contextmanager
def _locked_store(store_root, *, create=False):
    store = _private_directory(store_root) if create else _plain(store_root)
    if not store.is_dir():
        raise StorageIntegrityError('Missing snapshot store')
    if os.name == 'nt':
        _verify_windows_private_acl(store)
    elif stat.S_IMODE(store.stat().st_mode) & 0o077:
        raise StorageIntegrityError('Store directory must be private (0700)')
    lock = store / '.snapshot.lock'
    _plain(lock, missing=True)
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    acquired = False
    try:
        opened = os.fstat(descriptor)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino) != (lock.stat().st_dev, lock.stat().st_ino)):
            raise StorageIntegrityError('Invalid store lock identity')
        # Windows byte locks also protect the byte beyond EOF. No marker needs
        # removal after a crash: the operating system releases this advisory lock.
        try:
            if os.name == 'nt':
                import msvcrt
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError as error:
            raise StorageIntegrityError('Snapshot store is busy') from error
        observed = _regular(lock)
        if (observed.st_dev, observed.st_ino) != (opened.st_dev, opened.st_ino):
            raise StorageIntegrityError('Store lock was replaced')
        marker = store / 'store.json'
        if create and not marker.exists():
            _atomic_json(marker, {'format': FORMAT, 'store_id': uuid.uuid4().hex})
        _regular(marker)
        identity = json.loads(marker.read_text(encoding='utf-8'))
        if (identity.get('format') != FORMAT
                or not re.fullmatch(r'[0-9a-f]{32}', identity.get('store_id', ''))):
            raise StorageIntegrityError('Invalid store identity')
        if create:
            (store / 'objects').mkdir(mode=0o700, exist_ok=True)
            _sync_directory(store)
        _plain(store / 'objects')
        yield store, identity['store_id']
    finally:
        if acquired:
            if os.name == 'nt':
                import msvcrt
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _blob(store, digest):
    if not isinstance(digest, str) or not _HASH.fullmatch(digest):
        raise StorageIntegrityError('Invalid blob hash')
    return _plain(store / 'objects' / digest[:2] / digest, missing=True)


def _verify_blob(store, entry):
    path = _blob(store, entry['sha256'])
    digest, size, observed = _digest(path)
    if (digest != entry['sha256'] or size != entry['size']
            or stat.S_IMODE(observed.st_mode) & 0o222):
        raise StorageIntegrityError('Snapshot blob is missing or corrupt')
    return path


def _roots(paths):
    if not isinstance(paths, (list, tuple)):
        raise StorageIntegrityError('Snapshot paths must be a list')
    result = sorted((_relative(value) for value in paths), key=str.casefold)
    lowered = [value.casefold() for value in result]
    if len(set(lowered)) != len(result):
        raise StorageIntegrityError('Duplicate snapshot root')
    for index, value in enumerate(lowered):
        if any(value.startswith(other + '/') for other in lowered[:index]):
            raise StorageIntegrityError('Overlapping snapshot roots')
    return result


def _entries(source_root, roots):
    result = []
    seen = set()

    def visit(relative):
        relative = _relative(relative)
        if relative.casefold() in seen:
            raise StorageIntegrityError('Case-colliding snapshot paths')
        seen.add(relative.casefold())
        path = _plain(source_root / relative)
        item = path.lstat()
        mode = stat.S_IMODE(item.st_mode)
        if mode & 0o7000:
            raise StorageIntegrityError('Special permission bits are not supported')
        row = {'path': relative, 'mode': mode, 'mtime_ns': item.st_mtime_ns}
        if stat.S_ISDIR(item.st_mode):
            row['kind'] = 'directory'
            result.append(row)
            for child in sorted(path.iterdir()):
                visit(relative + '/' + child.name)
        elif stat.S_ISREG(item.st_mode) and item.st_nlink == 1:
            row.update(kind='file', size=item.st_size, _identity=_identity(item))
            result.append(row)
        else:
            raise StorageIntegrityError('Special files and hardlinks cannot be snapshotted')

    for relative in roots:
        visit(relative)
    return result


def snapshot_paths(source_root, relative_paths, manifest_path, store_root):
    """Commit an immutable snapshot of existing requested file/directory roots.

    Missing paths are intentionally rejected: callers record absence separately.
    Files created by an interrupted attempt remain unreferenced, never auto-GCed.
    """
    roots = _roots(relative_paths)
    source_root = _plain(source_root, missing=not roots)
    if source_root.exists() and not source_root.is_dir():
        raise StorageIntegrityError('Snapshot source root is not a directory')
    manifest_path = _plain(manifest_path, missing=True)
    if manifest_path.exists():
        raise StorageIntegrityError('Refusing to replace an existing snapshot')
    for relative in roots:
        source = source_root / relative
        if manifest_path.is_relative_to(source) or Path(os.path.abspath(store_root)).is_relative_to(source):
            raise StorageIntegrityError('Snapshot/store must be outside its source paths')
    entries = _entries(source_root, roots)
    new_bytes = reused_bytes = files = new_files = new_allocated = 0
    allocation_known = True
    with _locked_store(store_root, create=True) as (store, store_id):
        for entry in entries:
            if entry['kind'] != 'file':
                continue
            source = source_root / entry['path']
            digest, size, observed = _digest(source)
            if _identity(observed) != tuple(entry['_identity']):
                raise StorageIntegrityError('Source changed after inventory')
            entry.update(sha256=digest, size=size)
            blob = _blob(store, digest)
            if blob.exists():
                _verify_blob(store, entry)
                reused_bytes += size
            else:
                blob.parent.mkdir(mode=0o700, exist_ok=True)
                _plain(blob.parent)
                _sync_directory(store / 'objects')
                temporary = blob.parent / ('.new-' + uuid.uuid4().hex)
                descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                try:
                    with os.fdopen(descriptor, 'wb') as output:
                        copied_hash, copied_size, copied_stat = _digest(source, output)
                        output.flush()
                        os.fsync(output.fileno())
                    if (copied_hash != digest or copied_size != size
                            or _identity(copied_stat) != tuple(entry['_identity'])):
                        raise StorageIntegrityError('Source changed before blob publication')
                    if blob.exists():
                        raise StorageIntegrityError('Concurrent blob publication refused')
                    os.chmod(temporary, 0o400)
                    os.replace(temporary, blob)
                    _sync_directory(blob.parent)
                    new_bytes += size
                    new_files += 1
                    published = blob.stat()
                    if hasattr(published, 'st_blocks'):
                        new_allocated += published.st_blocks * 512
                    else:
                        allocation_known = False
                finally:
                    if temporary.exists():
                        os.chmod(temporary, 0o600)
                        temporary.unlink()
            files += 1
        # The full tree shape/identity must still match before committing.
        current = _entries(source_root, roots)
        expected = [{key: value for key, value in entry.items() if key != 'sha256'} for entry in entries]
        if current != expected:
            raise StorageIntegrityError('Source tree changed before snapshot commit')
        body = {'format': FORMAT, 'store_id': store_id, 'paths': roots,
                'created_at': time.time(),
                'entries': [{key: value for key, value in entry.items() if key != '_identity'}
                            for entry in entries]}
        checksum = hashlib.sha256(_canonical(body)).hexdigest()
        _atomic_json(manifest_path, {'body': body, 'sha256': checksum})
    return {'format': FORMAT, 'manifest_path': str(manifest_path), 'snapshot_sha256': checksum,
            'files': files, 'logical_bytes': new_bytes + reused_bytes,
            'new_blob_bytes': new_bytes, 'reused_blob_bytes': reused_bytes,
            'new_blob_files': new_files,
            'new_blob_allocated_bytes': new_allocated if allocation_known else None}


def _validate_manifest(manifest_path, store, store_id, *, verify_blobs=True):
    _regular(manifest_path)
    if Path(manifest_path).stat().st_size > 64 * 1024 * 1024:
        raise StorageIntegrityError('Snapshot manifest is too large')
    envelope = json.loads(Path(manifest_path).read_text(encoding='utf-8'))
    body = envelope.get('body')
    if (not isinstance(body, dict)
            or envelope.get('sha256') != hashlib.sha256(_canonical(body)).hexdigest()
            or body.get('format') != FORMAT or body.get('store_id') != store_id):
        raise StorageIntegrityError('Invalid or incomplete snapshot manifest')
    roots = _roots(body.get('paths'))
    entries = body.get('entries')
    if not isinstance(entries, list) or len(entries) > 500_000:
        raise StorageIntegrityError('Invalid snapshot entries')
    by_path = {}
    folded = set()
    verified = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise StorageIntegrityError('Invalid snapshot entry')
        relative = _relative(entry.get('path'))
        if (relative.casefold() in folded
                or not any(relative == root or relative.startswith(root + '/') for root in roots)):
            raise StorageIntegrityError('Duplicate or out-of-scope snapshot entry')
        folded.add(relative.casefold())
        if (type(entry.get('mode')) is not int or not 0 <= entry['mode'] <= 0o777
                or type(entry.get('mtime_ns')) is not int or entry['mtime_ns'] < 0):
            raise StorageIntegrityError('Invalid snapshot file metadata')
        kind = entry.get('kind')
        if kind == 'file':
            if type(entry.get('size')) is not int or entry['size'] < 0:
                raise StorageIntegrityError('Invalid snapshot file size')
            _blob(store, entry.get('sha256'))
            identity = (entry['sha256'], entry['size'])
            if verify_blobs and identity not in verified:
                _verify_blob(store, entry)
                verified.add(identity)
        elif kind != 'directory':
            raise StorageIntegrityError('Invalid snapshot entry kind')
        by_path[relative] = entry
    for root in roots:
        if root not in by_path:
            raise StorageIntegrityError('Incomplete snapshot root')
    for relative in by_path:
        for parent in PurePosixPath(relative).parents:
            name = parent.as_posix()
            if name in by_path and by_path[name]['kind'] != 'directory':
                raise StorageIntegrityError('A file is used as a snapshot directory')
            if any(name == root or name.startswith(root + '/') for root in roots) and name not in by_path:
                raise StorageIntegrityError('Missing snapshot parent directory')
    return body, envelope['sha256']


def validate_snapshot(manifest_path, store_root):
    """Validate the commit envelope and every referenced blob before recovery."""
    with _locked_store(store_root) as (store, store_id):
        body, _ = _validate_manifest(manifest_path, store, store_id)
    return body


@contextmanager
def snapshot_recovery(manifest_path, store_root, *, expected_checksum=None):
    """Hold the store lock from full validation through the caller's recovery.

    Acquire this before removing any live recovery target. The yielded restore
    function is valid only inside this context and never acquires another lock.
    """
    with _locked_store(store_root) as (store, store_id):
        body, checksum = _validate_manifest(manifest_path, store, store_id)
        if expected_checksum is not None and checksum != expected_checksum:
            raise StorageIntegrityError('Recovery snapshot changed before the recovery lock')
        active = True

        def restore(destination, *, paths=None, writable=True):
            if not active:
                raise StorageIntegrityError('Recovery context is no longer active')
            return _restore_validated(body, checksum, store, destination,
                                      paths=paths, writable=writable)

        try:
            yield restore
        finally:
            active = False


def restore_snapshot(manifest_path, store_root, destination, *, paths=None, writable=True):
    """Restore selected roots/subtrees into absent, independent targets."""
    with snapshot_recovery(manifest_path, store_root) as restore:
        return restore(destination, paths=paths, writable=writable)


def _restore_validated(body, checksum, store, destination, *, paths=None, writable=True):
    # The caller retains the store lock until all independent copies finish.
    destination = _plain(destination, missing=True)
    selected = _roots(paths) if paths is not None else body['paths']
    by_path = {entry['path']: entry for entry in body['entries']}
    for relative in selected:
        if relative not in by_path:
            raise StorageIntegrityError('Requested restore path is absent from snapshot')
        target = _plain(destination / relative, missing=True)
        if target.is_relative_to(store) or store.is_relative_to(target):
            raise StorageIntegrityError('Restore target overlaps the snapshot store')
        if target.exists():
            raise StorageIntegrityError('Refusing to overwrite an existing restore target')
    entries = [entry for entry in body['entries'] if any(
        entry['path'] == root or entry['path'].startswith(root + '/') for root in selected)]
    if entries:
        destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    files = size = 0
    directories = []
    for entry in sorted(entries, key=lambda item: (item['path'].count('/'), item['path'])):
        target = _plain(destination / entry['path'], missing=True)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _plain(target.parent)
        if entry['kind'] == 'directory':
            target.mkdir(mode=0o700)
            directories.append((target, entry))
            continue
        source = _blob(store, entry['sha256'])
        descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, 'wb') as output:
            digest, copied_size, _ = _digest(source, output)
            output.flush()
            os.fsync(output.fileno())
        if digest != entry['sha256'] or copied_size != entry['size']:
            raise StorageIntegrityError('Blob changed during restore; target is incomplete')
        if os.path.samefile(source, target):
            raise StorageIntegrityError('Restored files must be independent')
        os.chmod(target, entry['mode'] | (0o200 if writable else 0))
        os.utime(target, ns=(entry['mtime_ns'], entry['mtime_ns']))
        files += 1
        size += copied_size
    for target, entry in reversed(directories):
        os.chmod(target, entry['mode'] | (0o700 if writable else 0))
        os.utime(target, ns=(entry['mtime_ns'], entry['mtime_ns']))
    return {'snapshot_sha256': checksum, 'restored_paths': selected, 'files': files,
            'logical_bytes': size, 'shared_inodes': False}


def check_capacity(path, *, platform=None, purpose='development', required_bytes=0):
    """Warn on low disks; refuse new Ubuntu large tests, never block recovery."""
    if type(required_bytes) is not int or required_bytes < 0:
        raise ValueError('required_bytes must be a nonnegative integer')
    platform = (platform or sys.platform).lower()
    windows = platform in ('nt', 'windows', 'win32', 'win64')
    existing = _plain(path, missing=True)
    while not existing.exists():
        existing = existing.parent
    free = shutil.disk_usage(existing).free
    threshold = WINDOWS_WARNING_BYTES if windows else UBUNTU_WARNING_BYTES
    warnings = []
    if free < threshold:
        warnings.append('LOW_DISK_SPACE')
    if free < required_bytes:
        warnings.append('INSUFFICIENT_REQUESTED_SPACE')
    result = {'platform': 'windows' if windows else 'ubuntu', 'purpose': purpose,
              'free_bytes': free, 'warning_threshold_bytes': threshold,
              'required_bytes': required_bytes, 'warnings': warnings,
              'allowed': True, 'recovery_exempt': purpose == 'recovery'}
    if purpose == 'large_test' and not windows and free < threshold + required_bytes:
        result['allowed'] = False
        result['warnings'].append('LARGE_TEST_BLOCKED')
        raise StorageCapacityError(result)
    return result


def _usage(root):
    root = _plain(root, missing=True)
    logical = allocated = files = 0
    allocation_known = True
    errors = []
    seen = set()
    if root.exists():
        for directory, names, filenames in os.walk(root, followlinks=False):
            for name in list(names):
                item = Path(directory) / name
                if item.is_symlink() or getattr(item.lstat(), 'st_file_attributes', 0) & 0x400:
                    names.remove(name)
                    errors.append('Link directory excluded: ' + str(item))
            for name in filenames:
                item = Path(directory) / name
                try:
                    info = _regular(item)
                    identity = (info.st_dev, info.st_ino)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    files += 1
                    logical += info.st_size
                    if hasattr(info, 'st_blocks'):
                        allocated += info.st_blocks * 512
                    else:
                        allocation_known = False
                except (OSError, StorageIntegrityError) as error:
                    errors.append(str(error))
    return {'files': files, 'logical_bytes': logical,
            'allocated_bytes': allocated if allocation_known else None,
            'measurement_errors': errors}


class StorageReceipt:
    """Measure one operation directory, not the whole project or CAS history.

    `before` is JSON-serializable and can be saved with the job for process restart.
    Deltas are net footprint changes, not total I/O or guaranteed disk reclamation.
    CAS new/reused blob bytes belong in additional metadata from snapshot_paths.
    """
    def __init__(self, root, operation, purpose='development', *, before=None):
        self.root = _plain(root, missing=True)
        self.operation = str(operation)
        self.purpose = purpose
        self.before = before if before is not None else {
            'path': str(self.root), 'captured_at': time.time(), 'usage': _usage(self.root)}
        if (not isinstance(self.before, dict) or self.before.get('path') != str(self.root)
                or not isinstance(self.before.get('usage'), dict)):
            raise ValueError('Receipt baseline belongs to another operation directory')
        for name in ('files', 'logical_bytes', 'allocated_bytes'):
            value = self.before['usage'].get(name)
            if value is None and name == 'allocated_bytes':
                continue
            if type(value) is not int or value < 0:
                raise ValueError('Invalid receipt baseline')

    def finish(self, *, retained_environments=(), recovery_sources=(), **metadata):
        after = {'path': str(self.root), 'captured_at': time.time(), 'usage': _usage(self.root)}
        before_usage, after_usage = self.before['usage'], after['usage']
        delta = {name: (after_usage[name] - before_usage[name]
                        if after_usage[name] is not None and before_usage[name] is not None else None)
                 for name in ('files', 'logical_bytes', 'allocated_bytes')}
        return {'format': 'minecraft-storage-receipt-v1', 'operation': self.operation,
                'purpose': self.purpose, 'scope': 'operation_directory_net_footprint',
                'before': self.before, 'after': after, 'delta': delta,
                'retained_environments': [str(item) for item in retained_environments],
                'recovery_sources': [str(item) for item in recovery_sources],
                'capacity': check_capacity(self.root, purpose=self.purpose if self.purpose != 'large_test' else 'development'),
                'metadata': metadata}


def write_receipt(path, receipt):
    _atomic_json(path, receipt, replace=True)
