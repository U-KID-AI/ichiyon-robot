"""Apply a small, fingerprinted private asset delta to a managed pack stage.

The managed compiler remains the source of DB assets. This overlay owns only
listed new files or named registry entries, never whole non-JUNKET packs. A
different value at an owned key is a conflict, not permission to undo a future
catalog edit. All inputs and conflicts are checked before any stage mutation.
The existing release transaction supplies locking, backup and rollback.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import shutil

MAX_OPERATIONS = 2048
MAX_PAYLOAD_BYTES = 64 * 1024 * 1024
CATALOG = 'behavior_packs/import_structures/scripts/molcar_records_catalog.js'
MAIN = 'behavior_packs/import_structures/scripts/main.js'
KINDS = {'file', 'json_keys', 'lang', 'js_catalog', 'js_imports'}
HASH = re.compile(r'[0-9a-f]{64}')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def _path(root, relative):
    if __package__:
        from .minecraft_cosmetics_apply import pack_path, safe_path
    else:
        from minecraft_cosmetics_apply import pack_path, safe_path
    if not isinstance(relative, str):
        raise ValueError('Static overlay path must be a string')
    parts = relative.split('/')
    if len(parts) < 3 or any(not part or part.endswith(('.', ' ')) for part in parts):
        raise ValueError('Invalid static overlay path')
    pack = pack_path('/'.join(parts[:2]))
    # JUNKET is owned by the existing native full-pack overlay. Do not create
    # another path which could bypass its fixed identity/hash checks.
    if pack in ('behavior_packs/ichiyon_junket_bp', 'resource_packs/ichiyon_junket_rp'):
        raise ValueError('JUNKET packs cannot be patched by a static delta')
    if relative.endswith('/manifest.json') or relative == 'behavior_packs/import_structures/scripts/cosmetics_catalog.js':
        raise ValueError('Static delta cannot replace pack identity or the DB catalog')
    return safe_path(Path(root), relative)


def _guard(current, exists, change, label):
    if 'value' not in change:
        raise ValueError('Static registry change needs a value: ' + label)
    if exists and current == change['value']:
        return False
    if ('before' in change) != exists or (exists and current != change['before']):
        raise ValueError('Static overlay conflicts with managed content: ' + label)
    return True


def _changes(operation):
    changes = operation.get('changes')
    if not isinstance(changes, dict) or not changes:
        raise ValueError('Static registry delta needs named changes')
    if any(not isinstance(key, str) or not key or not isinstance(value, dict)
           for key, value in changes.items()):
        raise ValueError('Invalid named static registry change')
    return changes


def _json_keys(data, operation):
    if data is None:
        # Creating a registry requires explicit format metadata, never a copy of
        # the author's full registry with unrelated entries.
        document = deepcopy(operation.get('initial', {}))
    else:
        document = json.loads(data.decode('utf-8-sig'))
    path = operation.get('object_path')
    if not isinstance(path, list) or any(not isinstance(part, str) or not part for part in path):
        raise ValueError('Invalid JSON registry object path')
    target = document
    for part in path:
        if not isinstance(target, dict):
            raise ValueError('Static registry parent is not an object')
        target = target.setdefault(part, {})
    if not isinstance(target, dict):
        raise ValueError('Static registry target is not an object')
    changed = data is None
    for key, change in _changes(operation).items():
        if _guard(target.get(key), key in target, change, operation['path'] + ':' + key):
            target[key] = deepcopy(change['value'])
            changed = True
    return _json_bytes(document) if changed else data


def _lang(data, operation):
    if not operation['path'].endswith('.lang'):
        raise ValueError('Language delta must target a .lang file')
    text = data.decode('utf-8-sig') if data is not None else ''
    lines = text.splitlines(keepends=True)
    entries = {}
    for index, line in enumerate(lines):
        if line.lstrip().startswith('##') or '=' not in line:
            continue
        key, value = line.rstrip('\r\n').split('=', 1)
        if key in entries:
            raise ValueError('Duplicate language key: ' + key)
        entries[key] = (index, value)
    changed = data is None
    for key, change in _changes(operation).items():
        value = change.get('value')
        if (not isinstance(value, str) or any(c in key + value for c in '\r\n') or '=' in key):
            raise ValueError('Invalid language entry')
        existing = entries.get(key)
        if _guard(existing[1] if existing else None, existing is not None,
                  change, operation['path'] + ':' + key):
            line = key + '=' + value + '\n'
            if existing:
                lines[existing[0]] = line
            else:
                if lines and not lines[-1].endswith(('\n', '\r')):
                    lines[-1] += '\n'
                lines.append(line)
            changed = True
    return ''.join(lines).encode('utf-8') if changed else data


def _catalog(data, operation):
    if operation['path'] != CATALOG or data is None:
        raise ValueError('Only the existing Molcar record catalog supports keyed JS updates')
    text = data.decode('utf-8-sig')
    match = re.search(r'export\s+const\s+molcarRecords\s*=\s*(\[.*\])\s*;', text, re.S)
    if not match or text[:match.start()].strip().replace('// Generated from supplied assets by scripts/build_molcar_record.py.', '').strip():
        # Other harmless generated headers are accepted below; executable code
        # is not interpreted or evaluated by this data-only loader.
        prefix = text[:match.start()] if match else ''
        if not match or any(line.strip() and not line.lstrip().startswith('//') for line in prefix.splitlines()):
            raise ValueError('Unsupported Molcar catalog source')
    if text[match.end():].strip():
        raise ValueError('Unexpected code after Molcar catalog')
    rows = json.loads(match.group(1))
    if not isinstance(rows, list) or any(not isinstance(row, dict) or not isinstance(row.get('itemId'), str) for row in rows):
        raise ValueError('Invalid Molcar catalog rows')
    indices = {row['itemId']: index for index, row in enumerate(rows)}
    if len(indices) != len(rows):
        raise ValueError('Duplicate Molcar catalog item')
    changed = False
    for key, change in _changes(operation).items():
        value = change.get('value')
        if not isinstance(value, dict) or value.get('itemId') != key:
            raise ValueError('Molcar catalog key differs from itemId')
        exists = key in indices
        if _guard(rows[indices[key]] if exists else None, exists, change, key):
            if exists:
                rows[indices[key]] = deepcopy(value)
            else:
                indices[key] = len(rows)
                rows.append(deepcopy(value))
            changed = True
    sounds = [row.get('soundId') for row in rows]
    if any(not isinstance(sound, str) or not sound for sound in sounds) or len(set(sounds)) != len(sounds):
        raise ValueError('Missing or duplicate Molcar sound ID')
    if not changed:
        return data
    return (text[:match.start(1)] + json.dumps(rows, ensure_ascii=False, indent=2)
            + text[match.end(1):]).encode('utf-8')


def _imports(data, operation):
    if operation['path'] != MAIN or data is None:
        raise ValueError('Static imports must target the existing import_structures main.js')
    imports = operation.get('imports')
    if (not isinstance(imports, list) or not imports or len(set(imports)) != len(imports)
            or any(not isinstance(path, str) or not re.fullmatch(r'\./[a-z0-9_]+\.js', path) for path in imports)):
        raise ValueError('Invalid static side-effect import')
    text = data.decode('utf-8-sig')
    added = []
    for path in imports:
        # The same module may already have a named import. In that case its
        # side effects already run, so no duplicate import is needed.
        pattern = r'(?m)^[ \t]*import\s+(?:[^;\n]*?\bfrom\s+)?[\"\']' + re.escape(path) + r'[\"\']\s*;?'
        matches = re.findall(pattern, text)
        if len(matches) > 1:
            raise ValueError('Duplicate existing import: ' + path)
        if not matches:
            added.append('import "' + path + '";\n')
    return (''.join(added) + text).encode('utf-8') if added else data


def _reviewed_hashes(operation):
    values = operation.get('reviewed_before_sha256', [])
    if (not isinstance(values, list) or len(values) > 64
            or any(not isinstance(value, str) or not HASH.fullmatch(value) for value in values)
            or len(set(values)) != len(values)):
        raise ValueError('Invalid reviewed static file history')
    return values


def _revision(manifest):
    """Explicit file-only allowlist; never authorizes registry or import edits."""
    revision = manifest.get('owned_file_revision')
    if revision is None:
        return None
    if not isinstance(revision, dict) or set(revision) != {'previous_manifest_sha256', 'files'}:
        raise ValueError('Invalid owned-file revision approval')
    previous, files = revision['previous_manifest_sha256'], revision['files']
    if (not isinstance(previous, str) or not HASH.fullmatch(previous)
            or not isinstance(files, dict) or not files or len(files) > 64):
        raise ValueError('Invalid owned-file revision fingerprint or allowlist')
    by_path = {operation['path']: operation for operation in manifest['operations']}
    for path, expected in files.items():
        operation = by_path.get(path)
        if (operation is None or operation['kind'] != 'file'
                or not isinstance(expected, str) or not HASH.fullmatch(expected)
                or expected not in _reviewed_hashes(operation)):
            raise ValueError('Owned-file revision must name an existing file and reviewed old hash: ' + str(path))
    return revision


def load_overlay(source, expected_manifest_sha256):
    """Verify the complete private payload without reading or changing live data."""
    if __package__:
        from .minecraft_cosmetics_apply import MAX_FILE, check_tree, safe_path
    else:
        from minecraft_cosmetics_apply import MAX_FILE, check_tree, safe_path
    source = Path(source)
    if not isinstance(expected_manifest_sha256, str) or not HASH.fullmatch(expected_manifest_sha256):
        raise ValueError('Invalid static overlay manifest fingerprint')
    manifest_path = safe_path(source, 'manifest.json')
    raw = manifest_path.read_bytes()
    if digest(raw) != expected_manifest_sha256:
        raise ValueError('Static overlay manifest fingerprint mismatch')
    check_tree(source)
    manifest = json.loads(raw.decode('utf-8-sig'))
    operations = manifest.get('operations')
    if (manifest.get('schema') != 1 or not isinstance(operations, list)
            or not operations or len(operations) > MAX_OPERATIONS):
        raise ValueError('Invalid static overlay manifest')
    paths, payloads, total = set(), {}, len(raw)
    for operation in operations:
        if not isinstance(operation, dict) or operation.get('kind') not in KINDS:
            raise ValueError('Unsupported static overlay operation')
        relative = operation.get('path')
        _path(source, relative)
        if relative.casefold() in paths:
            raise ValueError('A static overlay path must have exactly one operation')
        paths.add(relative.casefold())
        if operation['kind'] == 'file':
            _reviewed_hashes(operation)
            expected = operation.get('sha256')
            before = operation.get('before_sha256')
            if not isinstance(expected, str) or not HASH.fullmatch(expected) or (before is not None and (not isinstance(before, str) or not HASH.fullmatch(before))):
                raise ValueError('Invalid static file fingerprint')
            data = _path(source / 'files', relative).read_bytes()
            if len(data) > MAX_FILE or digest(data) != expected:
                raise ValueError('Static overlay file fingerprint or size mismatch: ' + relative)
            payloads[relative] = data
            total += len(data)
        elif operation['kind'] in ('json_keys', 'lang', 'js_catalog'):
            _changes(operation)
    if total > MAX_PAYLOAD_BYTES:
        raise ValueError('Static overlay payload is too large')
    revision = _revision(manifest)
    if revision is None and any(_reviewed_hashes(operation) for operation in operations if operation['kind'] == 'file'):
        raise ValueError('Reviewed static file history requires a revision approval')
    return manifest, payloads


def plan_overlay(source, stage, expected_manifest_sha256):
    """Return exact prospective bytes; conflicts leave the stage byte-identical."""
    if __package__:
        from .minecraft_cosmetics_apply import MAX_FILE, pack_layout, safe_path
    else:
        from minecraft_cosmetics_apply import MAX_FILE, pack_layout, safe_path
    manifest, payloads = load_overlay(source, expected_manifest_sha256)
    _, packs, _ = pack_layout(Path(stage))
    planned, proof = {}, []
    for operation in manifest['operations']:
        relative, kind = operation['path'], operation['kind']
        if '/'.join(relative.split('/')[:2]) not in packs:
            raise ValueError('Static overlay requires an existing managed pack: ' + relative)
        target = _path(stage, relative)
        if target.exists() and not target.is_file():
            raise ValueError('Static overlay target is not a regular file: ' + relative)
        before = target.read_bytes() if target.exists() else None
        if before is not None and len(before) > MAX_FILE:
            raise ValueError('Static overlay target is too large')
        if kind == 'file':
            after = payloads[relative]
            actual = digest(before) if before is not None else None
            accepted_before = [operation.get('before_sha256'), *_reviewed_hashes(operation)]
            if before != after and actual not in accepted_before:
                raise ValueError('Static overlay conflicts with managed file: ' + relative)
        elif kind == 'json_keys':
            if not relative.endswith('.json'):
                raise ValueError('JSON registry delta must target JSON')
            after = _json_keys(before, operation)
        elif kind == 'lang':
            after = _lang(before, operation)
        elif kind == 'js_catalog':
            after = _catalog(before, operation)
        else:
            after = _imports(before, operation)
        if len(after) > MAX_FILE:
            raise ValueError('Merged static file is too large')
        planned[relative] = after
        proof.append({'path': relative, 'kind': kind,
                      'before_sha256': digest(before) if before is not None else None,
                      'after_sha256': digest(after), 'changed': before != after})
    for operation in manifest['operations']:
        if operation['kind'] == 'js_imports':
            for module in operation['imports']:
                relative = MAIN.rsplit('/', 1)[0] + '/' + module[2:]
                if relative not in planned and not safe_path(Path(stage), relative).is_file():
                    raise ValueError('Static import module is missing: ' + module)
    result = {'schema': 1, 'manifest_sha256': expected_manifest_sha256, 'files': proof}
    if 'owned_file_revision' in manifest:
        result['owned_file_revision'] = deepcopy(manifest['owned_file_revision'])
    return planned, result


def apply_overlay(source, stage, expected_manifest_sha256):
    """Apply only a preflighted delta to a disposable existing release stage."""
    if __package__:
        from .minecraft_cosmetics_apply import atomic_json, content_hash, pack_layout
    else:
        from minecraft_cosmetics_apply import atomic_json, content_hash, pack_layout
    stage = Path(stage)
    planned, result = plan_overlay(source, stage, expected_manifest_sha256)
    for relative, data in planned.items():
        target = _path(stage, relative)
        if target.exists() and target.read_bytes() == data:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    proof, _, _ = pack_layout(stage)
    for entry in proof.get('packs', []):
        if isinstance(entry, dict) and 'content_hash' in entry and any(path.startswith(entry['path'] + '/') for path in planned):
            entry['content_hash'] = content_hash(stage / entry['path'])
    proof['native_static_overlay'] = result
    atomic_json(stage / 'cosmetics-build.json', proof)
    return result


def pinned_source(project, operation, pin):
    """Fixed native operation path; marker data never selects a filesystem path."""
    if __package__:
        from .minecraft_cosmetics_apply import safe_path, valid_uuid
    else:
        from minecraft_cosmetics_apply import safe_path, valid_uuid
    if not isinstance(pin, dict) or set(pin) != {'manifest_sha256'}:
        raise ValueError('Invalid pinned static overlay')
    operation = valid_uuid(operation)
    source = safe_path(Path(project), 'junket-world-applications/' + operation + '/static-overlay')
    load_overlay(source, pin['manifest_sha256'])
    return source


def _require_retained(previous, current, previous_manifest_sha256=None):
    """A later native release cannot silently drop older private additions."""
    by_path = {operation['path']: operation for operation in current['operations']}
    old_paths = {operation['path']: operation for operation in previous['operations']}
    revision = _revision(current)
    # An unchanged approval is historical metadata, not a new authorization.
    approved = {}
    if revision is not None and revision != previous.get('owned_file_revision'):
        if revision['previous_manifest_sha256'] != previous_manifest_sha256:
            raise ValueError('Owned-file revision does not match the previous pinned manifest')
        if set(old_paths) != set(by_path):
            raise ValueError('Owned-file revision must retain exactly the previous operation paths')
        approved = revision['files']
        for path, expected in approved.items():
            old, new = old_paths[path], by_path[path]
            if old['kind'] != 'file' or old['sha256'] != expected:
                raise ValueError('Owned-file revision old payload fingerprint mismatch: ' + path)
            if new['sha256'] == old['sha256']:
                raise ValueError('Owned-file revision must change each approved payload: ' + path)
            allowed = deepcopy(old)
            allowed['sha256'] = new['sha256']
            allowed['reviewed_before_sha256'] = list(dict.fromkeys([*_reviewed_hashes(old), expected]))
            if new != allowed:
                raise ValueError('Owned-file revision changed guards or unapproved history: ' + path)
    for old in previous['operations']:
        new = by_path.get(old['path'])
        if new is None or new['kind'] != old['kind']:
            raise ValueError('New static overlay drops an existing owned path: ' + old['path'])
        if old['path'] in approved:
            continue
        if approved and new != old:
            raise ValueError('Owned-file revision changed an unapproved operation: ' + old['path'])
        if old['kind'] in ('json_keys', 'lang', 'js_catalog'):
            if old.get('object_path') != new.get('object_path') or any(new['changes'].get(key) != value for key, value in old['changes'].items()):
                raise ValueError('New static overlay must retain previous owned keys: ' + old['path'])
        elif old['kind'] == 'js_imports':
            if not set(old['imports']) <= set(new['imports']):
                raise ValueError('New static overlay drops an existing import')
        elif old != new:
            raise ValueError('Existing static file needs an explicit reviewed revision: ' + old['path'])
    return approved


def prepare_native_overlay(project, bundle, directory, stage):
    """Copy a new or inherited delta into this native operation, then apply it.

    Called only while preparing the existing release transaction, before pack
    versions are prepared. Without an incoming delta the current private pin is
    inherited byte-for-byte, so a later JUNKET-only fix cannot erase records.
    """
    if __package__:
        from .minecraft_cosmetics_apply import read_json, safe_path
    else:
        from minecraft_cosmetics_apply import read_json, safe_path
    project, bundle, directory = Path(project), Path(bundle), Path(directory)
    prior, prior_manifest = None, None
    marker_path = safe_path(project, 'junket-native-overlay.json')
    if marker_path.exists():
        marker = read_json(marker_path)
        if __package__:
            from .minecraft_junket_overlay import validate_marker_schema
        else:
            from minecraft_junket_overlay import validate_marker_schema
        validate_marker_schema(marker)
        if 'static_overlay' in marker:
            prior = pinned_source(project, marker['operation_id'], marker['static_overlay'])
            prior_manifest, _ = load_overlay(prior, marker['static_overlay']['manifest_sha256'])
    incoming = safe_path(bundle, 'static-overlay')
    if incoming.exists():
        expected = digest(safe_path(incoming, 'manifest.json').read_bytes())
        manifest, _ = load_overlay(incoming, expected)
        if prior_manifest is not None:
            _require_retained(prior_manifest, manifest, marker['static_overlay']['manifest_sha256'])
            # The old private payload must exist and match its pin (loaded
            # above). A regenerated managed stage may legitimately omit a file
            # originally added by that overlay. Preserve its original before
            # guard and let plan_overlay accept only that base, reviewed old
            # payloads, or the new payload. Unknown current bytes still fail.
        elif manifest.get('owned_file_revision') is not None:
            raise ValueError('Owned-file revision requires a previous pinned overlay')
        source = incoming
    elif prior is not None:
        source, expected = prior, marker['static_overlay']['manifest_sha256']
    else:
        return None, None
    destination = safe_path(directory, 'static-overlay')
    shutil.copytree(source, destination)
    load_overlay(destination, expected)
    result = apply_overlay(destination, stage, expected)
    return {'manifest_sha256': expected}, result


def _manifest_semantics(path):
    if __package__:
        from .minecraft_cosmetics_apply import read_json
    else:
        from minecraft_cosmetics_apply import read_json
    value = read_json(path)
    value['header'].pop('version', None)
    for module in value['modules']:
        module.pop('version', None)
    for dependency in value.get('dependencies', []):
        if 'uuid' in dependency and 'module_name' not in dependency:
            dependency.pop('version', None)
    return value


def verify_native_stage(stage, live, baseline, result):
    """Prove every non-JUNKET change is one declared file, preserving DB assets."""
    if __package__:
        from .minecraft_cosmetics_apply import check_tree, content_hash, pack_layout
    else:
        from minecraft_cosmetics_apply import check_tree, content_hash, pack_layout
    stage, live = Path(stage), Path(live)
    _, packs, _ = pack_layout(stage)
    junket = {'behavior_packs/ichiyon_junket_bp', 'resource_packs/ichiyon_junket_rp'}
    if set(packs) - junket != set(baseline) - junket:
        raise ValueError('Static overlay cannot add or remove a managed pack')
    allowed = {row['path']: row for row in (result or {}).get('files', [])}
    protected, changed = {}, {}
    for pack in sorted(set(packs) - junket):
        actual = content_hash(stage / pack)
        if not any(path.startswith(pack + '/') for path in allowed):
            if actual != baseline[pack]:
                raise ValueError('Unrelated managed pack would change: ' + pack)
            protected[pack] = actual
            continue
        check_tree(live / pack)
        if _manifest_semantics(stage / pack / 'manifest.json') != _manifest_semantics(live / pack / 'manifest.json'):
            raise ValueError('Static overlay changed a pack identity or dependency: ' + pack)
        before_files = {p.relative_to(live).as_posix(): p for p in (live / pack).rglob('*') if p.is_file() and p != live / pack / 'manifest.json'}
        after_files = {p.relative_to(stage).as_posix(): p for p in (stage / pack).rglob('*') if p.is_file() and p != stage / pack / 'manifest.json'}
        for path in before_files.keys() | after_files.keys():
            before = digest(before_files[path].read_bytes()) if path in before_files else None
            after = digest(after_files[path].read_bytes()) if path in after_files else None
            if path in allowed:
                if before != allowed[path]['before_sha256'] or after != allowed[path]['after_sha256']:
                    raise ValueError('Static overlay proof differs from actual bytes: ' + path)
            elif before != after:
                raise ValueError('Undeclared managed file would change: ' + path)
        if any(path not in after_files for path in allowed if path.startswith(pack + '/')):
            raise ValueError('Static overlay file is missing after preparation')
        changed[pack] = {'before_content_hash': baseline[pack], 'after_content_hash': actual}
    return {'unchanged_managed_packs': protected, 'static_overlay_packs': changed}
