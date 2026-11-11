"""Private fixed-target native-world adapter for the existing release runner.

Run on the configured BDS host, under its existing Control API Python runtime.
Reuses PackApplications' deployment lock, stop/start, pack backup, versioning,
byte verification and terminal operation proof. World hooks add a complete
backup, offline native placement, protected catalog patch, and world rollback.
JUNKET packs and explicitly named static deltas come from the private bundle.
Other pack content is copied from the active, hash-verified managed installation.
Never builds or replaces DB assets from Git.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import struct
from io import BytesIO
import sys
import time
import uuid

CONTROL = Path('/opt/ichiyon-minecraft-control')
PROJECT = Path('/home/ubuntu/minecraft-bedrock-creative')
WORLD = 'ichiyon-creative-flat'
JUNKET = ('behavior_packs/ichiyon_junket_bp', 'resource_packs/ichiyon_junket_rp')


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def correction_operations(active, operation, commands_updated):
    previous = active.get('native_world_operation', {})
    world_operation = str(uuid.UUID(previous.get('world_operation_id', previous.get('operation_id', ''))))
    warp_operation = operation if commands_updated else str(uuid.UUID(previous.get('warp_operation_id', world_operation)))
    return world_operation, warp_operation


def recover_native_lineage(active, project, installed_hashes, validate_overlay):
    """Recover omitted managed metadata from the existing, verified native pin.

    Ordinary catalog releases can omit native_world_operation from active.json.
    They retain the separate native overlay. Do not infer lineage from a newest
    directory or alter active.json: require that pin, its successful operation,
    original stage and the currently installed pack bytes all agree.
    """
    project = Path(project)
    marker_path = project / 'junket-native-overlay.json'
    marker = read(marker_path)
    from minecraft_junket_overlay import validate_marker_schema
    schema = validate_marker_schema(marker)
    validated = validate_overlay(project)  # Existing read-only stage/tree/hash guard.
    if not isinstance(marker.get('packs'), dict):
        raise ValueError('Unsupported native lineage marker')
    if set(marker['packs']) != set(JUNKET):
        raise ValueError('Native lineage marker must own only the two JUNKET packs')
    operation = str(uuid.UUID(marker['operation_id']))
    world_operation = str(uuid.UUID(marker['world_operation_id']))
    warp_operation = str(uuid.UUID(marker['warp_operation_id']))
    expected = {'schema': schema, 'operation_id': operation, 'packs': marker['packs']}
    if 'static_overlay' in marker:
        expected['static_overlay'] = marker['static_overlay']
    if validated != expected:
        raise ValueError('Native lineage stage validation differs from marker')
    bundle_hash = marker.get('native_source_bundle_sha256', '')
    if not isinstance(bundle_hash, str) or not re.fullmatch('[0-9a-f]{64}', bundle_hash):
        raise ValueError('Invalid native lineage source bundle fingerprint')
    baseline = {entry['path']: entry['content_hash'] for entry in active['packs']}
    for pack, expected_hash in marker['packs'].items():
        if not isinstance(expected_hash, str) or not re.fullmatch('[0-9a-f]{64}', expected_hash):
            raise ValueError('Invalid native lineage pack fingerprint')
        if baseline.get(pack) != expected_hash or installed_hashes.get(pack) != expected_hash:
            raise ValueError('Native lineage does not match installed managed pack: ' + pack)
    native_root = project / 'junket-world-applications'
    latest_path = native_root / 'latest.json'
    proof_path = native_root / operation / 'native-release-proof.json'
    preflight_path = native_root / operation / 'preflight.json'
    latest, proof, preflight = read(latest_path), read(proof_path), read(preflight_path)
    if latest.get('operation_id') != operation or latest.get('status') != 'succeeded':
        raise ValueError('Native lineage is not the latest successful native operation')
    if (proof.get('operation_id') != operation or proof.get('status') != 'succeeded'
            or proof.get('installed') is not True or proof.get('world') != WORLD
            or proof.get('world_placement_operation') != world_operation
            or proof.get('warp_operation') != warp_operation):
        raise ValueError('Native lineage success proof does not match marker')
    if (preflight.get('world') != WORLD
            or preflight.get('bundle_report_sha256') != bundle_hash
            or preflight.get('world_placement_operation') != world_operation
            or preflight.get('warp_operation') != warp_operation):
        raise ValueError('Native lineage source proof does not match marker')
    if marker.get('static_overlay') != preflight.get('static_overlay'):
        raise ValueError('Native lineage static overlay differs from preparation proof')
    return {'schema': 1, 'managed_operation_id': active['operation_id'],
            'native_world_operation': marker,
            'verified_installed_packs': {pack: installed_hashes[pack] for pack in JUNKET},
            'verified_files': {str(path.relative_to(project)): sha256(path)
                               for path in (marker_path, latest_path, proof_path, preflight_path)}}


def recovered_native_active(active, recovery):
    """Bind a saved recovery to its unchanged managed baseline, including resume."""
    if (recovery.get('schema') != 1
            or recovery.get('managed_operation_id') != active['operation_id']):
        raise ValueError('Native lineage recovery belongs to another managed operation')
    marker = recovery['native_world_operation']
    baseline = {entry['path']: entry['content_hash'] for entry in active['packs']}
    hashes = {pack: baseline.get(pack) for pack in JUNKET}
    if marker.get('packs') != hashes or recovery.get('verified_installed_packs') != hashes:
        raise ValueError('Native lineage recovery differs from the saved managed baseline')
    return {**active, 'native_world_operation': marker}


def require_same_physical_layout(current, original):
    def physical(value):
        return [{**{k: game.get(k) for k in ('id', 'production_origin', 'size', 'destination', 'block_overlays')},
                 'structures': [{k: v for k, v in part.items() if k != 'path'} for part in game['structures']]}
                for game in value['games']]
    if physical(current) != physical(original):
        raise ValueError('Pack correction cannot change buildings, floors or warp destinations')


def confirmed_blocks(output):
    return {tuple(map(int, match)) for match in re.findall(
        r'Successfully found the block at (-?\d+),\s*(-?\d+),\s*(-?\d+)\.', output)}


def wait_console(api, since, log, complete, timeout=90):
    """BDS consumes multiline stdin asynchronously; tee success is not execution."""
    deadline = time.monotonic() + timeout
    while True:
        response = api.run_fixed(['docker', 'logs', '--since', since, api.CONTAINER_NAME])
        output = response.stdout + response.stderr
        log.write_text(output, encoding='utf-8')
        if response.returncode == 0 and complete(output):
            return
        if time.monotonic() >= deadline:
            raise RuntimeError('Bedrock console confirmation timed out: ' + log.name)
        time.sleep(2)


def verified_bundle(bundle):
    report = read(bundle / 'bundle-report.json')
    if report['games'].__len__() != 17 or report['production_applied'] is not False:
        raise ValueError('Invalid private bundle')
    for rel, expected in report['files'].items():
        p = (bundle / rel).resolve()
        if not p.is_relative_to(bundle.resolve()) or not p.is_file() or sha256(p) != expected:
            raise ValueError('Private bundle fingerprint mismatch: ' + rel)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--operation', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--resume', action='store_true', help='Start a prepared operation or recover an interrupted one')
    corrections = parser.add_mutually_exclusive_group()
    corrections.add_argument('--packs-only', action='store_true', help='Repair packs after a verified native placement; retain the world')
    corrections.add_argument('--packs-and-warps', action='store_true', help='Repair packs/catalog commands; allow validated reception/upper-floor or same-bounds barrier-only revisions')
    args = parser.parse_args()
    operation = str(uuid.UUID(args.operation))
    from minecraft_storage import check_capacity, StorageReceipt, write_receipt
    directory = PROJECT / 'junket-world-applications' / operation
    purpose = 'recovery' if args.resume else 'deployment'
    capacity = check_capacity(PROJECT, platform='linux', purpose=purpose)
    if capacity['warnings']:
        print(json.dumps({'storage_warning': capacity}), file=sys.stderr, flush=True)
    storage = StorageReceipt(directory, operation, purpose=purpose)
    try:
        return _release(args, operation)
    finally:
        try:
            if directory.is_dir():
                sources = [str(args.bundle.resolve())]
                backup_path = directory / 'full-backup.json'
                if backup_path.is_file():
                    sources.append(read(backup_path)['path'])
                write_receipt(directory / 'native-storage-receipt.json', storage.finish(
                    retained_environments=[str(directory)], recovery_sources=sources, capacity=capacity))
        except Exception as error:
            # Accounting is not the deployment result. In particular, do not
            # turn a successful release into a Restart=on-failure retry loop.
            try:
                print(json.dumps({'storage_receipt_warning': type(error).__name__}), file=sys.stderr, flush=True)
            except Exception:
                pass


def _release(args, operation):
    bundle = args.bundle.resolve()
    verified_bundle(bundle)
    sys.path.insert(0, str(CONTROL))
    private_dependencies = Path(__file__).resolve().parent.parent / 'python-deps'
    sys.path.insert(0, str(private_dependencies))
    # Reuse the already installed host runtimes; do not alter the control
    # service virtualenv or install packages during a production transaction.
    for runtime in ('.venv-leveldb', '.venv-nbt'):
        candidates = list((PROJECT / runtime / 'lib').glob('python*/site-packages'))
        if len(candidates) != 1:
            raise RuntimeError('Existing native-world Python runtime is unavailable')
        sys.path.append(str(candidates[0]))
    import nbtlib
    if getattr(nbtlib, '__version__', None) != '2.0.4':
        raise RuntimeError('The reviewed native writer requires private nbtlib 2.0.4')
    import minecraft_control_api as api
    from minecraft_cosmetics_apply import (PackApplications, atomic_json, content_hash,
                                          prepare_manifests, now, TERMINAL, pack_layout)
    from junket_static_overlay import (prepare_native_overlay, verify_native_stage,
                                       load_overlay, plan_overlay)
    if api.PROJECT_DIR.resolve() != PROJECT or api.WORLD_NAME != WORLD:
        raise ValueError('This adapter is restricted to the recorded production world')
    root = PROJECT / 'cosmetics-applications'
    native_root = PROJECT / 'junket-world-applications'
    directory = native_root / operation
    active = read(directory / 'managed-active-before.json') if args.resume else read(root / 'active.json')
    baseline = {item['path']: item['content_hash'] for item in active['packs']}
    correction = args.packs_only or args.packs_and_warps
    lineage_active = active
    lineage_recovery = None
    lineage_path = directory / 'native-lineage-recovery.json'
    if args.resume and correction and not active.get('native_world_operation'):
        if sha256(lineage_path) != read(directory / 'preflight.json').get('native_lineage_recovery_sha256'):
            raise ValueError('Saved native lineage recovery fingerprint mismatch')
        lineage_recovery = read(lineage_path)
        lineage_active = recovered_native_active(active, lineage_recovery)
    stage = directory / 'stage'
    static_pin = None
    static_result = None
    if not args.resume:
        latest = read(root / 'latest.json')
        if latest['status'] not in TERMINAL:
            raise RuntimeError('Existing managed operation is still active')
        installed_hashes = {}
        for pack, expected in baseline.items():
            installed_hashes[pack] = content_hash(api.DATA_DIR / pack)
            if installed_hashes[pack] != expected:
                raise RuntimeError('Installed managed pack differs from active proof: ' + pack)
        if correction and not active.get('native_world_operation'):
            from minecraft_junket_overlay import preserve_overlay
            lineage_recovery = recover_native_lineage(active, PROJECT, installed_hashes, preserve_overlay)
            lineage_active = recovered_native_active(active, lineage_recovery)
        directory.mkdir(parents=True, exist_ok=False)
        atomic_json(directory / 'managed-active-before.json', active)
        if lineage_recovery:
            atomic_json(lineage_path, lineage_recovery)
        marker = PROJECT / 'junket-native-overlay.json'
        atomic_json(directory / 'overlay-before.json', read(marker) if marker.exists() else None)
        stage.mkdir()
        for pack in baseline:
            shutil.copytree(api.DATA_DIR / pack, stage / pack)
        previous_stage = root / active['operation_id'] / 'stage'
        shutil.copy2(previous_stage / 'cosmetics-build.json', stage / 'cosmetics-build.json')
        shutil.copytree(previous_stage / 'cosmetics', stage / 'cosmetics')
        for pack in JUNKET:
            target = stage / pack
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(bundle / 'packs' / pack, target)
        static_pin, static_result = prepare_native_overlay(PROJECT, bundle, directory, stage)
        if static_result is not None:
            atomic_json(directory / 'static-overlay-proof.json', static_result)
        prepare_manifests(stage, api.DATA_DIR)
    else:
        preflight = read(directory / 'preflight.json')
        static_pin = preflight.get('static_overlay')
        if static_pin is not None:
            static_source = directory / 'static-overlay'
            load_overlay(static_source, static_pin['manifest_sha256'])
            proof_path = directory / 'static-overlay-proof.json'
            if sha256(proof_path) != preflight.get('static_overlay_proof_sha256'):
                raise ValueError('Saved static overlay proof fingerprint mismatch')
            static_result = read(proof_path)
            planned, _ = plan_overlay(static_source, stage, static_pin['manifest_sha256'])
            if any(sha256(stage / relative) != hashlib.sha256(data).hexdigest() for relative, data in planned.items()):
                raise ValueError('Prepared static overlay no longer matches the stage')
    _, packs, _ = pack_layout(stage)
    if args.resume and preflight.get('managed_pack_preservation'):
        # Recovery may see a partially installed live pack or unfinished backup.
        # Compare the immutable stage to the preparation proof, then let the
        # existing transaction decide whether the committed backup is restorable.
        preservation = preflight['managed_pack_preservation']
        expected_stage = {**preservation['unchanged_managed_packs'],
                          **{pack: value['after_content_hash'] for pack, value in preservation['static_overlay_packs'].items()}}
        if set(expected_stage) != set(packs) - set(JUNKET) or any(content_hash(stage / pack) != expected for pack, expected in expected_stage.items()):
            raise ValueError('Prepared managed pack differs from the saved preservation proof')
    else:
        preservation = verify_native_stage(stage, api.DATA_DIR, baseline, static_result)
    layout = read(bundle / 'source-layouts.json')
    for game in layout['games']:
        for structure in game['structures']:
            path = (bundle / structure['path']).resolve()
            if not path.is_relative_to(bundle) or sha256(path) != structure['sha256']:
                raise ValueError('Source structure mismatch')
            structure['path'] = str(path)
    world_operation = operation
    warp_operation = operation
    reception_changes = []
    barrier_changes = []
    previous_layout = None
    inherited_world_operation = operation
    if correction:
        world_operation, warp_operation = correction_operations(lineage_active, operation, args.packs_and_warps)
        inherited_world_operation = world_operation
        original_directory = native_root / world_operation
        original_proof = read(original_directory / 'native-release-proof.json')
        if original_proof.get('status') != 'succeeded' or original_proof.get('world') != WORLD:
            raise ValueError('Pack correction requires an already completed native world placement')
        latest_native = lineage_active.get('native_world_operation', {}).get('operation_id', world_operation)
        previous_layout = read(native_root / str(uuid.UUID(latest_native)) / 'source-layouts.json')
        if args.packs_and_warps and layout.get('barrier_revision') == 1:
            from junket_barriers import validate_barrier_revision
            barrier_changes = validate_barrier_revision(layout, previous_layout)
            world_operation = operation
        elif args.packs_and_warps and layout.get('reception_revision') == 1:
            from junket_venue_correction import reception_corrections
            reception_changes = reception_corrections(layout, previous_layout)
            if any(change['structures'] for change in reception_changes):
                world_operation = operation
        else:
            require_same_physical_layout(layout, previous_layout)
    if not args.resume:
        atomic_json(directory / 'source-layouts.json', layout)
        atomic_json(directory / 'preflight.json', {
        'world': WORLD, 'previous_operation': active['operation_id'],
        'catalog_digest': active['catalog_digest'], 'baseline': baseline,
        'bundle_report_sha256': sha256(bundle / 'bundle-report.json'),
        'unchanged_managed_packs': sorted(preservation['unchanged_managed_packs']),
        'static_overlay': static_pin,
        'static_overlay_proof_sha256': sha256(directory / 'static-overlay-proof.json') if static_pin else None,
        'managed_pack_preservation': preservation,
        'apply_requested': args.apply,
        'packs_only': args.packs_only, 'packs_and_warps': args.packs_and_warps,
        'world_placement_operation': world_operation, 'warp_operation': warp_operation,
        'source_reception_corrections': reception_changes,
        'source_barrier_corrections': barrier_changes,
        'native_lineage_recovery_sha256': sha256(lineage_path) if lineage_recovery else None,
        })
    bank_plan = None
    if (bundle / 'bank-plan.json').exists():
        if not args.packs_and_warps:
            raise ValueError('Bank extension requires the bounded packs-and-warps transaction')
        bank_plan = read(bundle / 'bank-plan.json')
        bank_source = read(bundle / 'bank-source.json')
        bank_structure = (bundle / bank_source['structure_path'].replace('\\', '/')).resolve()
        if not bank_structure.is_relative_to(bundle) or sha256(bank_structure) != bank_source['structure_sha256']:
            raise ValueError('Bank source fingerprint mismatch')
        from junket_bank_world import validate_plan
        validate_plan(bank_plan)
        if bank_plan['source_structure_sha256'] != sha256(bank_structure):
            raise ValueError('Bank before ledger belongs to another original structure')
        world_operation = operation
        if not args.resume:
            preflight = read(directory / 'preflight.json')
            preflight.update(bank_plan_sha256=sha256(bundle / 'bank-plan.json'),
                             bank_source_sha256=sha256(bundle / 'bank-source.json'),
                             world_placement_operation=world_operation)
            atomic_json(directory / 'preflight.json', preflight)
    if not args.apply:
        print(json.dumps({'status': 'prepared', 'operation_id': operation, 'directory': str(directory)}))
        return

    class NativeApplications(PackApplications):
        def _console(self, command):
            # The current image runs BDS as uid 1001 without CAP_SYS_PTRACE.
            # Its in-container send-command cannot open /proc/.../fd/0. Resolve
            # ONLY this container's BDS PID, then use the existing host sudo
            # authority to write its console. No shell parses command contents.
            result = api.run_fixed(['docker', 'top', api.CONTAINER_NAME, '-eo', 'pid,comm'])
            rows = [line.split() for line in result.stdout.splitlines()[1:]]
            pids = [row[0] for row in rows if len(row) == 2 and row[0].isdigit()
                    and row[1].startswith('bedrock_server')]
            if result.returncode or len(pids) != 1:
                raise RuntimeError('Unable to identify this container BDS console')
            sent = subprocess.run(['sudo', 'tee', '/proc/' + pids[0] + '/fd/0'],
                                  input=command + '\n', text=True, capture_output=True, timeout=20)
            return {'command': command, 'returncode': sent.returncode,
                    'stderr': sent.stderr[-500:], 'stdout': sent.stdout[-500:]}

        def _save(self, job, **changes):
            if changes.get('status') == 'succeeded':
                pinned = {'schema': 2 if static_pin is not None else 1, 'operation_id': operation,
                          'world_operation_id': world_operation, 'warp_operation_id': warp_operation,
                          'pack_update_only': args.packs_only, 'catalog_commands_updated': args.packs_and_warps,
                          'native_source_bundle_sha256': sha256(bundle / 'bundle-report.json'),
                          'packs': {pack: content_hash(stage / pack) for pack in JUNKET}}
                if static_pin is not None:
                    pinned['static_overlay'] = static_pin
                atomic_json(PROJECT / 'junket-native-overlay.json', pinned)
                # Keep the DB-managed catalog identity while explicitly recording
                # the separate native-world migration and its exact bundle hash.
                managed = dict(active)
                managed['packs'] = read(self.root / 'active.json')['packs']
                managed['native_world_operation'] = pinned
                atomic_json(root / 'active.json', managed)
            super()._save(job, **changes)
            print(json.dumps({'operation_id': operation, 'status': job['status']}, ensure_ascii=False), flush=True)

        def _check_blocks(self, positions, block, destination, label, commands, mutations=()):
            expected = {tuple(at) for at in positions}
            since = now()
            tests = [f'testforblock {x} {y} {z} {block}' for x, y, z in positions]
            result = self._console('\n'.join([*mutations, *tests]))
            commands.append(result)
            if result['returncode']:
                raise RuntimeError('Unable to send native preload commands: ' + label)
            wait_console(api, since, destination / (label + '.log'),
                         lambda output: expected <= confirmed_blocks(output))

        def _backup(self, destination):
            try:
                self._backup_inner(destination)
            except Exception as error:
                atomic_json(destination / 'native-error.json', {
                    'phase': 'backup/preload', 'type': type(error).__name__, 'message': str(error)})
                raise

        def _backup_inner(self, destination):
            # Check again inside the same existing deployment lock, before writes.
            if read(root / 'active.json')['operation_id'] != active['operation_id']:
                raise RuntimeError('Managed catalog changed during preparation')
            for pack, expected in baseline.items():
                if content_hash(api.DATA_DIR / pack) != expected:
                    raise RuntimeError('Managed pack changed during preparation: ' + pack)
            free = shutil.disk_usage(PROJECT).free
            if free < 1024 * 1024 * 1024:
                raise RuntimeError('Less than 1 GiB available for full backup and rollback')
            full_backup = Path(api.create_backup())
            atomic_json(destination / 'full-backup.json', {
                'path': str(full_backup), 'sha256': sha256(full_backup),
                'bytes': full_backup.stat().st_size, 'server_was_stopped': True,
            })
            super()._backup(destination)
            world_path = api.DATA_DIR / 'worlds' / WORLD
            shutil.copytree(world_path, destination / 'prewarm-original-world')
            if correction:
                if args.packs_and_warps:
                    shutil.copytree(world_path, destination / 'working-world')
                return
            # Bedrock may discard clean generated chunks when a ticking area is
            # removed. Mark only entirely absent chunks dirty, then immediately
            # restore their verified flat-world air cell. Existing chunks are
            # never touched by this preload aid, and rollback restores the full
            # prewarm world even if interrupted between the two commands.
            import leveldb
            level = nbtlib.File.parse(BytesIO((world_path / 'level.dat').read_bytes()[8:]), byteorder='little')
            layers = json.loads(str(level.get('FlatWorldLayers', '{}')))
            if (int(level.get('Generator', -1)) != 2 or not layers.get('block_layers')
                    or sum(int(v.get('count', 0)) for v in layers['block_layers']) >= 383):
                raise RuntimeError('Native preloader requires the verified flat world and empty y=319')
            db = leveldb.LevelDB(str(world_path / 'db'))
            try:
                prefixes = {key[:8] for key in db.keys() if len(key) >= 9}
            finally:
                db.close()
            markers = {}
            for game in layout['games']:
                x, _, z = game['production_origin']
                sx, _, sz = game['size']
                markers[game['id']] = [
                    [max(x, cx * 16), 319, max(z, cz * 16)]
                    for cx in range(x // 16, (x + sx - 1) // 16 + 1)
                    for cz in range(z // 16, (z + sz - 1) // 16 + 1)
                    if struct.pack('<ii', cx, cz) not in prefixes]
            atomic_json(destination / 'preload-new-chunks.json', markers)
            # Let Bedrock itself generate missing chunk metadata. No fabricated
            # biome/version records are written by the native structure writer.
            self._start()
            commands = []
            try:
                games = layout['games']
                groups = [games[i:i+2] for i in range(0, len(games), 2)]
                # Keep the four existing ticking areas. At most five new areas
                # are present, each spanning two neighboring planned venues.
                for batch_at in range(0, len(groups), 5):
                    batch = groups[batch_at:batch_at+5]
                    names = []
                    positions = []
                    added_since = now()
                    for index, group in enumerate(batch, batch_at):
                        x, y, z = group[0]['production_origin']
                        endx = max(g['production_origin'][0] + max(g['size'][0], 64) - 1 for g in group)
                        endz = max(g['production_origin'][2] + max(g['size'][2], 64) - 1 for g in group)
                        if (endx // 16 - x // 16 + 1) * (endz // 16 - z // 16 + 1) > 100:
                            raise RuntimeError('Preload group exceeds Bedrock chunk limit')
                        name = 'jnative_' + str(index)
                        names.append(name)
                        positions.extend(at for g in group for at in markers[g['id']])
                        result = self._console(f'tickingarea add {x} {y} {z} {endx} {y} {endz} {name} true')
                        commands.append(result)
                        if result['returncode']:
                            raise RuntimeError('Unable to register preload area')
                    wait_console(api, added_since, destination / f'preload-added-{batch_at}.log',
                                 lambda output: output.count('Added ticking area from') == len(names))
                    # preload is a world-launch guarantee, not a fixed 3s delay.
                    self._stop()
                    self._start()
                    if positions:
                        self._check_blocks(positions, 'air', destination,
                                           f'preload-loaded-{batch_at}', commands)
                        self._check_blocks(positions, 'stone', destination,
                                           f'preload-dirty-{batch_at}', commands,
                                           [f'setblock {x} {y} {z} stone [] keep' for x, y, z in positions])
                        self._check_blocks(positions, 'air', destination,
                                           f'preload-restored-{batch_at}', commands,
                                           [f'setblock {x} {y} {z} air' for x, y, z in positions])
                    removed_since = now()
                    for name in names:
                        result = self._console('tickingarea remove ' + name)
                        commands.append(result)
                        if result['returncode']:
                            raise RuntimeError('Unable to remove temporary chunk loader')
                    wait_console(api, removed_since, destination / f'preload-removed-{batch_at}.log',
                                 lambda output: output.count('Removed ticking area(s)') == len(names))
            finally:
                self._stop()
                atomic_json(destination / 'preload.json', commands)
            shutil.copytree(api.DATA_DIR / 'worlds' / WORLD, destination / 'working-world')

        def _install(self, destination):
            try:
                if args.packs_only:
                    super()._install(destination)
                    for filename in ('native-proof.json', 'warp-proof.json'):
                        inherited_operation = world_operation if filename == 'native-proof.json' else warp_operation
                        original = read(native_root / inherited_operation / filename)
                        atomic_json(destination / filename, {**original,
                            'verification_inherited_from': inherited_operation,
                            'world_rewritten_by_this_operation': False})
                    return
                from junket_native_world import apply_layouts
                from junket_catalog_world import native_plan, apply_patch
                from junket_structure import ConvertedWorld
                working = destination / 'working-world'
                backup = read(destination / 'full-backup.json')['path']
                if args.packs_and_warps:
                    native = {**read(native_root / inherited_world_operation / 'native-proof.json'),
                              'verification_inherited_from': inherited_world_operation,
                              'buildings_rewritten_by_this_operation': False}
                    if reception_changes:
                        from junket_venue_correction import apply_reception_corrections
                        extension = apply_reception_corrections(working, layout, previous_layout,
                            destination / 'native-extension-rollback.json', full_backup=Path(backup), world_is_stopped=True)
                        atomic_json(destination / 'native-extension-proof.json', extension)
                        native['source_reception_extension'] = extension
                    if barrier_changes:
                        from junket_barriers import apply_barrier_revision
                        barrier = apply_barrier_revision(working, layout, previous_layout,
                            destination / 'native-barrier-rollback.json', full_backup=Path(backup), world_is_stopped=True)
                        atomic_json(destination / 'native-barrier-proof.json', barrier)
                        native['source_barrier_revision'] = barrier
                        native['barriers_rewritten_by_this_operation'] = True
                        native['world_rewritten_by_this_operation'] = True
                else:
                    world = ConvertedWorld(working)
                    try:
                        for positions in read(destination / 'preload-new-chunks.json').values():
                            for at in positions:
                                prefix = struct.pack('<ii', at[0] // 16, at[2] // 16)
                                biome = world.get(prefix + b'+')
                                if world.get(prefix + b',') is None or biome is None or len(biome) < 512:
                                    raise RuntimeError('Bedrock did not persist generated chunk metadata: ' + str(at))
                                blocks = world.blocks(*at)
                                if any(str(block['name']) != 'minecraft:air' for block in blocks):
                                    raise RuntimeError('New chunk preload marker was not restored to air: ' + str(at))
                    finally:
                        world.db.close()
                    native = apply_layouts(working, layout, destination / 'native-rollback.json',
                                           full_backup=Path(backup), world_is_stopped=True,
                                           legacy_pack=self.backup_source(destination, 'behavior_packs/ichiyon_junket_bp'))
                if bank_plan is not None:
                    from junket_bank_world import apply_bank
                    bank = apply_bank(working, bank_plan, bank_structure,
                        destination / 'bank-world-rollback.json', full_backup=Path(backup), world_is_stopped=True)
                    atomic_json(destination / 'bank-world-proof.json', bank)
                    native['bank_extension'] = bank
                    native['world_rewritten_by_this_operation'] = True
                atomic_json(destination / 'native-proof.json', native)
                world = ConvertedWorld(working)
                try:
                    warps = apply_patch(world, native_plan(layout, read(bundle / 'junket-catalog.json')),
                                        destination / 'warp-rollback.json', command_only=args.packs_and_warps)
                finally:
                    world.db.close()
                atomic_json(destination / 'warp-proof.json', warps)
                super()._install(destination)
                world = api.DATA_DIR / 'worlds' / WORLD
                # PackApplications updates references; carry those exact bytes
                # into the fully verified offline world before the atomic swap.
                for name in ('world_behavior_packs.json', 'world_resource_packs.json'):
                    shutil.copy2(world / name, working / name)
                world.rename(destination / 'previous-world')
                working.rename(world)
                atomic_json(destination / 'world-swapped.json', {'world': str(world), 'at': now()})
            except Exception as error:
                atomic_json(destination / 'native-error.json', {'type': type(error).__name__, 'message': str(error)})
                raise

        def _before_restore(self, destination):
            # PackApplications holds the validated CAS recovery lock before this swap.
            previous = destination / 'previous-world'
            if not previous.exists():
                previous = destination / 'prewarm-original-world'
            if previous.exists():
                world = api.DATA_DIR / 'worlds' / WORLD
                if world.exists():
                    world.rename(destination / ('failed-world-' + uuid.uuid4().hex))
                previous.rename(world)

        def _restore(self, destination):
            super()._restore(destination)
            atomic_json(root / 'active.json', read(destination / 'managed-active-before.json'))
            marker = PROJECT / 'junket-native-overlay.json'
            previous_marker = read(destination / 'overlay-before.json')
            if previous_marker is None:
                if marker.exists():
                    marker.unlink()
            else:
                atomic_json(marker, previous_marker)

    applications = NativeApplications(api)
    applications.root = native_root
    job_path = directory / 'job.json'
    recovering = args.resume and job_path.exists()
    job = read(job_path) if recovering else {'operation_id': operation, 'catalog_digest': active['catalog_digest'],
           'status': 'queued', 'started_at': now(), 'message': 'JUNKET native world migration'}
    def finalize():
        final = dict(job)
        native_active = applications._read('active.json')
        if native_active and native_active.get('operation_id') == operation:
            final['active_digest'] = native_active['catalog_digest']
            final['installed'] = all(content_hash(api.DATA_DIR / p['path']) == p['content_hash']
                                     for p in native_active['packs'])
        final['runtime'] = api.status_payload()
        final['world'] = WORLD
        final['world_placement_operation'] = world_operation
        final['packs_only'] = args.packs_only
        final['packs_and_warps'] = args.packs_and_warps
        final['warp_operation'] = warp_operation
        final['static_overlay'] = static_pin
        final['managed_pack_preservation'] = preservation
        final['two_player_playthrough'] = False
        final['full_backup'] = read(directory / 'full-backup.json') if (directory / 'full-backup.json').exists() else None
        atomic_json(directory / 'native-release-proof.json', final)
        print(json.dumps(final, ensure_ascii=False), flush=True)
    if recovering and job['status'] in TERMINAL:
        finalize()
        return
    if not recovering:
        applications._save(job)
    def interrupted(signum, frame):
        raise RuntimeError('Native release interrupted')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    applications._run(job, recovering=recovering)
    finalize()
    if job['status'] != 'succeeded':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
