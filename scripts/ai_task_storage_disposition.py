"""Fresh verification of operator-reviewed recovery disposition, P1c-2B.

The fixed namespace is deployment evidence, not task-supplied JSON. Operator
review is a scoped decision about uniqueness/incident completion; checksums do
not authenticate an attacker with the same privileged account. Independently
rehash source, preserved payload and restored rehearsal on every observation.
Never synthesize a terminal receipt or approval for an old/unowned operation.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tarfile

from ai_task_storage_cleanup import CATEGORIES, EVIDENCE_ROOT, inventory_digest, object_digest, _target_valid

ROOT = Path(EVIDENCE_ROOT)
MAX_REVIEW_BYTES = 65536
HEX = re.compile(r'[0-9a-f]{64}\Z')


def review_object_digest(node):
    """Ignore only the executor's own flock transition, never reference changes.

    Cleanup evidence still binds the complete fresh node and inventory. This
    review identity remains stable from idle plan to exclusive maintenance.
    """
    return object_digest({key: value for key, value in node.items()
                          if key != 'lock_held_during_observation'})


def content_inventory(tree):
    """Portable restored content contract; inode/ctime remain execution checks."""
    return [dict(path=row['path'], type='directory' if stat.S_ISDIR(row['signature'][2]) else 'file',
                 mode=stat.S_IMODE(row['signature'][2]),
                 size=0 if row['sha256'] is None else row['signature'][3], sha256=row['sha256']) for row in tree]


def read_review(path):
    from ai_task_storage_maintenance import open_directory, digest, need, signature
    from ai_task_storage_evidence import _uid
    with open_directory(path.parent) as parent:
        info = os.fstat(parent)
        need(info.st_uid == _uid() and stat.S_IMODE(info.st_mode) == 0o700, 'REVIEW_PARENT_INVALID')
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            info = os.fstat(fd)
            need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == _uid()
                 and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= MAX_REVIEW_BYTES, 'REVIEW_FILE_INVALID')
            raw = os.read(fd, MAX_REVIEW_BYTES + 1)
            need(len(raw) == info.st_size and signature(os.fstat(fd)) == signature(info)
                 and signature(os.stat(path.name, dir_fd=parent, follow_symlinks=False)) == signature(info), 'REVIEW_CHANGED')
            def pairs(values):
                result = {}
                for key, value in values:
                    need(key not in result, 'REVIEW_DUPLICATE_KEY')
                    result[key] = value
                return result
            envelope = json.loads(raw, object_pairs_hook=pairs)
            need(isinstance(envelope, dict) and set(envelope) == {'body', 'sha256'}
                 and isinstance(envelope['body'], dict)
                 and envelope['sha256'] == digest(envelope['body']), 'REVIEW_CHECKSUM_INVALID')
            return envelope
        finally:
            os.close(fd)


def image_archive_identity(path, expected):
    """Verify full Docker-save tar and config digest; never execute archive code."""
    from ai_task_storage_maintenance import need
    from ai_task_storage_retention import regular_stream
    checksums, manifest = {}, None
    with regular_stream(path) as stream, tarfile.open(fileobj=stream, mode='r|*') as archive:
        for member in archive:
            need(len(checksums) < 4096, 'IMAGE_ARCHIVE_ENTRY_LIMIT')
            parsed = PurePosixPath(member.name)
            need(not parsed.is_absolute() and '..' not in parsed.parts and str(parsed) == member.name.rstrip('/')
                 and (member.isfile() or member.isdir()) and member.name not in checksums, 'IMAGE_ARCHIVE_MEMBER_INVALID')
            if member.isdir():
                checksums[member.name] = None
                continue
            source = archive.extractfile(member)
            h, data, count = hashlib.sha256(), bytearray(), 0
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
                count += len(chunk)
                if member.name == 'manifest.json':
                    need(count <= MAX_REVIEW_BYTES, 'IMAGE_MANIFEST_TOO_LARGE')
                    data.extend(chunk)
            need(count == member.size, 'IMAGE_ARCHIVE_TRUNCATED')
            checksums[member.name] = h.hexdigest()
            if member.name == 'manifest.json':
                manifest = json.loads(data)
    need(isinstance(manifest, list) and len(manifest) == 1 and isinstance(manifest[0], dict), 'IMAGE_MANIFEST_INVALID')
    config_name = manifest[0].get('Config')
    need(isinstance(config_name, str) and checksums.get(config_name) == expected.removeprefix('sha256:'), 'IMAGE_ARCHIVE_IDENTITY_INVALID')
    with regular_stream(path) as stream, tarfile.open(fileobj=stream, mode='r:*') as archive:
        member = archive.getmember(config_name)
        need(member.isfile() and member.size <= MAX_REVIEW_BYTES, 'IMAGE_CONFIG_INVALID')
        raw = archive.extractfile(member).read(MAX_REVIEW_BYTES + 1)
        need(len(raw) == member.size and hashlib.sha256(raw).hexdigest() == checksums[config_name], 'IMAGE_CONFIG_CHANGED')
        config = json.loads(raw)
    need(isinstance(config, dict), 'IMAGE_CONFIG_INVALID')
    layers = manifest[0].get('Layers')
    rootfs = config.get('rootfs', {})
    need(isinstance(rootfs, dict), 'IMAGE_ROOTFS_INVALID')
    diffs = rootfs.get('diff_ids')
    need(rootfs.get('type') == 'layers' and isinstance(layers, list) and isinstance(diffs, list)
         and len(diffs) == len(layers) and all(isinstance(name, str) and checksums.get(name) is not None for name in layers)
         and len(set(layers)) == len(layers)
         and all(isinstance(diff, str) and re.fullmatch(r'sha256:[0-9a-f]{64}', diff) for diff in diffs)
         and all(checksums[name] == diff[7:] for name, diff in zip(layers, diffs)), 'IMAGE_ARCHIVE_LAYERS_INVALID')


def attest_recovery_dispositions(snapshot):
    """Add recovery proof only to already independently bound terminal objects."""
    from ai_task_storage_maintenance import target_tree, digest, need, open_directory
    from ai_task_storage_evidence import _uid
    evidence = snapshot.get('cleanup_evidence')
    if not isinstance(evidence, dict):
        return snapshot
    nodes = {(c, node['id']): node for c in CATEGORIES for node in snapshot.get(c, [])}
    receipts = {row['operation_id']: row['receipt_sha256'] for row in snapshot.get('durable_operations', {}).get('operations', [])
                if row.get('terminal') is True}
    errors = []
    for record in evidence.get('records', []):
        # Authority is observational, never cached across a missing/changed proof.
        record.update(content_inventory_verified=False, content_sha256=None,
                      format_validation=None, recovery={}, backup_validation={})
        try:
            key = (record['category'], record['id'])
            node, operation = nodes[key], record['operation']
            need(_target_valid(key[0], node) and record.get('source_verified') is True
                 and operation.get('terminal_receipt_verified') is True
                 and operation.get('owner_process_state') == 'terminated_identity_verified', 'TERMINAL_OWNERSHIP_REQUIRED')
            binding = dict(category=key[0], id=key[1], operation_id=operation['id'],
                           receipt_sha256=receipts[operation['id']], object_sha256=review_object_digest(node))
            review_path = ROOT / 'cleanup-dispositions' / (digest([key[0], key[1]]) + '.json')
            reviewed = read_review(review_path)
            review = reviewed['body']
            need(set(review) == {'version', 'binding', 'archive_id', 'content_sha256', 'verification_sha256',
                                 'unique_data', 'incident_review_complete', 'disposition_validation'}
                 and type(review['version']) is int and review['version'] == 1 and review['binding'] == binding
                 and review['unique_data'] is False and review['incident_review_complete'] is True
                 and review['disposition_validation'] in ('independent_restore_rehearsal', 'reproducible_artifact_verified')
                 and all(isinstance(review[field], str) and HEX.fullmatch(review[field]) for field in
                         ('archive_id', 'content_sha256', 'verification_sha256')), 'DISPOSITION_INVALID_OR_UNBOUND')
            preserved = ROOT / 'cleanup-preserved' / review['archive_id']
            with open_directory(preserved) as fd:
                info = os.fstat(fd)
                need(info.st_uid == _uid() and stat.S_IMODE(info.st_mode) == 0o700, 'PRESERVED_ROOT_INVALID')
            verification = read_review(preserved / 'verification.json')
            proof = verification['body']
            need(verification['sha256'] == review['verification_sha256'] and proof.get('binding') == binding
                 and proof.get('content_sha256') == review['content_sha256']
                 and proof.get('restored_content_sha256') == review['content_sha256'], 'RESTORE_PROOF_UNBOUND')
            if key[0] == 'images':
                # Imported exact image was rehearsed by an operator; independently
                # read the saved immutable config identity and full archive now.
                need(proof.get('image_restore_verified') is True and review['disposition_validation'] == 'independent_restore_rehearsal', 'IMAGE_RESTORE_REQUIRED')
                image_path = preserved / 'image.tar'
                from ai_task_storage_retention import digest_file
                need(digest_file(image_path) == review['content_sha256'], 'IMAGE_ARCHIVE_CHANGED')
                image_archive_identity(image_path, key[1])
                need(digest_file(image_path) == review['content_sha256'], 'IMAGE_ARCHIVE_CHANGED')
            else:
                trees = [target_tree(path) for path in (node['path'], preserved / 'payload', preserved / 'rehearsal')]
                need(len({tuple(tree[0]['signature'][:2]) for tree in trees}) == 3, 'RECOVERY_COPIES_NOT_INDEPENDENT')
                original, saved, restored = [content_inventory(tree) for tree in trees]
                need(original == saved == restored and digest(original) == review['content_sha256'], 'RECOVERY_CONTENT_MISMATCH')
                # Re-observe source after every preserved/restored byte was read.
                need(content_inventory(target_tree(node['path'])) == original, 'SOURCE_CHANGED_DURING_PROOF')
                complete_backup = key[0] == 'backups' or key[0] == 'staging' and '/.backup-' in node['path'] and node.get('ready') is True
                if complete_backup:
                    need(proof.get('database_restore_verified') is True
                         and review['disposition_validation'] == 'independent_restore_rehearsal'
                         , 'V2_BACKUP_AND_DATABASE_RESTORE_REQUIRED')
                    from ai_task_backup import validate_backup, validate_staged_backup
                    from ai_task_storage_retention import production_dump_list
                    options = dict(releases_root=Path('/home/ubuntu/ichiyon-releases'),
                                   recovery_root=Path('/home/ubuntu/ichiyon-recovery-archives'), dump_validator=production_dump_list)
                    if key[0] == 'backups':
                        backup_proof = validate_backup(Path(node['path']), **options)
                    else:
                        backup_proof = validate_staged_backup(Path(node['path']), operation['target_sha'],
                                                             expected_uid=_uid(), **options)
                    need(backup_proof['format_version'] == 2 and backup_proof['checksum_verified'] is True
                         and backup_proof['pg_restore_list'] is True and backup_proof['persistence_tar_verified'] is True,
                         'V2_BACKUP_VALIDATION_REQUIRED')
                    record['backup_validation'] = dict.fromkeys(('ready', 'checksum_manifest', 'checksums_match',
                        'pg_restore_list', 'tar_complete', 'previous_reference', 'rollback_contract'), True)
            # Re-open the review/proof to reject updates across the observation.
            need(read_review(review_path) == reviewed and read_review(preserved / 'verification.json') == verification,
                 'DISPOSITION_CHANGED_DURING_OBSERVATION')
            record.update(content_inventory_verified=True, content_sha256=review['content_sha256'],
                format_validation='v2_verified' if node.get('backup_format_version') == 2 else
                                  'current_verified' if node.get('validation') == 'verified' else None,
                recovery=dict(unique_data=False, evidence_preserved=True, incident_review_complete=True,
                    disposition_validation=review['disposition_validation'], proof_sha256=verification['sha256']))
        except FileNotFoundError:
            # Missing operator review is expected, not a claim of eligibility.
            continue
        except (OSError, ValueError, KeyError, TypeError, tarfile.TarError):
            errors.append('DISPOSITION_INVALID_OR_CHANGED')
    if errors:
        evidence['source_verified'] = False
    snapshot['recovery_disposition_errors'] = sorted(set(errors))
    evidence['inventory_sha256'] = inventory_digest(snapshot)
    return snapshot


def disposition_template(snapshot, category, ident, archive_id, content_sha256, verification_sha256):
    """Reviewable template only. It deliberately cannot authorize cleanup."""
    from ai_task_storage_maintenance import digest
    record = next(row for row in snapshot['cleanup_evidence']['records'] if (row['category'], row['id']) == (category, ident))
    operation_id = record['operation']['id']
    receipt = next(row for row in snapshot['durable_operations']['operations'] if row['operation_id'] == operation_id and row['terminal'])
    node = next(row for row in snapshot[category] if row['id'] == ident)
    body = dict(version=1, binding=dict(category=category, id=ident, operation_id=operation_id,
        receipt_sha256=receipt['receipt_sha256'], object_sha256=review_object_digest(node)), archive_id=archive_id,
        content_sha256=content_sha256, verification_sha256=verification_sha256, unique_data=None,
        incident_review_complete=False, disposition_validation=None)
    return dict(body=body, sha256=digest(body))
