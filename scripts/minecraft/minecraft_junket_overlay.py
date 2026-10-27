"""Preserve the explicitly pinned private JUNKET packs during catalog releases.

This helper never changes the installed world or installed packs. Its only
write target is the existing managed application's stage directory. Source
packs are restricted to a completed native operation's fixed stage directory.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
import shutil
import uuid

JUNKET_PACKS = ('behavior_packs/ichiyon_junket_bp', 'resource_packs/ichiyon_junket_rp')
TERMINAL = {'succeeded', 'failed', 'recovery_failed'}


def validate_marker_schema(marker):
    """Schema 2 makes old two-pack-only helpers reject private static pins."""
    schema = marker.get('schema')
    if type(schema) is not int or schema not in (1, 2):
        raise ValueError('Unsupported pinned JUNKET overlay schema')
    if schema == 2 and 'static_overlay' not in marker:
        raise ValueError('Pinned JUNKET overlay schema 2 requires static_overlay')
    if schema == 1 and 'static_overlay' in marker:
        raise ValueError('Static overlay requires pinned JUNKET overlay schema 2')
    return schema


def preserve_overlay(project: Path, stage: Path | None = None):
    # Lazy import avoids a module import cycle and keeps the unpinned installation
    # independent of this optional helper.
    if __package__:
        from .minecraft_cosmetics_apply import (atomic_json, check_tree, content_hash,
                                               pack_layout, read_json, safe_path, valid_uuid)
    else:
        from minecraft_cosmetics_apply import (atomic_json, check_tree, content_hash,
                                               pack_layout, read_json, safe_path, valid_uuid)

    project = Path(project)
    marker_path = safe_path(project, 'junket-native-overlay.json')
    latest_path = safe_path(project, 'junket-world-applications/latest.json')
    if latest_path.exists():
        latest = read_json(latest_path)
        if latest.get('status') not in TERMINAL:
            raise RuntimeError('JUNKET native world migration is active; catalog apply is deferred')
    if not marker_path.exists():
        return None

    marker = read_json(marker_path)
    schema = validate_marker_schema(marker)
    operation = valid_uuid(marker['operation_id'])
    hashes = marker.get('packs')
    if not isinstance(hashes, dict) or set(hashes) != set(JUNKET_PACKS):
        raise ValueError('Pinned overlay must contain only the two JUNKET packs')
    for expected in hashes.values():
        if not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected):
            raise ValueError('Invalid pinned JUNKET fingerprint')

    sources = {}
    manifests = {}
    for pack in JUNKET_PACKS:
        source = safe_path(project, f'junket-world-applications/{operation}/stage/{pack}')
        if not (source / 'manifest.json').is_file():
            raise ValueError('Pinned JUNKET source is missing: ' + pack)
        check_tree(source)
        if content_hash(source) != hashes[pack]:
            raise ValueError('Pinned JUNKET source fingerprint mismatch: ' + pack)
        sources[pack] = source
        manifests[pack] = read_json(source / 'manifest.json')
    proof_marker = {'schema': schema, 'operation_id': operation, 'packs': dict(hashes)}
    static_source = None
    if 'static_overlay' in marker:
        if __package__:
            from .junket_static_overlay import pinned_source
        else:
            from junket_static_overlay import pinned_source
        static_source = pinned_source(project, operation, marker['static_overlay'])
        proof_marker['static_overlay'] = deepcopy(marker['static_overlay'])
    if stage is None:
        return proof_marker

    stage = Path(stage)
    proof, old_packs, _ = pack_layout(stage)
    # Conflicting future DB/static edits fail before even the pinned JUNKET
    # copies are changed. The static delta never substitutes whole DB packs.
    if static_source is not None:
        if __package__:
            from .junket_static_overlay import plan_overlay
        else:
            from junket_static_overlay import plan_overlay
        plan_overlay(static_source, stage, marker['static_overlay']['manifest_sha256'])
    # Validate every path and both sources before mutating this disposable stage.
    targets = {pack: safe_path(stage, pack) for pack in JUNKET_PACKS}
    for target in targets.values():
        if target.exists():
            check_tree(target)
    proof = deepcopy(proof)
    entries = proof.setdefault('packs', list(old_packs))
    for pack in JUNKET_PACKS:
        found = False
        for entry in entries:
            if (entry.get('path') if isinstance(entry, dict) else entry) == pack:
                found = True
                if isinstance(entry, dict):
                    if 'uuid' in entry:
                        entry['uuid'] = manifests[pack]['header']['uuid']
                    if 'content_hash' in entry:
                        entry['content_hash'] = hashes[pack]
        if not found:
            entries.append({'path': pack, 'uuid': manifests[pack]['header']['uuid']})

    temporary = safe_path(stage, '.junket-overlay-' + uuid.uuid4().hex)
    temporary.mkdir()
    try:
        for pack, source in sources.items():
            shutil.copytree(source, temporary / pack)
            if content_hash(temporary / pack) != hashes[pack]:
                raise ValueError('Pinned JUNKET stage copy fingerprint mismatch: ' + pack)
        for pack, target in targets.items():
            if target.exists():
                shutil.rmtree(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            (temporary / pack).rename(target)
        proof['native_junket_overlay'] = proof_marker
        atomic_json(stage / 'cosmetics-build.json', proof)
        if static_source is not None:
            if __package__:
                from .junket_static_overlay import apply_overlay
            else:
                from junket_static_overlay import apply_overlay
            apply_overlay(static_source, stage, marker['static_overlay']['manifest_sha256'])
        pack_layout(stage)
    finally:
        shutil.rmtree(temporary)
    return proof_marker
