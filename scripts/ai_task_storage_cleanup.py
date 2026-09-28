"""Pure P1c cleanup eligibility proposal; Python 3.8, no I/O or executor.

This deliberately does not change P1a retention classifications.  SAFE_TO_CLEAN
means the supplied observation has all evidence needed for a future reviewed
operation, not permission to delete.  A future executor must acquire the existing
deployment flock and collect everything again while holding it.

The only accepted evidence namespace is EVIDENCE_ROOT/operations/<32hex>.json.
It is a *collector contract*: booleans below mean independently observed facts,
never fields trusted directly from that JSON.  The reader must verify provenance,
owner/permissions, object identities, terminal receipts, checksums, references,
and current /proc state. P1c-1 ships neither a writer nor a JSON-input CLI. Existing
deployments do not produce this evidence, and remain NEEDS_REVIEW. This module
must not reconstruct lifecycle or ownership from mtime, names, or missing PIDs.
"""

import hashlib
import json
import math
import re


HOME = '/home/ubuntu'
EVIDENCE_ROOT = HOME + '/ichiyon-storage-evidence'
DEPLOY_LOCK = HOME + '/ichiyon-deploy.lock'
CATEGORIES = ('releases', 'backups', 'images', 'staging')
CLASSIFICATIONS = ('SAFE_TO_CLEAN', 'NEEDS_REVIEW', 'ACTIVE')
PRESERVATION_SECONDS = {'staging': 7 * 86400, 'backups': 30 * 86400,
                        'releases': 30 * 86400, 'images': 30 * 86400}
SHA = re.compile(r'[0-9a-f]{40}\Z')
IMAGE = re.compile(r'sha256:[0-9a-f]{64}\Z')
OPERATION = re.compile(r'[0-9a-f]{32}\Z')
UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z')
HEX = re.compile(r'[0-9a-f]{64}\Z')


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=True, allow_nan=False).encode('ascii')).hexdigest()


def inventory_digest(snapshot):
    """Bind collector attestations to this entire inventory, excluding itself."""
    return _digest({key: value for key, value in snapshot.items() if key != 'cleanup_evidence'})


def object_digest(node):
    return _digest(node)


def _time(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _positive(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _matches(expression, value):
    return isinstance(value, str) and expression.fullmatch(value) is not None


def _strings(value):
    return isinstance(value, list) and all(isinstance(item, str) and item for item in value)


def _inventory(snapshot, category):
    value = snapshot.get(category)
    return value if isinstance(value, list) else []


def _target_valid(category, node):
    ident, path = node.get('id'), node.get('path')
    if category == 'images':
        return _matches(IMAGE, ident)
    if not isinstance(path, str) or '\\' in path or '\x00' in path:
        return False
    # Restrict names and parents before any future filesystem use. Legacy named
    # backups may be enrolled, but related ad-hoc backup roots remain excluded.
    if category in ('releases', 'backups'):
        name = ident if isinstance(ident, str) else ''
        allowed = SHA.fullmatch(name) if category == 'releases' else re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', name)
        return bool(allowed) and path == HOME + '/ichiyon-' + category + '/' + name
    match = re.fullmatch(r'/home/ubuntu/ichiyon-(releases|backups)/\.(prepare|release|backup)-([0-9a-f]{40})\.([A-Za-z0-9]{8,32})', path)
    return bool(match and ident == path and
                (match[1], match[2]) in {('releases', 'prepare'), ('releases', 'release'), ('backups', 'backup')} and
                node.get('operation_sha') == match[3])


def _global_errors(snapshot, evidence, retention):
    errors = []
    if snapshot.get('references_complete') is not True or snapshot.get('collection_errors') != []:
        errors.append('REFERENCE_OBSERVATION_INCOMPLETE')
    if retention.get('unknown_reference_kinds'):
        errors.append('UNRESOLVED_REFERENCE_KINDS')
    observation = snapshot.get('operation_observation', {})
    if not isinstance(observation, dict) or observation.get('process_scan_complete') is not True:
        errors.append('PROCESS_OBSERVATION_INCOMPLETE')
        observation = {}
    if not isinstance(evidence, dict):
        return errors + ['DURABLE_CLEANUP_EVIDENCE_UNAVAILABLE']
    if (type(evidence.get('schema_version')) is not int or evidence.get('schema_version') != 1 or evidence.get('source_root') != EVIDENCE_ROOT or
            evidence.get('source_verified') is not True):
        errors.append('EVIDENCE_PROVENANCE_UNVERIFIED')
    try:
        bound = evidence.get('inventory_sha256') == inventory_digest(snapshot)
    except (ValueError, TypeError):
        bound = False
    if not bound:
        errors.append('EVIDENCE_INVENTORY_BINDING_INVALID')
    end = snapshot.get('captured_end_at', snapshot.get('collected_at'))
    if not _time(end) or not _time(evidence.get('observed_at')) or evidence.get('observed_at') != end:
        errors.append('EVIDENCE_OBSERVATION_EPOCH_MISMATCH')
    for key in ('process_scan_complete', 'open_file_scan_complete', 'lease_scan_complete',
                'migration_reference_scan_complete', 'reference_scan_complete'):
        if evidence.get(key) is not True:
            errors.append(key.upper() + '_REQUIRED')
    lock = evidence.get('lock')
    if not isinstance(lock, dict):
        lock = {}
    if (lock.get('path') != DEPLOY_LOCK or any(lock.get(key) is not True for key in
            ('regular_file', 'no_symlink', 'identity_stable', 'kernel_scan_complete')) or
            not _positive(lock.get('inode')) or not _positive(lock.get('device')) or
            lock.get('identity') != [lock.get('device'), lock.get('inode')] or
            lock.get('identity') != observation.get('lock_identity')):
        errors.append('EXACT_DEPLOYMENT_LOCK_IDENTITY_UNVERIFIED')
    if lock.get('held') is not False or lock.get('holder_pids') != [] or observation.get('lock_held') is not False:
        errors.append('DEPLOYMENT_LOCK_NOT_OBSERVED_IDLE')
    # A read-only observation does not acquire the lock. Even SAFE proposals must
    # later be regenerated inside an exclusive lock before any mutation.
    return errors


def _reverse_references(snapshot, retention):
    result, invalid = {}, False
    identities = {category: {item['id'] for item in _inventory(snapshot, category)
                             if isinstance(item, dict) and isinstance(item.get('id'), str)}
                  for category in CATEGORIES}
    aliases = {}
    for node in _inventory(snapshot, 'images'):
        if not isinstance(node, dict) or not isinstance(node.get('id'), str):
            continue
        tags = node.get('tags', [])
        if not _strings(tags):
            invalid = True
            continue
        for alias in [node['id']] + tags:
            aliases.setdefault(alias, set()).add(node['id'])

    def add(category, ident, source):
        nonlocal invalid
        if not isinstance(ident, str) or not ident:
            return
        if category == 'images':
            resolved = aliases.get(ident, set())
            if len(resolved) != 1:
                invalid = True
                return
            ident = next(iter(resolved))
        if ident not in identities[category]:
            invalid = True
            return
        result.setdefault(category + ':' + ident, set()).add(source)

    for edge in retention.get('references', []):
        if isinstance(edge, dict) and isinstance(edge.get('to'), str):
            result.setdefault(edge['to'], set()).add(edge.get('from', 'graph'))
    # Include references from *all* objects, even other P1a candidates. A cleanup
    # proposal does not silently retire a rollback pin or jointly delete parents.
    fields = {'required_releases': 'releases', 'release_refs': 'releases',
              'required_images': 'images', 'image_refs': 'images', 'rollback_images': 'images',
              'backup_refs': 'backups', 'required_staging': 'staging', 'staging_refs': 'staging'}
    for category in CATEGORIES:
        for node in _inventory(snapshot, category):
            if not isinstance(node, dict):
                invalid = True
                continue
            source = category + ':' + str(node.get('id', '?'))
            for field, kind in fields.items():
                if field in node:
                    if not _strings(node[field]):
                        invalid = True
                    else:
                        for target in node[field]:
                            add(kind, target, source)
            if category == 'backups':
                add('releases', node.get('previous_release'), source)
                add('releases', node.get('target_release'), source)
            if category == 'releases':
                add('images', node.get('image_id') or node.get('immutable_image'), source)
    return result, invalid


def _record_errors(category, node, record, now):
    errors = []
    if not isinstance(record, dict):
        return ['OWNERSHIP_AND_TERMINAL_RECEIPT_UNAVAILABLE']
    operation = record.get('operation', {})
    if not isinstance(operation, dict):
        operation = {}
    operation_id = operation.get('id')
    legacy_source = (record.get('source_format') in (None, 'v1-file') and
                     record.get('source_path') == EVIDENCE_ROOT + '/operations/' + str(operation_id) + '.json')
    indexed_source = (record.get('source_format') == 'indexed-v2' and
                      record.get('source_path') == EVIDENCE_ROOT + '/v2/index.sqlite3' and
                      record.get('source_operation_id') == operation_id and
                      record.get('source_index_verified') is True and
                      record.get('source_classification') in ('A', 'B'))
    if (not _matches(OPERATION, operation_id) or not (legacy_source or indexed_source) or
            any(record.get(key) is not True for key in
                ('source_verified', 'source_no_symlinks', 'source_stable', 'source_checksum_verified'))):
        errors.append('OPERATION_RECEIPT_PROVENANCE_UNVERIFIED')
    try:
        bound = record.get('object_sha256') == object_digest(node)
    except (ValueError, TypeError):
        bound = False
    if not bound or record.get('category') != category or record.get('id') != node.get('id'):
        errors.append('OPERATION_OBJECT_IDENTITY_MISMATCH')
    if (operation.get('ownership_verified') is not True or
            not _matches(UUID, operation.get('boot_id')) or
            not _positive(operation.get('owner_pid')) or not _positive(operation.get('owner_start_ticks')) or
            not _matches(SHA, operation.get('target_sha')) or
            (category == 'staging' and operation.get('target_sha') != node.get('operation_sha')) or
            (category == 'releases' and operation.get('target_sha') != node.get('id')) or
            (category == 'backups' and operation.get('target_sha') != node.get('target_release'))):
        errors.append('OPERATION_OWNER_IDENTITY_UNVERIFIED')
    if (operation.get('state') not in ('succeeded', 'failed', 'cancelled') or
            operation.get('terminal_receipt_verified') is not True or
            operation.get('owner_process_state') != 'terminated_identity_verified' or
            operation.get('lease_state') != 'terminal_verified'):
        errors.append('OPERATION_TERMINATION_UNPROVEN')
    created, completed = operation.get('created_at'), operation.get('completed_at')
    if (not _time(created) or not _time(completed) or not _time(now) or
            not created <= completed <= now):
        errors.append('LIFECYCLE_TIMESTAMPS_INVALID')
    elif now - completed < PRESERVATION_SECONDS[category]:
        errors.append('PRESERVATION_PERIOD_NOT_ELAPSED')
    if (record.get('object_identity_stable') is not True or record.get('no_symlink_escape') is not True or
            record.get('no_mount_crossing') is not True or record.get('content_inventory_verified') is not True or
            not _matches(HEX, record.get('content_sha256'))):
        errors.append('OBJECT_CONTENT_OR_PATH_IDENTITY_UNVERIFIED')
    if record.get('open_paths') != [] or record.get('active_processes') != []:
        errors.append('PROCESS_REFERENCE_ABSENCE_UNPROVEN')
    recovery = record.get('recovery', {})
    if not isinstance(recovery, dict):
        recovery = {}
    if (recovery.get('unique_data') is not False or recovery.get('evidence_preserved') is not True or
            recovery.get('incident_review_complete') is not True or
            recovery.get('disposition_validation') not in ('independent_restore_rehearsal', 'reproducible_artifact_verified') or
            not _matches(HEX, recovery.get('proof_sha256'))):
        errors.append('UNIQUE_RECOVERY_DATA_OR_INCIDENT_EVIDENCE_UNRESOLVED')
    # READY is mandatory for completed backups, never sufficient. Partial backup
    # staging needs explicit inventory + durable evidence rather than fake READY.
    completed_backup = category == 'backups' or (category == 'staging' and '/.backup-' in node.get('path', '') and node.get('ready') is True)
    if category == 'staging' and '/.backup-' in node.get('path', '') and type(node.get('ready')) is not bool:
        errors.append('BACKUP_COMPLETION_OBSERVATION_UNAVAILABLE')
    if category == 'backups' and node.get('ready') is not True:
        errors.append('COMPLETE_BACKUP_READY_NOT_VERIFIED')
    validation = record.get('backup_validation', {})
    if not isinstance(validation, dict):
        validation = {}
    if completed_backup and any(validation.get(key) is not True for key in (
            'ready', 'checksum_manifest', 'checksums_match', 'pg_restore_list',
            'tar_complete', 'previous_reference', 'rollback_contract')):
        errors.append('COMPLETE_BACKUP_RESTORE_VALIDATION_REQUIRED')
    if category in ('backups', 'releases') and record.get('format_validation') not in ('legacy_verified', 'current_verified', 'v2_verified'):
        errors.append('LEGACY_OR_CURRENT_FORMAT_NOT_VERIFIED')
    if category == 'images' and record.get('image_identity_verified') is not True:
        errors.append('IMMUTABLE_IMAGE_IDENTITY_UNVERIFIED')
    return errors


def build_cleanup_plan(snapshot, retention_plan):
    """Return additive, fail-closed eligibility for one immutable observation."""
    if not isinstance(snapshot, dict) or not isinstance(retention_plan, dict):
        raise ValueError('snapshot and retention plan must be objects')
    evidence = snapshot.get('cleanup_evidence')
    global_errors = _global_errors(snapshot, evidence, retention_plan)
    references, invalid_references = _reverse_references(snapshot, retention_plan)
    if invalid_references:
        global_errors.append('MALFORMED_ADDITIONAL_REFERENCE_LIST')
    records, duplicates = {}, set()
    raw_records = evidence.get('records') if isinstance(evidence, dict) else None
    if isinstance(raw_records, list):
        for record in raw_records:
            if not isinstance(record, dict) or record.get('category') not in CATEGORIES or not isinstance(record.get('id'), str):
                global_errors.append('MALFORMED_OPERATION_RECORD')
                continue
            key = (record['category'], record['id'])
            if key in records:
                duplicates.add(key)
            records[key] = record
    elif evidence is not None:
        global_errors.append('OPERATION_RECORD_INVENTORY_UNAVAILABLE')
    raw_nodes = {}
    for category in CATEGORIES:
        for node in _inventory(snapshot, category):
            if isinstance(node, dict) and isinstance(node.get('id'), str):
                key = (category, node['id'])
                if key in raw_nodes and raw_nodes[key] != node:
                    global_errors.append('CONFLICTING_OBJECT_IDENTITY')
                raw_nodes[key] = node
    now = snapshot.get('captured_end_at', snapshot.get('collected_at'))
    output, summary = {}, {}
    for category in CATEGORIES:
        output[category] = []
        summary[category] = {name: {'count': 0, 'object_bytes': 0} for name in CLASSIFICATIONS}
        for retained in retention_plan.get('nodes', {}).get(category, []):
            key = (category, retained['id'])
            node = raw_nodes.get(key, {})
            record = records.get(key)
            reasons = list(global_errors)
            active = (retained.get('classification') == 'ACTIVE' or node.get('active') is True or
                      node.get('possible_active') is True or node.get('ownership') == 'possible_active')
            if isinstance(record, dict):
                operation = record.get('operation')
                if isinstance(operation, dict) and (operation.get('state') in ('running', 'pending') or
                        operation.get('owner_process_state') == 'alive' or operation.get('lease_state') == 'active'):
                    active = True
                if record.get('open_paths') or record.get('active_processes'):
                    active = True
                observation = snapshot.get('operation_observation')
                active_shas = observation.get('operation_shas') if isinstance(observation, dict) else None
                if isinstance(operation, dict) and _strings(active_shas) and operation.get('target_sha') in active_shas:
                    active = True
            if not _target_valid(category, node):
                reasons.append('TARGET_OUTSIDE_FIXED_PROTOCOL_SCOPE')
            if key in duplicates:
                reasons.append('DUPLICATE_OPERATION_RECEIPTS')
            if retained.get('classification') in ('KEEP_REQUIRED', 'KEEP_POLICY'):
                reasons.append('RETENTION_REQUIRES_OBJECT')
            incoming = sorted(references.get(category + ':' + retained['id'], set()))
            if incoming or retained.get('retained_by'):
                reasons.append('CURRENT_ROLLBACK_BACKUP_CONTAINER_OR_OPERATION_REFERENCE')
            if node.get('references_complete') is not True or node.get('unknown_reference_kinds'):
                reasons.append('OBJECT_REFERENCES_UNVERIFIED')
            if category != 'staging' and (node.get('managed') is not True or node.get('validation') not in ('verified', 'legacy')):
                reasons.append('OBJECT_FORMAT_OR_MANAGEMENT_UNVERIFIED')
            value = node.get('size_bytes') if category == 'images' else node.get('allocated_bytes')
            size = value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
            if size is None or node.get('size_complete') is not True:
                reasons.append('OBJECT_SIZE_OBSERVATION_INCOMPLETE')
            reasons.extend(_record_errors(category, node, record, now))
            if active:
                reasons.append('ACTIVE_OR_POSSIBLY_ACTIVE')
            state = 'ACTIVE' if active else 'NEEDS_REVIEW' if reasons else 'SAFE_TO_CLEAN'
            if not reasons:
                reasons = ['ALL_REQUIRED_EVIDENCE_VERIFIED_FOR_PLAN_ONLY']
            output[category].append(dict(id=retained['id'], path=node.get('path'),
                classification=state, retention_classification=retained.get('classification'),
                reasons=sorted(set(reasons)), referenced_by=incoming, object_bytes=size))
            summary[category][state]['count'] += 1
            if size is not None:
                summary[category][state]['object_bytes'] += size
    return dict(schema_version=1, read_only=True, plan_only=True, execution_authorized=False,
                evidence_root=EVIDENCE_ROOT, preservation_seconds=dict(PRESERVATION_SECONDS),
                future_execution_requirement='acquire_existing_deployment_flock_and_recollect_all_evidence',
                byte_semantics='object_sizes_only_not_confirmed_physical_recovery',
                nodes=output, summary=summary, warnings=sorted(set(global_errors)))
