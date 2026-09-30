"""P1c-2B maintenance: exact targets, fresh evidence, durable failure audit.

No timer, prune, automatic stale-operation recovery or production invocation.
The CLI defaults to plan. Apply requires a reviewed plan digest and an explicit
UTC window. The collector still requires independently verified recovery proof;
missing incident disposition blocks execution rather than inventing authority.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
import uuid

from ai_task_storage_cleanup import CATEGORIES, DEPLOY_LOCK, _target_valid, inventory_digest, object_digest
from ai_task_storage_retention import collect_snapshot
from ai_task_storage_retention_graph import build_plan

AUDIT_ROOT = Path('/home/ubuntu/ichiyon-storage-evidence/cleanup-audit')
RECOVERY_ROOT = Path('/home/ubuntu/ichiyon-recovery-archives')
MAX_PLAN_AGE = 300
MAX_WINDOW = 3600


class MaintenanceError(ValueError):
    """Fixed diagnostics only; do not disclose arbitrary subprocess errors."""


def need(condition, code):
    if not condition:
        raise MaintenanceError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def window_open(window, now):
    need(isinstance(window, dict) and set(window) == {'start', 'end'}, 'WINDOW_INVALID')
    start, end = window['start'], window['end']
    need(all(type(v) in (int, float) and 0 <= v < 10**12 for v in (start, end, now))
         and 0 < end - start <= MAX_WINDOW, 'WINDOW_INVALID')
    need(start <= now < end, 'OUTSIDE_MAINTENANCE_WINDOW')


def receipt_archive_graph(objects, receipts, archives, now, complete=True):
    """Pure conservative retention graph, including absent objects and incidents.

    Inputs are independently verified observations, not uploaded self-attestation.
    Preserve original lossless A/B receipts. An archive can be a retirement
    proposal only after every receipt/object/incident/pin edge is gone, 30 days
    elapsed, and an independently verified off-host copy remains recoverable.
    This function never retires evidence or archives.
    """
    incoming, unknown = {}, not complete
    if any(row.get('active') is not False or row.get('owner_state') not in
           (None, 'terminated_identity_verified') for row in receipts + archives):
        unknown = True
    ids = set()
    for row in objects + receipts + archives:
        key = row.get('key')
        need(isinstance(key, str) and key and key not in ids, 'GRAPH_IDENTITY_INVALID')
        ids.add(key)
    for row in objects + receipts + archives:
        references = row.get('references')
        if not isinstance(references, list) or row.get('verified') is not True:
            unknown = True
            continue
        for target in references:
            if not isinstance(target, str) or target not in ids:
                unknown = True
            else:
                incoming.setdefault(target, []).append(row['key'])
    output = []
    for row in receipts + archives:
        reasons = []
        if unknown:
            reasons.append('GRAPH_INCOMPLETE')
        if incoming.get(row['key']):
            reasons.append('RETAINED_REFERENCE')
        if row.get('active') is not False or row.get('owner_state') not in (None, 'terminated_identity_verified'):
            reasons.append('ACTIVE_OR_UNRESOLVED_OWNER')
        if row.get('incident_review_complete') is not True:
            reasons.append('INCIDENT_REVIEW_REQUIRED')
        completed = row.get('completed_at')
        if type(completed) not in (int, float) or not 0 <= completed <= now - 30 * 86400:
            reasons.append('PRESERVATION_REQUIRED')
        if row.get('off_host_restore_verified') is not True:
            reasons.append('INDEPENDENT_RECOVERY_REQUIRED')
        # Raw evidence stays lossless. Graph reports why it is retained; moving
        # it into a separately validated lossless archive needs a later writer.
        if row in receipts:
            reasons.append('LOSSLESS_RECEIPT_REQUIRED')
        output.append(dict(key=row['key'], classification='KEEP' if reasons else 'RETIREMENT_PROPOSAL',
                           referenced_by=sorted(incoming.get(row['key'], [])), reasons=reasons))
    return dict(plan_only=True, nodes=output, complete=not unknown)


def collect_receipt_archive_graph(snapshot):
    """All index pages and legacy records; reporting samples never prove absence.

    Keep raw lossless receipts. Local archive checks do not prove off-host
    recovery or incident resolution, so those obligations remain explicit.
    """
    from ai_task_storage_evidence import ROOT, read_receipt, _indexed_payload, _owner_state
    from ai_task_storage_evidence_store import read_session, PAGE_LIMIT
    from ai_task_backup import directory, checksums, archive_inventory, digest as file_digest
    complete = snapshot.get('references_complete') is True and not snapshot.get('collection_errors')
    objects, receipts, archives = [], [], []
    by_key = {}
    for category in CATEGORIES:
        for node in snapshot.get(category, []):
            key = category + ':' + node['id']
            by_key[key] = dict(key=key, verified=node.get('references_complete') is True,
                references=['archive:' + ident for ident in node.get('recovery_archive_ids', [])])

    def add(payload, classification, keys):
        receipt_key = 'receipt:' + payload['operation_id']
        terminal = payload['state'] != 'running'
        row = dict(key=receipt_key, verified=True, references=[], active=not terminal,
            owner_state=_owner_state(payload, terminal), completed_at=payload.get('completed_at'),
            incident_review_complete=False, off_host_restore_verified=False, classification=classification)
        receipts.append(row)
        for category, ident in keys:
            matches = [key for key in by_key if key == category + ':' + ident or
                       category == 'staging' and key.startswith('staging:') and Path(key[8:]).name == ident]
            for key in matches:
                row['references'].extend(ref for ref in by_key[key]['references'] if ref.startswith('archive:'))
                by_key[key]['references'].append(receipt_key)
        row['references'] = sorted(set(row['references']))
    try:
        terminal_ids = set()
        legacy = ROOT / 'operations'
        if legacy.exists() or legacy.is_symlink():
            with open_directory(legacy) as fd:
                names = sorted(os.listdir(fd))
            for name in names:
                payload = read_receipt(legacy / name)
                terminal_ids.add(payload['operation_id'])
                keys = [('staging', Path(row['path']).name) for row in payload['staging']]
                keys += [('releases', payload['target_sha']), ('backups', payload['target_sha'])]
                add(payload, 'legacy', keys)
        active = ROOT / 'active'
        if active.exists() or active.is_symlink():
            with open_directory(active) as fd:
                names = sorted(os.listdir(fd))
            for name in names:
                payload = read_receipt(active / name, terminal=False)
                if payload['operation_id'] not in terminal_ids:
                    add(payload, 'active', [])
                    complete = False
        if (ROOT / 'v2').exists() or (ROOT / 'v2').is_symlink():
            with read_session() as store:
                store.summary()
                for reader in (store.persistent, store.incidents):
                    after = ''
                    while True:
                        page = reader(after)
                        for entry in page:
                            add(_indexed_payload(entry), entry['classification'], entry['object_keys'])
                        if len(page) < PAGE_LIMIT:
                            break
                        after = page[-1]['payload']['operation_id']
                for entry in store.active():
                    add(_indexed_payload(entry, terminal=False), 'active', [])
                    complete = False
        else:
            complete = False
        if RECOVERY_ROOT.exists() or RECOVERY_ROOT.is_symlink():
            root = directory(RECOVERY_ROOT)
            before = sorted(path.name for path in root.iterdir())
            for name in before:
                need(len(name) == 64 and all(c in '0123456789abcdef' for c in name), 'ARCHIVE_ID_INVALID')
                path = directory(root / name)
                need({p.name for p in path.iterdir()} == {'READY', 'checksums.sha256', 'recovery.tar'}
                     and (path / 'READY').stat().st_size == 0, 'ARCHIVE_LAYOUT_INVALID')
                observed = target_tree(path)
                sums = checksums(path, {'recovery.tar'})
                need(sums['recovery.tar'] == name and file_digest(path / 'recovery.tar') == name, 'ARCHIVE_CHECKSUM_INVALID')
                archive_inventory(path / 'recovery.tar', recovery=True)
                need(target_tree(path) == observed, 'ARCHIVE_CHANGED')
                archives.append(dict(key='archive:' + name, references=[], verified=True, active=False,
                    completed_at=None, incident_review_complete=False, off_host_restore_verified=False))
            need(before == sorted(path.name for path in root.iterdir()), 'ARCHIVE_INVENTORY_CHANGED')
    except (OSError, ValueError, KeyError, TypeError):
        complete = False
    return receipt_archive_graph(list(by_key.values()), receipts, archives,
                                 snapshot.get('captured_end_at', 0), complete=complete)


def signature(info):
    return [info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def mount_points():
    """Kernel mount table also detects bind mounts on the same device."""
    rows = Path('/proc/self/mountinfo').read_text(encoding='utf-8').splitlines()
    result = []
    for row in rows:
        fields = row.split()
        need(len(fields) >= 10 and '-' in fields, 'MOUNT_TABLE_INVALID')
        value = re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), fields[4])
        path = Path(value)
        need(path.is_absolute() and '..' not in path.parts, 'MOUNT_TABLE_INVALID')
        result.append(path)
    need(result, 'MOUNT_TABLE_UNAVAILABLE')
    return result


def reject_target_mounts(path):
    path = Path(path)
    need(not any(mount == path or path in mount.parents for mount in mount_points()), 'TARGET_CONTAINS_MOUNT')


@contextmanager
def open_directory(path):
    """Walk every absolute component without following symlinks."""
    path = Path(path)
    need(path.is_absolute() and '..' not in path.parts, 'PATH_INVALID')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def tree_inventory(fd, device=None, prefix=''):
    """Hash all members by descriptor; reject links, mounts and special files."""
    root = os.fstat(fd)
    device = root.st_dev if device is None else device
    need(root.st_dev == device, 'MOUNT_CROSSING')
    records = [dict(path=prefix, signature=signature(root), sha256=None)]
    before_names = sorted(os.listdir(fd))
    for name in before_names:
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        need(info.st_dev == device, 'MOUNT_CROSSING')
        relative = prefix + '/' + name
        if stat.S_ISDIR(info.st_mode):
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                need(signature(os.fstat(child)) == signature(info), 'IDENTITY_CHANGED')
                records.extend(tree_inventory(child, device, relative))
            finally:
                os.close(child)
        else:
            need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'LINK_OR_SPECIAL_FILE')
            child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                need(signature(os.fstat(child)) == signature(info), 'IDENTITY_CHANGED')
                h = hashlib.sha256()
                while True:
                    data = os.read(child, 1024 * 1024)
                    if not data:
                        break
                    h.update(data)
                need(signature(os.fstat(child)) == signature(info), 'CONTENT_CHANGED')
                records.append(dict(path=relative, signature=signature(info), sha256=h.hexdigest()))
            finally:
                os.close(child)
        need(signature(os.stat(name, dir_fd=fd, follow_symlinks=False)) == signature(info), 'IDENTITY_CHANGED')
    need(before_names == sorted(os.listdir(fd)) and signature(os.fstat(fd)) == signature(root), 'TREE_CHANGED')
    return records


def target_tree(path):
    reject_target_mounts(path)
    with open_directory(path) as fd:
        result = tree_inventory(fd)
    reject_target_mounts(path)
    return result


def maintenance_plan(snapshot):
    from ai_task_storage_disposition import review_object_digest
    retention = build_plan(snapshot)
    raw = {(c, n['id']): n for c in CATEGORIES for n in snapshot.get(c, [])}
    targets, blocked = [], []
    for category in CATEGORIES:
        for item in retention['cleanup']['nodes'][category]:
            if item['classification'] != 'SAFE_TO_CLEAN':
                continue
            node = raw[(category, item['id'])]
            need(_target_valid(category, node), 'TARGET_SCOPE_INVALID')
            target = dict(category=category, id=node['id'], path=node.get('path'), object_sha256=review_object_digest(node))
            try:
                target['tree'] = None if category == 'images' else target_tree(node['path'])
                targets.append(target)
            except (OSError, MaintenanceError):
                blocked.append(dict(category=category, id=node['id'], reason='EXACT_TREE_UNVERIFIED'))
    body = dict(version=1, captured_at=snapshot.get('captured_end_at'), inventory_sha256=inventory_digest(snapshot),
                targets=targets, blocked=blocked, plan_only=True)
    return dict(body=body, sha256=digest(body))


@contextmanager
def deployment_lock():
    import fcntl
    from ai_task_storage_evidence import _uid
    path = Path(DEPLOY_LOCK)
    with open_directory(path.parent) as parent:
        fd = os.open(path.name, os.O_RDWR | os.O_NOFOLLOW, dir_fd=parent)
        try:
            value = os.fstat(fd)
            need(stat.S_ISREG(value.st_mode) and value.st_nlink == 1 and value.st_uid == _uid()
                 and stat.S_IMODE(value.st_mode) == 0o600, 'LOCK_INVALID')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            need(signature(os.stat(path.name, dir_fd=parent, follow_symlinks=False)) == signature(value), 'LOCK_REPLACED')
            yield [value.st_dev, value.st_ino]
        finally:
            os.close(fd)


def fresh_locked_snapshot(lock_identity):
    snapshot = collect_snapshot()
    observed = snapshot.get('cleanup_evidence', {}).get('lock', {})
    need(observed.get('identity') == lock_identity and observed.get('held') is True
         and observed.get('holder_pids') == [os.getpid()] and observed.get('kernel_scan_complete') is True,
         'EXCLUSIVE_LOCK_NOT_INDEPENDENTLY_OBSERVED')
    snapshot['operation_observation']['maintenance_holder_pid'] = os.getpid()
    observed['maintenance_holder_pid'] = os.getpid()
    snapshot['cleanup_evidence']['inventory_sha256'] = inventory_digest(snapshot)
    from ai_task_storage_disposition import attest_recovery_dispositions
    attest_recovery_dispositions(snapshot)
    return snapshot


class Audit:
    def __init__(self):
        from ai_task_storage_evidence import _uid
        # Root must be provisioned deliberately, not silently under untrusted parents.
        self.context = open_directory(AUDIT_ROOT)
        self.directory = self.context.__enter__()
        try:
            info = os.fstat(self.directory)
            need(info.st_uid == _uid() and stat.S_IMODE(info.st_mode) == 0o700, 'AUDIT_ROOT_INVALID')
            self.name = uuid.uuid4().hex + '.jsonl'
            self.fd = os.open(self.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.directory)
            if os.fstat(self.fd).st_uid != _uid():
                os.fchown(self.fd, _uid(), -1)
            os.fsync(self.directory)
            self.previous = '0' * 64
        except BaseException:
            if hasattr(self, 'fd'):
                os.close(self.fd)
            self.context.__exit__(None, None, None)
            raise

    def write(self, event):
        body = dict(event=event, previous_sha256=self.previous)
        checksum = digest(body)
        data = canonical(dict(body=body, sha256=checksum)) + b'\n'
        while data:
            written = os.write(self.fd, data)
            need(written > 0, 'AUDIT_WRITE_FAILED')
            data = data[written:]
        os.fsync(self.fd)
        self.previous = checksum

    def close(self):
        os.close(self.fd)
        self.context.__exit__(None, None, None)


def remove_exact(target, audit):
    guard = getattr(audit, 'mutation_guard', lambda: None)
    category, ident = target['category'], target['id']
    need(_target_valid(category, dict(id=ident, path=target['path'],
         operation_sha=Path(target['path']).name.split('-')[1].split('.')[0] if category == 'staging' else None)), 'TARGET_SCOPE_INVALID')
    if category == 'images':
        guard()
        result = subprocess.run(['docker', 'image', 'rm', ident], shell=False, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60, check=False)
        need(result.returncode == 0, 'EXACT_IMAGE_REMOVAL_FAILED')
        return
    path = Path(target['path'])
    reject_target_mounts(path)
    original_guard = guard
    def guard():
        original_guard()
        reject_target_mounts(path)
    with open_directory(path.parent) as parent:
        fd = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            need(tree_inventory(fd) == target['tree'], 'TREE_CHANGED_BEFORE_DELETE')
            expected = {r['path']: r for r in target['tree']}

            def remove_members(current, prefix=''):
                for name in sorted(os.listdir(current)):
                    key = prefix + '/' + name
                    info = os.stat(name, dir_fd=current, follow_symlinks=False)
                    need(key in expected and signature(info) == expected[key]['signature'], 'MEMBER_CHANGED')
                    audit.write(dict(state='member_started', category=category, id=ident, member=key))
                    if stat.S_ISDIR(info.st_mode):
                        child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
                        try:
                            need(signature(os.fstat(child)) == signature(info), 'MEMBER_CHANGED')
                            remove_members(child, key)
                        finally:
                            os.close(child)
                        after = os.stat(name, dir_fd=current, follow_symlinks=False)
                        need((after.st_dev, after.st_ino) == (info.st_dev, info.st_ino), 'MEMBER_REPLACED')
                        guard()
                        os.rmdir(name, dir_fd=current)
                    else:
                        need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'MEMBER_INVALID')
                        need(signature(os.stat(name, dir_fd=current, follow_symlinks=False)) == signature(info), 'MEMBER_CHANGED')
                        guard()
                        os.unlink(name, dir_fd=current)
                    os.fsync(current)
                    audit.write(dict(state='member_removed', category=category, id=ident, member=key))
            root = os.fstat(fd)
            remove_members(fd)
            after = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            need((after.st_dev, after.st_ino) == (root.st_dev, root.st_ino), 'TARGET_REPLACED')
            guard()
            os.rmdir(path.name, dir_fd=parent)
            os.fsync(parent)
        finally:
            os.close(fd)


def execute_plan(plan, window, collect=fresh_locked_snapshot, clock=time.time, lock=deployment_lock, audit_factory=Audit, remover=remove_exact):
    need(isinstance(plan, dict) and set(plan) == {'body', 'sha256'} and plan['sha256'] == digest(plan['body']), 'PLAN_DIGEST_INVALID')
    body = plan['body']
    need(body.get('version') == 1 and body.get('plan_only') is True and isinstance(body.get('targets'), list)
         and body['targets'], 'EMPTY_OR_INVALID_PLAN')
    keys = [(target['category'], target['id']) for target in body['targets']]
    need(len(keys) == len(set(keys)), 'DUPLICATE_PLAN_TARGET')
    captured = body.get('captured_at')
    need(type(captured) in (int, float) and 0 <= clock() - captured <= MAX_PLAN_AGE, 'PLAN_EXPIRED')
    window_open(window, clock())
    with lock() as identity:
        audit = audit_factory()
        try:
            audit.write(dict(state='started', plan_sha256=plan['sha256'], window=window,
                             targets=[dict(category=t['category'], id=t['id']) for t in body['targets']]))
            results = []
            for target in body['targets']:
                try:
                    window_open(window, clock())
                    snapshot = collect(identity)
                    need(type(snapshot.get('captured_end_at')) in (int, float)
                         and 0 <= clock() - snapshot['captured_end_at'] <= MAX_PLAN_AGE, 'FRESH_SNAPSHOT_EXPIRED')
                    fresh = maintenance_plan(snapshot)
                    eligible = [t for t in fresh['body']['targets'] if (t['category'], t['id']) == (target['category'], target['id'])]
                    need(eligible == [target], 'FRESH_ELIGIBILITY_OR_IDENTITY_CHANGED')
                    window_open(window, clock())
                    audit.write(dict(state='target_started', target=target))
                    def mutation_guard():
                        window_open(window, clock())
                        need(0 <= clock() - snapshot['captured_end_at'] <= MAX_PLAN_AGE, 'FRESH_SNAPSHOT_EXPIRED')
                    audit.mutation_guard = mutation_guard
                    mutation_guard()
                    remover(target, audit)
                    result = dict(category=target['category'], id=target['id'], state='removed')
                    audit.write(result)
                    results.append(result)
                except (OSError, ValueError, subprocess.SubprocessError):
                    # Do not retry a partly removed tree against the old proof.
                    result = dict(category=target['category'], id=target['id'], state='failed_or_partial', reason='REPLAN_REQUIRED')
                    audit.write(result)
                    results.append(result)
                    break
            for target in body['targets'][len(results):]:
                result = dict(category=target['category'], id=target['id'], state='not_attempted', reason='EARLIER_FAILURE')
                audit.write(result)
                results.append(result)
            audit.write(dict(state='completed' if len(results) == len(body['targets']) and all(r['state'] == 'removed' for r in results) else 'partial_failure', results=results))
            return results
        finally:
            audit.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply-plan', type=Path)
    parser.add_argument('--expect-plan-sha256')
    parser.add_argument('--window-start', type=float)
    parser.add_argument('--window-end', type=float)
    args = parser.parse_args(argv)
    if args.apply_plan:
        plan = json.loads(args.apply_plan.read_text(encoding='utf-8'))
        need(plan.get('sha256') == args.expect_plan_sha256, 'REVIEWED_PLAN_DIGEST_REQUIRED')
        result = execute_plan(plan, dict(start=args.window_start, end=args.window_end))
        print(json.dumps(result, sort_keys=True))
        return 0 if all(row['state'] == 'removed' for row in result) else 1
    need(args.expect_plan_sha256 is None and args.window_start is None and args.window_end is None, 'PLAN_MODE_ARGUMENTS_INVALID')
    snapshot = collect_snapshot()
    from ai_task_storage_disposition import attest_recovery_dispositions
    attest_recovery_dispositions(snapshot)
    print(json.dumps(dict(cleanup=maintenance_plan(snapshot),
                          evidence_retention=collect_receipt_archive_graph(snapshot)), sort_keys=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
