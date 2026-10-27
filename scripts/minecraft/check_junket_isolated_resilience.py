"""Run the existing destructive BDS probe in a network-isolated backup clone.

This host-only command never opens the live world's files or restarts its server.
It requires a succeeded native operation and its hash-verified stopped backup.
The clone and evidence are retained; its exclusively owned container is stopped.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import subprocess
import tarfile
import time
from types import SimpleNamespace
import uuid
from zipfile import ZipFile

import check_junket_native_runtime as checker
from minecraft_cosmetics_apply import PackApplications, atomic_json, check_tree, pack_layout, safe_path
from minecraft_storage import check_capacity, StorageReceipt, write_receipt

PROJECT = Path('/home/ubuntu/minecraft-bedrock-creative')
OPERATIONS = PROJECT / 'junket-world-applications'
ISOLATED = PROJECT / 'junket-isolated-resilience'
WORLD = 'ichiyon-creative-flat'
PRODUCTION = 'minecraft-bedrock-creative'
# Inspected 2026-10-24 after production's 2026-10-22 security-image update.
# Keep the exact immutable image identity; never accept an arbitrary current tag.
IMAGE = 'sha256:d89ce654f3ee2e5417d556a1d4b85b1ec73942bbf47062d1393936d674dc2c97'
BINARY = 'bedrock_server-1.26.52.3'
# This pinned image is arm64; its production bedrock-entry.sh launches the x86
# server through this runner and Box64. Do not execute the x86 ELF directly, or
# invoke the full entry script that performs download/property-management work.
SERVER_RUNNER = '/usr/local/bin/mc-server-runner'
EMULATOR = '/usr/local/bin/box64'
EXECUTABLES = {
    SERVER_RUNNER: {'sha256': 'cf3e4a4b0dede366cc00b525e55dd995f5ad9aceab10446130c46f932936b904', 'elf_machine': 183},
    EMULATOR: {'sha256': 'ffe9d70a15b9139b7336ee4cc162933917d88ee1625725da8331c3b62e7b43ed', 'elf_machine': 183},
    '/data/' + BINARY: {'sha256': '0a490c711d4a2ce075debcd979e8862b7eaba008373f9970f0edbb67f98f9207', 'elf_machine': 62},
}
# Packs come from the stopped, verified backup and stage, never from another
# redundant copy of the running installation. The executable has a read-only
# per-content cache and a separate container file mount.
RUNTIME_FILES = ('definitions', 'minecraftpe', 'treatments',
                 'profanity_filter.wlist', 'packetlimitconfig.json')
LABEL = 'org.ichiyon.junket.isolated-resilience'
MAX_EXPANDED = 4 * 1024**3
MAX_MEMBERS = 250000
JUNKET_PACKS = ('behavior_packs/ichiyon_junket_bp', 'resource_packs/ichiyon_junket_rp')
BUNDLE_METADATA = {'bundle-report.json', 'source-layouts.json', 'warp-plan.json',
                   'junket-catalog.json', 'PRIVATE-NOTICE.txt'}


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def require_plain_path(path):
    for entry in (path, *path.parents):
        if entry.is_symlink() or (hasattr(entry, 'is_junction') and entry.is_junction()):
            raise ValueError('Linked path is not allowed: ' + str(entry))
    return path


@contextmanager
def exclusive_file_lock(path):
    """Serialize preparation without replacing an existing test environment."""
    import fcntl
    require_plain_path(path)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode) or os.fstat(descriptor).st_nlink != 1:
            raise ValueError('Invalid isolated preparation lock')
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('An isolated preparation/test is already active; reuse its result') from exc
        yield
    finally:
        os.close(descriptor)


def binary_cache_path():
    return PROJECT / '.minecraft-storage/runtime' / EXECUTABLES['/data/' + BINARY]['sha256'] / BINARY


def checked_binary(path):
    require_plain_path(path)
    info = path.stat()
    if (not path.is_file() or info.st_nlink != 1 or info.st_mode & 0o222
            or (os.name == 'posix' and stat.S_IMODE(info.st_mode) != 0o555)
            or digest(path) != EXECUTABLES['/data/' + BINARY]['sha256']):
        raise ValueError('Shared BDS executable identity, permissions or hash changed')
    return path


def prepare_shared_binary():
    """Publish one verified executable; never link a writable production inode."""
    target = require_plain_path(binary_cache_path())
    for directory in (PROJECT / '.minecraft-storage', target.parent.parent, target.parent):
        require_plain_path(directory)
        directory.mkdir(mode=0o700, exist_ok=True)
        if not directory.is_dir() or (os.name == 'posix' and directory.stat().st_mode & 0o077):
            raise ValueError('Runtime cache directories must be private (0700)')
    with exclusive_file_lock(target.parent / '.publication.lock'):
        if target.exists():
            return checked_binary(target), 0
        temporary = target.with_name('.incoming-' + uuid.uuid4().hex)
        try:
            docker('cp', PRODUCTION + ':/data/' + BINARY, str(temporary), timeout=300)
            require_plain_path(temporary)
            if not temporary.is_file() or temporary.stat().st_nlink != 1:
                raise ValueError('Copied BDS executable is not an independent regular file')
            if digest(temporary) != EXECUTABLES['/data/' + BINARY]['sha256']:
                raise ValueError('Copied BDS executable differs from the inspected runtime')
            temporary.chmod(0o600)
            with temporary.open('r+b') as source:
                os.fsync(source.fileno())
            temporary.chmod(0o555)
            os.replace(temporary, target)
            return checked_binary(target), target.stat().st_size
        finally:
            # Only this invocation's unpublished temporary is disposable.
            if temporary.exists():
                temporary.chmod(0o600)
                temporary.unlink()


def runtime_resource_fingerprint():
    """Hash only the fixed non-world runtime inputs; detect missing/unreadable files."""
    names = ' '.join(RUNTIME_FILES)
    script = ('set -eu; cd /data; for p in ' + names + '; do test -e "$p"; done; '
              'find ' + names + ' ! -type f ! -type d -print; '
              'find ' + names + ' -type f -exec sha256sum {} +')
    lines = docker('exec', PRODUCTION, 'sh', '-c', script).splitlines()
    if not lines or any(not re.fullmatch(r'[0-9a-f]{64}  [^\r\n]+', line) for line in lines):
        raise ValueError('Runtime resources contain links, special files or incomplete hashes')
    return hashlib.sha256('\n'.join(sorted(lines)).encode()).hexdigest()


def copied_resource_fingerprint(data):
    lines = []
    for name in RUNTIME_FILES:
        root = require_plain_path(data / name)
        check_tree(root) if root.is_dir() else require_plain_path(root)
        for path in sorted(root.rglob('*')) if root.is_dir() else [root]:
            if path.is_file():
                lines.append(digest(path) + '  ' + path.relative_to(data).as_posix())
    return hashlib.sha256('\n'.join(sorted(lines)).encode()).hexdigest()


def reuse_fingerprint(operation_dir, backup, candidate_bundle, candidate_sha256, runtime_contract, adapter):
    # Unknown adapters cannot silently inherit a successful result: they must
    # bind every external input/configuration in their explicit contract.
    if candidate_bundle is not None:
        candidate = require_plain_path(Path(candidate_bundle))
        if (not candidate.is_absolute() or not candidate.is_file()
                or not re.fullmatch(r'[0-9a-f]{64}', candidate_sha256 or '')
                or digest(candidate) != candidate_sha256):
            raise ValueError('Candidate ZIP SHA256 mismatch')
    if adapter is not None and not hasattr(adapter, 'storage_fingerprint'):
        return None
    code = {p.name: digest(p) for p in sorted(Path(__file__).resolve().parent.glob('*.py'))}
    # Bind preparation/replay inputs, not mutable status payloads or capacity
    # receipts (a later status query must not create an unnecessary new clone).
    input_records = ('preflight.json', 'full-backup.json', 'managed-active-before.json',
                     'native-lineage-recovery.json', 'native-extension-proof.json',
                     'native-barrier-proof.json', 'native-proof.json', 'warp-proof.json')
    records = {name: digest(operation_dir / name) for name in input_records if (operation_dir / name).is_file()}
    body = {'schema': 1, 'operation': operation_dir.name, 'backup': backup,
            'candidate_sha256': candidate_sha256, 'runtime': runtime_contract,
            'code': code,
            'operation_records': records, 'stage': tree_digest(operation_dir / 'stage'),
            'adapter': adapter.storage_fingerprint() if adapter is not None else None}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def reusable_result(fingerprint, operation):
    """Reuse evidence only; never overwrite or relaunch a previous world."""
    if fingerprint is None:
        return None
    index = require_plain_path(ISOLATED / '.reuse' / (fingerprint + '.json'))
    if not index.is_file():
        return None
    record = read_json(index)
    name = record.get('directory', '')
    if not re.fullmatch(re.escape(operation) + r'-[0-9a-f]{32}', name):
        raise ValueError('Invalid reusable isolated environment identity')
    directory = require_plain_path(ISOLATED / name)
    proof_path = require_plain_path(directory / 'isolated-resilience-proof.json')
    if not proof_path.is_file() or digest(proof_path) != record.get('proof_sha256'):
        return None  # Retain stale/removed evidence; do not rewrite its index.
    proof = read_json(proof_path)
    if (proof.get('storage_fingerprint') != fingerprint or proof.get('operation_id') != operation
            or proof.get('directory') != str(directory) or proof.get('issues') != []
            or proof.get('isolated_server_resilience_verified') is not True
            or proof.get('production_modified') is not False
            or proof.get('final_stop', {}).get('graceful_stop') is not True):
        return None
    server = IsolatedServer(directory, operation, name.rsplit('-', 1)[1])
    if proof.get('container') != server.name:
        return None
    server.created = True
    info = server.inspect_owned()
    if info['State']['Running'] or info['State'].get('ExitCode') != 0:
        raise ValueError('Reusable isolated environment is active or has a failed exit')
    for key, expected in proof.get('storage_environment', {}).items():
        if key not in ('data', 'stage', 'candidate') or tree_digest(directory / key) != expected:
            return None
    if not {'data', 'stage'} <= set(proof.get('storage_environment', {})):
        return None
    result = deepcopy(proof)
    result['source_storage_receipt'] = result.pop('storage_receipt', None)
    try:
        result['storage_receipt'] = StorageReceipt(directory, operation).finish(
                retained_environments=[str(directory)],
                recovery_sources=[str(proof_path), proof['full_backup']['path']],
                new_environment_created=False, fresh_bds_test_executed=False)
    except Exception as error:
        result['storage_receipt_error'] = type(error).__name__
    result['validation_reuse'] = {'kind': 'identical_completed_isolated_proof',
                                'source_proof': str(proof_path), 'sha256': record['proof_sha256'],
                                'fresh_bds_test_executed': False, 'new_environment_created': False}
    return result


def preparation_required_bytes(archive, operation_dir, candidate_bundle=None):
    """Bound new copies using archive metadata, plus runtime/probe headroom."""
    with tarfile.open(archive, 'r:gz') as source:
        backup_bytes = sum(member.size for member in source.getmembers() if member.isfile())
    if backup_bytes > MAX_EXPANDED:
        raise ValueError('Backup expansion limit exceeded')
    stage_bytes = sum(p.stat().st_size for p in (operation_dir / 'stage').rglob('*') if p.is_file())
    candidate_bytes = 0
    if candidate_bundle is not None:
        with ZipFile(candidate_bundle) as source:
            candidate_bytes = sum(member.file_size for member in source.infolist())
        if candidate_bytes > MAX_EXPANDED:
            raise ValueError('Candidate expansion limit exceeded')
    # Candidate+stage+data may coexist; preserve the 10 GB reserve even while
    # creating them. The headroom covers fixed runtime resources and probe DBs.
    return backup_bytes + 2 * stage_bytes + 2 * candidate_bytes + 768 * 1024**2


def validate_operation(operation, root=OPERATIONS, backups=PROJECT / 'backups'):
    if str(uuid.UUID(operation)) != operation:
        raise ValueError('A canonical operation UUID is required')
    directory = require_plain_path(root / operation)
    release = read_json(directory / 'native-release-proof.json')
    preflight = read_json(directory / 'preflight.json')
    backup = read_json(directory / 'full-backup.json')
    if (release.get('operation_id') != operation or release.get('status') != 'succeeded'
            or release.get('installed') is not True or release.get('world') != WORLD
            or preflight.get('world') != WORLD or release.get('full_backup') != backup):
        raise ValueError('Succeeded fixed-world release and matching backup proof required')
    if backup.get('server_was_stopped') is not True:
        raise ValueError('Backup must have been taken while BDS was stopped')
    if baseline_replay_kind(directory, preflight):
        previous_native_operation(directory)
    archive = require_plain_path(Path(backup['path']))
    if (archive.parent != backups or not re.fullmatch(r'creative-before-restart-\d{8}-\d{6}\.tar\.gz', archive.name)
            or type(backup.get('bytes')) is not int or backup['bytes'] <= 0
            or not re.fullmatch(r'[0-9a-f]{64}', backup.get('sha256', ''))
            or not archive.is_file() or archive.stat().st_size != backup['bytes']
            or digest(archive) != backup['sha256']):
        raise ValueError('Immutable backup path, bytes or SHA256 mismatch')
    stage = require_plain_path(directory / 'stage')
    check_tree(stage)
    _, packs, _ = pack_layout(stage)
    if not {'behavior_packs/ichiyon_junket_bp', 'resource_packs/ichiyon_junket_rp'} <= set(packs):
        raise ValueError('Operation does not contain both JUNKET packs')
    return directory, archive, backup, packs


def baseline_replay_kind(directory, preflight=None):
    """A backup precedes its release; require proof before replaying bounded edits."""
    preflight = preflight or read_json(directory / 'preflight.json')
    extensions = preflight.get('source_reception_corrections', [])
    if any(change.get('structures') for change in extensions):
        proof_path = directory / 'native-extension-proof.json'
        if not proof_path.is_file():
            raise ValueError('This operation added source floors after its backup; verified replay proof required')
        proof = read_json(proof_path)
        if (proof.get('verified') is not True or proof.get('mode') != 'additive_source_upper_floor'
                or proof.get('reception_changes') != extensions
                or proof.get('existing_building_blocks_replaced') != 0):
            raise ValueError('Unverified source extension replay')
        return 'reception'
    if preflight.get('source_barrier_corrections'):
        proof = read_json(directory / 'native-barrier-proof.json')
        if (proof.get('verified') is not True or proof.get('mode') != 'barrier_only_visible_floor_revision'
                or proof.get('source_validation') != preflight['source_barrier_corrections']
                or proof.get('non_barrier_blocks_replaced') != 0):
            raise ValueError('Unverified source barrier replay')
        return 'barriers'
    return None


def previous_native_operation(directory):
    """Use the release's already-recorded lineage recovery when managed metadata omitted it."""
    active = read_json(directory / 'managed-active-before.json')
    marker = active.get('native_world_operation')
    if marker is not None:
        return str(uuid.UUID(marker['operation_id']))
    recovery = read_json(directory / 'native-lineage-recovery.json')
    marker = recovery.get('native_world_operation', {})
    previous = str(uuid.UUID(marker['operation_id']))
    expected = {item['path']:item['content_hash'] for item in active['packs'] if item['path'] in JUNKET_PACKS}
    if (recovery.get('schema') != 1 or recovery.get('managed_operation_id') != active.get('operation_id')
            or set(expected) != set(JUNKET_PACKS) or recovery.get('verified_installed_packs') != expected
            or marker.get('packs') != expected):
        raise ValueError('Recorded native lineage does not match the managed pre-release snapshot')
    previous_dir = require_plain_path(directory.parent / previous)
    fingerprints = recovery.get('verified_files', {})
    for name in ('native-release-proof.json', 'preflight.json'):
        key = 'junket-world-applications/' + previous + '/' + name
        if digest(previous_dir / name) != fingerprints.get(key):
            raise ValueError('Recovered predecessor proof fingerprint mismatch: ' + name)
    release = read_json(previous_dir / 'native-release-proof.json')
    if (release.get('operation_id') != previous or release.get('status') != 'succeeded'
            or release.get('installed') is not True or release.get('world') != WORLD):
        raise ValueError('Recovered predecessor was not successfully installed in the fixed world')
    return previous


def archive_path(name):
    value = name.rstrip('/')
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or '..' in path.parts or '\\' in value
            or ':' in value or str(path) != value):
        raise ValueError('Unsafe backup member: ' + name)
    allowed = ('data/worlds/' + WORLD, 'data/behavior_packs', 'data/resource_packs')
    if not (value in ('docker-compose.yml', 'cosmetics-applications/active.json')
            or any(value == prefix or value.startswith(prefix + '/') for prefix in allowed)):
        raise ValueError('Unexpected backup member: ' + name)
    return path


def extract_backup(archive, destination):
    """Validate the complete archive before writing; never use extractall.

    The standard backup repeats world pack-reference files. Identical repeated
    members are accepted, but conflicting copies and all links are rejected.
    """
    require_plain_path(destination)
    with tarfile.open(archive, 'r:gz') as source:
        members = source.getmembers()
        if len(members) > MAX_MEMBERS or sum(m.size for m in members) > MAX_EXPANDED:
            raise ValueError('Backup expansion limit exceeded')
        seen = {}
        for member in members:
            relative = archive_path(member.name)
            if not member.isdir() and not member.isfile():
                raise ValueError('Non-regular backup member: ' + member.name)
            key = relative.as_posix()
            if key in seen and (member.isdir() != seen[key].isdir()):
                raise ValueError('Conflicting backup member type: ' + key)
            seen[key] = member
        written = {}
        for member in members:
            relative = archive_path(member.name).as_posix()
            target = safe_path(destination, relative)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            stream = source.extractfile(member)
            with stream:
                if relative in written:
                    value = hashlib.sha256()
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        value.update(chunk)
                    if value.hexdigest() != written[relative]:
                        raise ValueError('Conflicting duplicate backup file: ' + relative)
                    continue
                value = hashlib.sha256()
                with target.open('wb') as output:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        value.update(chunk)
                        output.write(chunk)
                written[relative] = value.hexdigest()
        world = destination / 'data/worlds' / WORLD
        if not (world / 'level.dat').is_file() or not (world / 'db/CURRENT').is_file():
            raise ValueError('Backup is missing the Bedrock world')
    return {'files': len(written), 'world': WORLD, 'source': 'stopped_full_backup'}


def tree_digest(path):
    require_plain_path(path)
    if not path.is_dir():
        raise ValueError('Pack tree must be an existing directory')
    check_tree(path)
    value = hashlib.sha256()
    count = 0
    for entry in sorted(p for p in path.rglob('*') if p.is_file()):
        value.update(entry.relative_to(path).as_posix().encode() + b'\0' + bytes.fromhex(digest(entry)))
        count += 1
    return {'sha256': value.hexdigest(), 'files': count}


def extract_candidate(archive, expected_sha256, destination, *, extra_metadata=(), extra_prefixes=()):
    """Unpack a private builder ZIP only after proving every allowed file."""
    require_plain_path(archive)
    require_plain_path(destination)
    if (not archive.is_absolute() or not archive.is_file()
            or not re.fullmatch(r'[0-9a-f]{64}', expected_sha256 or '')
            or digest(archive) != expected_sha256):
        raise ValueError('Candidate ZIP SHA256 mismatch')
    with ZipFile(archive) as source:
        members = source.infolist()
        names = [member.filename for member in members]
        if (len(names) != len(set(names)) or len(names) > MAX_MEMBERS
                or sum(member.file_size for member in members) > MAX_EXPANDED):
            raise ValueError('Candidate duplicate members or expansion limit')
        for member in members:
            name = member.filename
            path = PurePosixPath(name)
            mode = stat.S_IFMT(member.external_attr >> 16)
            if (not name or path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name
                    or str(path) != name or member.is_dir() or mode not in (0, stat.S_IFREG)
                    or member.flag_bits & 1):
                raise ValueError('Unsafe candidate member: ' + name)
            if name not in BUNDLE_METADATA | set(extra_metadata) and not any(name.startswith(prefix) for prefix in
                    [*('packs/' + pack + '/' for pack in JUNKET_PACKS), *extra_prefixes]):
                raise ValueError('Candidate contains a foreign pack or file: ' + name)
        if not BUNDLE_METADATA <= set(names) or source.getinfo('bundle-report.json').file_size > 32 * 1024**2:
            raise ValueError('Candidate builder metadata is incomplete')
        report_bytes = source.read('bundle-report.json')
        report = json.loads(report_bytes.decode('utf-8-sig'))
        files = report.get('files')
        games = report.get('games', [])
        if (report.get('schema') != 1 or report.get('production_applied') is not False
                or report.get('real_two_player_test') is not False or not isinstance(games, list)
                or len(games) != 17 or set(games) != set(checker.EXPECTED_IDS)
                or not isinstance(files, dict) or set(files) != set(names) - {'bundle-report.json'}
                or not re.fullmatch(r'[0-9a-f]{64}', report.get('source_zip_sha256', ''))):
            raise ValueError('Candidate bundle report/file inventory mismatch')
        for member in members:
            if member.filename == 'bundle-report.json':
                continue
            expected = files[member.filename]
            if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
                raise ValueError('Candidate report contains an invalid file SHA256')
            value = hashlib.sha256()
            with source.open(member) as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    value.update(chunk)
            if value.hexdigest() != expected:
                raise ValueError('Candidate file SHA256 mismatch: ' + member.filename)
        for pack in JUNKET_PACKS:
            if 'packs/' + pack + '/manifest.json' not in files:
                raise ValueError('Candidate needs both JUNKET manifests')
        destination.mkdir(mode=0o700)
        for member in members:
            target = safe_path(destination, member.filename)
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open(member) as stream, target.open('xb') as output:
                shutil.copyfileobj(stream, output)
            expected = hashlib.sha256(report_bytes).hexdigest() if member.filename == 'bundle-report.json' else files[member.filename]
            if digest(target) != expected:
                raise ValueError('Candidate changed during extraction')
    if digest(archive) != expected_sha256:
        raise ValueError('Candidate ZIP changed during extraction')
    return {'path': str(archive), 'sha256': expected_sha256,
            'bundle_report_sha256': hashlib.sha256(report_bytes).hexdigest(),
            'source_zip_sha256': report['source_zip_sha256'], 'verified_files': len(members),
            'source_packs': {pack: tree_digest(destination / 'packs' / pack) for pack in JUNKET_PACKS}}


def install_stage(operation_dir, directory, packs, candidate=None, *, stage_mutator=None):
    # Reuse only the standard installer's filesystem method, with a clone-only
    # API. Its production stop/start, locking, versioning and deploy are not used.
    shutil.copytree(operation_dir / 'stage', directory / 'stage')
    if stage_mutator is not None:
        stage_mutator(directory / 'stage')
    expected_packs = {pack: tree_digest(operation_dir / 'stage' / pack) for pack in packs}
    if stage_mutator is not None:
        expected_packs = {pack: tree_digest(directory / 'stage' / pack) for pack in packs}
    if candidate is not None:
        for pack in JUNKET_PACKS:
            target = safe_path(directory / 'stage', pack)
            replacement = safe_path(candidate, 'packs/' + pack)
            check_tree(replacement)
            if (read_json(target / 'manifest.json')['header']['uuid']
                    != read_json(replacement / 'manifest.json')['header']['uuid']):
                raise ValueError('Candidate JUNKET pack identity changed: ' + pack)
            if not target.resolve().is_relative_to((directory / 'stage').resolve()):
                raise ValueError('Candidate target escaped the clone stage')
            shutil.rmtree(target)
            shutil.copytree(replacement, target)
            expected_packs[pack] = tree_digest(replacement)
    application = PackApplications(SimpleNamespace(PROJECT_DIR=directory, DATA_DIR=directory / 'data', WORLD_NAME=WORLD))
    application._install(directory)
    result = {}
    for pack in packs:
        expected = expected_packs[pack]
        if tree_digest(directory / 'stage' / pack) != expected or tree_digest(directory / 'data' / pack) != expected:
            raise ValueError('Staged pack changed during isolated preparation: ' + pack)
        result[pack] = expected
    return result


def install_source_corrections(operation_dir, directory, candidate, archive):
    """Apply the same bounded native migration to the stopped clone before BDS."""
    layout = read_json(candidate / 'source-layouts.json')
    if layout.get('reception_revision') != 1:
        return None
    for runtime in ('.venv-leveldb', '.venv-nbt'):
        matches = list((PROJECT / runtime / 'lib').glob('python*/site-packages'))
        if len(matches) != 1:
            raise ValueError('Native source correction dependency is unavailable')
        sys.path.append(str(matches[0]))
    for game in layout['games']:
        for part in game['structures']:
            path = safe_path(candidate, part['path'])
            if not path.is_file() or digest(path) != part['sha256']:
                raise ValueError('Candidate source structure mismatch')
            part['path'] = str(path)
    from junket_venue_correction import apply_reception_corrections
    from junket_barriers import apply_barrier_revision
    from junket_catalog_world import native_plan, apply_patch
    from junket_structure import ConvertedWorld
    world_path = directory / 'data/worlds' / WORLD
    baseline = read_json(operation_dir / 'source-layouts.json')
    replay_kind = baseline_replay_kind(operation_dir)
    replay = None
    if replay_kind:
        previous_id = previous_native_operation(operation_dir)
        previous = read_json(operation_dir.parent / previous_id / 'source-layouts.json')
        restore = apply_reception_corrections if replay_kind == 'reception' else apply_barrier_revision
        replay = restore(world_path, baseline, previous, directory / 'baseline-source-rollback.json',
                         full_backup=archive, world_is_stopped=True)
        write_json(directory / 'baseline-source-replay-proof.json', replay)
    apply = apply_barrier_revision if layout.get('barrier_revision') == 1 else apply_reception_corrections
    result = apply(world_path, layout, baseline, directory / 'native-extension-rollback.json',
                   full_backup=archive, world_is_stopped=True)
    if replay is not None:
        result['baseline_source_replay'] = {'kind': replay_kind, 'verified': replay['verified'],
                                           'proof': 'baseline-source-replay-proof.json'}
    world = ConvertedWorld(world_path)
    try:
        result['catalog'] = apply_patch(world, native_plan(layout, read_json(candidate / 'junket-catalog.json')),
            directory / 'catalog-rollback.json', command_only=True)
    finally:
        world.db.close()
    write_json(directory / 'native-extension-proof.json', result)
    return result


def docker(*arguments, timeout=120):
    return subprocess.run(['docker', *arguments], text=True, capture_output=True, check=True, timeout=timeout).stdout.strip()


def validate_runtime_contract(image_info, executable_lines):
    """Pin the inspected ARM runner/emulator and unchanged x86 BDS executable."""
    if image_info.split() != ['linux', 'arm64', IMAGE]:
        raise ValueError('Inspected BDS image OS, architecture or digest changed')
    files = {}
    for line in executable_lines.splitlines():
        fields = line.split('|')
        if len(fields) != 3 or fields[0] not in EXECUTABLES or fields[0] in files:
            raise ValueError('Unexpected runtime executable evidence')
        path, sha256, header_hex = fields
        expected = EXECUTABLES[path]
        try:
            header = bytes.fromhex(header_hex)
        except ValueError as exc:
            raise ValueError('Malformed runtime ELF evidence') from exc
        if (sha256 != expected['sha256'] or len(header) != 20 or
                header[:6] != b'\x7fELF\x02\x01' or
                int.from_bytes(header[18:20], 'little') != expected['elf_machine']):
            raise ValueError('Runtime executable digest or ELF architecture changed: ' + path)
        files[path] = dict(expected)
    if set(files) != set(EXECUTABLES):
        raise ValueError('Missing runtime executable evidence')
    return {'image': IMAGE, 'os': 'linux', 'architecture': 'arm64', 'executable_files': files,
            'executable_permission_checked': True, 'launch': [SERVER_RUNNER, EMULATOR, '/data/' + BINARY]}


def inspect_runtime_contract():
    image_info = docker('image', 'inspect', '--format', '{{.Os}} {{.Architecture}} {{.Id}}', IMAGE)
    # Read only these three fixed runtime paths; do not run the production
    # entrypoint, touch world data, download anything, or expose environment.
    script = ('set -eu; for path in ' + ' '.join(EXECUTABLES) + '; do '
              'test -f "$path"; test -x "$path"; printf "%s|" "$path"; '
              'sha256sum "$path" | cut -d " " -f 1 | tr -d "\\n"; printf "|"; '
              'od -An -v -t x1 -N20 "$path" | tr -d " \\n"; printf "\\n"; done')
    return validate_runtime_contract(image_info, docker('exec', PRODUCTION, 'sh', '-c', script))


def run_arguments(name, token, operation, data, image, binary=None):
    if not name.startswith('junket-isolated-') or image != IMAGE:
        raise ValueError('Invalid isolated container identity')
    mounts = ['--mount', 'type=bind,source=' + str(data) + ',target=/data']
    if binary is not None:
        if Path(binary) != binary_cache_path():
            raise ValueError('Shared executable must use the fixed verified cache')
        mounts.extend(['--mount', 'type=bind,source=' + str(binary) + ',target=/data/' + BINARY + ',readonly'])
    return ['run', '--pull', 'never', '-dit', '--name', name,
            '--label', LABEL + '=' + token, '--label', LABEL + '.operation=' + operation,
            '--network', 'none', '--restart', 'no', '--no-healthcheck',
            '--cpus', '1.5', '--memory', '3g', '--user', str(os.getuid()) + ':' + str(os.getgid()),
            '--security-opt', 'no-new-privileges', '--cap-drop', 'ALL',
            *mounts,
            '--workdir', '/data', '--env', 'LD_LIBRARY_PATH=/data',
            '--entrypoint', SERVER_RUNNER, image, EMULATOR, '/data/' + BINARY]


class IsolatedServer:
    def __init__(self, directory, operation, token):
        self.directory = directory
        self.evidence_directory = directory
        self.operation = operation
        self.token = token
        self.name = 'junket-isolated-' + token
        self.created = False

    def binary_path(self):
        marker = require_plain_path(self.directory / 'runtime-binary.json')
        if not marker.exists():
            return require_plain_path(self.directory / 'data' / BINARY)  # Legacy independent clone.
        value = read_json(marker)
        expected = binary_cache_path()
        require_plain_path(expected)
        if (value.get('schema') != 1 or value.get('path') != str(expected)
                or value.get('sha256') != EXECUTABLES['/data/' + BINARY]['sha256']
                or not expected.is_file() or expected.stat().st_nlink != 1
                or expected.stat().st_size != value.get('bytes') or expected.stat().st_mode & 0o222
                or (os.name == 'posix' and stat.S_IMODE(expected.stat().st_mode) != 0o555)):
            raise ValueError('Shared isolated executable reference changed')
        return expected

    def inspect_owned(self):
        if not self.created or self.name == PRODUCTION:
            raise ValueError('No owned isolated container')
        info = json.loads(docker('inspect', self.name))[0]
        labels = info['Config'].get('Labels', {})
        mounts = info.get('Mounts', [])
        data_mounts = [m for m in mounts if m.get('Destination') == '/data']
        binary_mounts = [m for m in mounts if m.get('Destination') == '/data/' + BINARY]
        shared_binary = (self.directory / 'runtime-binary.json').exists()
        if shared_binary:
            expected_binary = self.binary_path()
            if (len(binary_mounts) != 1 or binary_mounts[0].get('Type') != 'bind'
                    or binary_mounts[0].get('Source') != str(expected_binary) or binary_mounts[0].get('RW') is not False):
                raise ValueError('Isolated shared executable mount is not read-only or changed')
        elif binary_mounts:
            raise ValueError('Legacy isolated container has an unexpected binary mount')
        if (info['Name'] != '/' + self.name or info['Image'] != IMAGE
                or labels.get(LABEL) != self.token or labels.get(LABEL + '.operation') != self.operation
                or info['HostConfig']['NetworkMode'] != 'none' or info['HostConfig'].get('PortBindings')
                or len(data_mounts) != 1 or data_mounts[0].get('Source') != str(self.directory / 'data')
                or any(m.get('Type') == 'bind' and m not in data_mounts + binary_mounts for m in mounts)):
            raise ValueError('Isolated container ownership or network/mount mismatch')
        return info

    def start_new(self, image):
        self.since = checker.utc_now()
        binary = self.binary_path() if (self.directory / 'runtime-binary.json').exists() else None
        if binary is not None:
            checked_binary(binary)
        docker(*run_arguments(self.name, self.token, self.operation, self.directory / 'data', image, binary))
        self.created = True
        self.inspect_owned()

    def wait_ready(self, fixtures=True, timeout=300):
        deadline = time.monotonic() + timeout
        while True:
            output = checker.read_logs(self.since)
            report, progress = checker.parse_runtime_reports(output)
            (self.evidence_directory / ('isolated-startup.log' if fixtures else 'isolated-restart-startup.log')).write_text(output, encoding='utf-8')
            errors = checker.runtime_errors(output)
            # The installer deliberately continues to the other venues after
            # one fails. Keep their diagnostics in the same run; errors still
            # fail the whole run once the all-venue report is available.
            if errors and (not fixtures or report is not None):
                raise RuntimeError('Isolated startup Scripting/Actor errors: ' + '; '.join(errors))
            if fixtures and report is not None:
                completed = report.get('completed', [])
                if not (report.get('ok') is True and len(completed) == 17
                        and {item['id'] for item in completed} == set(checker.EXPECTED_IDS)):
                    raise RuntimeError('Isolated startup did not initialize all 17 venues')
                manifest = read_json(self.directory / 'stage/behavior_packs/ichiyon_junket_bp/scripts/all17_original_generated.json')
                games = manifest if isinstance(manifest, list) else manifest['venues']
                panels = checker.panel_initialization_evidence(output)
                if any(game.get('legacy_service_controls') for game in games) and not panels['verified']:
                    raise RuntimeError('Isolated startup panel binding or legacy migration failed')
                return {'server_fixture_initialization': True, 'fixture_report': report,
                        'fixture_progress': progress, 'service_panels': panels}
            if not fixtures and 'Server started.' in output:
                return {'server_started_log': True}
            if time.monotonic() >= deadline:
                raise TimeoutError('Isolated BDS startup timed out' + (': ' + '; '.join(errors) if errors else ''))
            if not self.inspect_owned()['State']['Running']:
                raise RuntimeError('Isolated BDS exited during startup')
            time.sleep(2)

    def stop(self):
        info = self.inspect_owned()
        if not info['State']['Running']:
            return {'stopped': True, 'already_stopped': True, 'graceful_stop': False}
        since = checker.utc_now()
        checker.send_console('stop')
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            info = self.inspect_owned()
            if not info['State']['Running']:
                logs = checker.read_logs(since)
                (self.evidence_directory / 'isolated-stop.log').write_text(logs, encoding='utf-8')
                return {'stopped': True, 'graceful_stop': info['State']['ExitCode'] == 0 and 'Quit correctly' in logs,
                        'exit_code': info['State']['ExitCode']}
            time.sleep(1)
        raise TimeoutError('Isolated BDS did not finish its graceful stop')

    def restart(self):
        stopped = self.stop()
        if stopped.get('graceful_stop') is not True:
            raise RuntimeError('Isolated restart requires a confirmed graceful save/stop')
        self.inspect_owned()
        self.since = checker.utc_now()
        docker('start', self.name)
        ready = self.wait_ready(fixtures=False)
        return {**stopped, **ready, 'same_operation': self.operation, 'scope': 'isolated_actual_bds',
                'container': self.name, 'production_restarted': False}

    def cleanup(self):
        try:
            return self.stop()
        except Exception as exc:
            # A bounded fallback may stop only this fully revalidated container.
            self.inspect_owned()
            docker('stop', '--time', '60', self.name, timeout=90)
            info = self.inspect_owned()
            return {'stopped': not info['State']['Running'], 'graceful_stop': False, 'fallback_reason': str(exc)}


def run(operation, timeout=1200, candidate_bundle=None, candidate_sha256=None, *, adapter=None):
    if os.name != 'posix':
        raise ValueError('This helper runs only on the configured Linux BDS host')
    require_plain_path(ISOLATED)
    ISOLATED.mkdir(mode=0o700, exist_ok=True)
    with exclusive_file_lock(ISOLATED / '.preparation.lock'):
        return _run(operation, timeout, candidate_bundle, candidate_sha256, adapter=adapter)


def _run(operation, timeout=1200, candidate_bundle=None, candidate_sha256=None, *, adapter=None):
    if (candidate_bundle is None) != (candidate_sha256 is None):
        raise ValueError('Candidate ZIP and SHA256 must be supplied together')
    operation_dir, archive, backup, packs = validate_operation(operation)
    image = docker('inspect', '--format', '{{.Image}}', PRODUCTION)
    if image != IMAGE:
        raise ValueError('Production BDS image differs from the inspected runtime')
    runtime_contract = {**inspect_runtime_contract(), 'resources_sha256': runtime_resource_fingerprint()}
    fingerprint = reuse_fingerprint(operation_dir, backup, candidate_bundle, candidate_sha256, runtime_contract, adapter)
    existing = reusable_result(fingerprint, operation)
    if existing is not None:
        return existing
    # Recovery never calls this test-only guard. An identical verified result
    # above can be reused even when less than 10 GB is available.
    capacity = check_capacity(PROJECT, platform='linux', purpose='large_test',
                              required_bytes=preparation_required_bytes(archive, operation_dir, candidate_bundle))
    token = uuid.uuid4().hex
    directory = ISOLATED / (operation + '-' + token)
    directory.mkdir(mode=0o700)
    storage = StorageReceipt(directory, operation, purpose='large_test')
    data = directory / 'data'
    data.mkdir()
    scope = 'isolated_candidate_actual_bds' if candidate_bundle is not None else 'isolated_actual_bds'
    proof = {'scope': scope, 'operation_id': operation, 'base_operation_id': operation, 'started_at': checker.utc_now(),
             'isolated_server_resilience_verified': False, 'production_modified': False,
             'production_player_test': False, 'player_test': False, 'mobile_test': False,
             'full_backup': backup, 'image': image, 'network': 'none', 'published_ports': [],
             'runtime_contract': runtime_contract,
             'storage_fingerprint': fingerprint, 'storage_capacity': capacity,
             'execution': {'image_architecture': 'arm64', 'runner': SERVER_RUNNER,
                           'emulator': EMULATOR, 'binary': '/data/' + BINARY},
             'directory': str(directory), 'issues': []}
    server = IsolatedServer(directory, operation, token)
    previous_container = checker.CONTAINER
    try:
        candidate = None
        if candidate_bundle is not None:
            candidate = directory / 'candidate'
            extractor = adapter.extract_candidate if adapter is not None else extract_candidate
            proof['candidate'] = extractor(Path(candidate_bundle), candidate_sha256, candidate)
            proof['candidate_applied_to_production'] = False
        # The only running-container file reads are these fixed immutable BDS
        # runtime resources. No worlds, credentials, env or config are copied.
        for name in RUNTIME_FILES:
            docker('cp', PRODUCTION + ':/data/' + name, str(data / name), timeout=300)
        check_tree(data)
        if copied_resource_fingerprint(data) != runtime_contract['resources_sha256']:
            raise ValueError('Runtime resources changed while preparing the isolated copy')
        binary, shared_new_bytes = prepare_shared_binary()
        write_json(directory / 'runtime-binary.json', {'schema': 1, 'path': str(binary),
                   'sha256': EXECUTABLES['/data/' + BINARY]['sha256'], 'bytes': binary.stat().st_size})
        # Docker's nested file bind needs a mountpoint, not another 256 MB copy.
        (data / BINARY).touch(exist_ok=False)
        (data / BINARY).chmod(0o400)
        proof['shared_runtime'] = {'binary': str(binary), 'sha256': EXECUTABLES['/data/' + BINARY]['sha256'],
                                   'new_logical_bytes': shared_new_bytes,
                                   'new_allocated_bytes': (getattr(binary.stat(), 'st_blocks', 0) * 512
                                                           if shared_new_bytes and hasattr(binary.stat(), 'st_blocks')
                                                           else None if shared_new_bytes else 0),
                                   'read_only_mount': True}
        proof['runtime_files'] = {name: tree_digest(data / name) if (data / name).is_dir()
                                  else {'sha256': digest(data / name)} for name in RUNTIME_FILES}
        proof['extraction'] = extract_backup(archive, directory)
        installer = adapter.install_stage if adapter is not None else install_stage
        proof['packs'] = installer(operation_dir, directory, packs, candidate=candidate)
        if candidate is not None:
            proof['source_corrections'] = install_source_corrections(operation_dir, directory, candidate, archive)
        if adapter is not None:
            proof['extension_preparation'] = adapter.prepare_world(directory, candidate, archive)
        (data / 'server.properties').write_text('\n'.join([
            'server-name=JUNKET isolated resilience', 'gamemode=creative', 'force-gamemode=false',
            'difficulty=peaceful', 'allow-cheats=true', 'max-players=1', 'online-mode=false',
            'allow-list=false', 'server-port=19132', 'server-portv6=19133', 'enable-lan-visibility=false',
            'level-name=' + WORLD, 'default-player-permission-level=member', 'view-distance=4',
            'tick-distance=4', 'max-threads=2', 'texturepack-required=false',
            'content-log-file-enabled=true', 'content-log-console-output-enabled=true',
            'script-watchdog-enable=true', 'enable-command-block=true', '']), encoding='utf-8')
        if adapter is not None:
            proof['isolated_probe_injection'] = adapter.prepare_runtime(directory)
        checker.CONTAINER = server.name
        server.start_new(image)
        proof['container'] = server.name
        proof['startup'] = server.wait_ready()
        write_json(directory / 'isolated-startup-proof.json', proof['startup'])
        if adapter is not None:
            proof['extension_before_restart'] = adapter.check_runtime(directory, server, 'before_restart', timeout)
            proof['issues'].extend(proof['extension_before_restart']['issues'])
        reuse = adapter.reuse_standard_checks() if adapter is not None and hasattr(adapter, 'reuse_standard_checks') else None
        if reuse is not None:
            proof['validation_reuse'] = {k:v for k,v in reuse.items() if k != 'checks'}
            for key in ('warp', 'quality', 'resilience'):
                proof[key] = reuse['checks'][key]
                proof['issues'].extend(proof[key]['issues'])
            # The changed native runtime is exercised on both sides of a real
            # save/restart. Expensive unchanged JUNKET tests retain their old
            # provenance and are explicitly marked unexecuted in this run.
            proof['current_native_restart'] = server.restart()
            proof['current_restart_startup'] = server.wait_ready()
        else:
            warp_check = adapter.check_warp if adapter is not None and hasattr(adapter, 'check_warp') else checker.check_warp_destinations
            proof['warp'] = warp_check(directory, operation)
            proof['issues'].extend(proof['warp']['issues'])
            proof['quality'] = checker.check_quality(directory, operation)
            proof['issues'].extend(proof['quality']['issues'])
            proof['resilience'] = checker.check_resilience(directory, operation, timeout=timeout, restart_callback=server.restart)
            proof['resilience']['scope'] = scope
            if candidate is not None:
                proof['resilience']['candidate_sha256'] = proof['candidate']['sha256']
            proof['resilience']['production_modified'] = False
        write_json(directory / 'resilience-runtime-proof.json', proof['resilience'])
        proof['issues'].extend(proof['resilience']['issues'])
        proof['isolated_server_resilience_verified'] = proof['resilience']['server_resilience_verified'] is True
        if adapter is not None:
            proof['extension_after_restart'] = adapter.check_runtime(directory, server, 'after_restart', timeout)
            proof['issues'].extend(proof['extension_after_restart']['issues'])
    except Exception as exc:
        proof['issues'].append(str(exc))
    finally:
        if server.created:
            try:
                proof['final_stop'] = server.cleanup()
                (directory / 'isolated-container.log').write_text(checker.read_logs(proof['started_at']), encoding='utf-8')
                if proof['final_stop'].get('stopped') is not True or proof['final_stop'].get('graceful_stop') is not True:
                    proof['issues'].append('Final isolated save/stop was not confirmed graceful')
            except Exception as exc:
                proof['issues'].append('Isolated cleanup: ' + str(exc))
        checker.CONTAINER = previous_container
        proof['isolated_server_resilience_verified'] &= not proof['issues']
        proof['completed_at'] = checker.utc_now()
        if proof['isolated_server_resilience_verified']:
            proof['storage_environment'] = {name: tree_digest(directory / name) for name in ('data', 'stage', 'candidate')
                                            if (directory / name).exists()}
        try:
            proof['storage_receipt'] = storage.finish(retained_environments=[str(directory)],
                    recovery_sources=[str(archive), str(operation_dir / 'stage'),
                                      *([str(candidate_bundle)] if candidate_bundle is not None else []),
                                      *([proof['shared_runtime']['binary']] if 'shared_runtime' in proof else [])])
            proof['storage_receipt']['shared_runtime_new_logical_bytes'] = proof.get('shared_runtime', {}).get('new_logical_bytes', 0)
            proof['storage_receipt']['shared_runtime_new_allocated_bytes'] = proof.get('shared_runtime', {}).get('new_allocated_bytes', 0)
            write_receipt(directory / 'storage-receipt.json', proof['storage_receipt'])
        except Exception as error:
            # Preserve the real BDS result and final proof even when optional
            # capacity accounting cannot be persisted. Never expose path/value
            # details from the accounting exception.
            proof['storage_receipt_error'] = type(error).__name__
        write_json(directory / 'isolated-resilience-proof.json', proof)
        if proof['isolated_server_resilience_verified'] and fingerprint is not None:
            index = require_plain_path(ISOLATED / '.reuse')
            index.mkdir(mode=0o700, exist_ok=True)
            # Only this cache pointer advances. Retain its previous references;
            # no old environment, proof or failure is overwritten.
            path = index / (fingerprint + '.json')
            previous = read_json(path) if path.exists() else None
            atomic_json(path, {'directory': directory.name,
                              'proof_sha256': digest(directory / 'isolated-resilience-proof.json'),
                              'previous': previous})
    return proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--operation', '--operationUUID', required=True)
    parser.add_argument('--timeout', type=int, default=1200)
    parser.add_argument('--candidate-bundle', type=Path, help='Private all17 builder ZIP; replace only the clone JUNKET BP/RP')
    parser.add_argument('--candidate-sha256', help='Exact candidate ZIP SHA256')
    args = parser.parse_args()
    if not 60 <= args.timeout <= 3600:
        parser.error('--timeout must be between 60 and 3600 seconds per phase')
    if (args.candidate_bundle is None) != (args.candidate_sha256 is None):
        parser.error('--candidate-bundle and --candidate-sha256 must be supplied together')
    proof = run(args.operation, args.timeout, args.candidate_bundle, args.candidate_sha256)
    print(json.dumps(proof, ensure_ascii=False))
    return 0 if proof['isolated_server_resilience_verified'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
