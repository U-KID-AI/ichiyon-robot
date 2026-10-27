"""Retest fixture persistence only in an existing stopped, owned BDS clone.

The original failed evidence is immutable. Earlier quality, warp and destructive
results are references, not rerun or relabelled as a successful full-candidate run.
"""
from copy import deepcopy
import argparse
import json
import os
from pathlib import Path
import re
import shutil
from types import SimpleNamespace
import uuid

import check_junket_isolated_resilience as isolated
from check_junket_isolated_resilience import checker

SCRIPT_PREFIX = 'packs/behavior_packs/ichiyon_junket_bp/scripts/'
SCRIPT_DELTA = {'fixture_reconciliation.js', 'special_venue.js', 'production_venue.js',
                'resilience_probe.js', 'all17_reference_main.js'}
NEW_SCRIPT = SCRIPT_PREFIX + 'fixture_reconciliation.js'
BP_MANIFEST = 'packs/behavior_packs/ichiyon_junket_bp/manifest.json'
RP_MANIFEST = 'packs/resource_packs/ichiyon_junket_rp/manifest.json'


def compare_candidate(previous, candidate):
    """No reducers, menus, assets, layouts, warps or source structures may change."""
    def inventory(root):
        isolated.check_tree(root)
        return {p.relative_to(root).as_posix(): isolated.digest(p) for p in root.rglob('*') if p.is_file()}
    old, new = inventory(previous), inventory(candidate)
    removed, added = set(old) - set(new), set(new) - set(old)
    changed = {name for name in old.keys() & new.keys() if old[name] != new[name]}
    allowed = {SCRIPT_PREFIX + name for name in SCRIPT_DELTA} | {BP_MANIFEST, RP_MANIFEST, 'bundle-report.json'}
    if removed or added != {NEW_SCRIPT} or not changed <= allowed:
        raise ValueError('Restart candidate contains changes outside the fixed fixture reconciliation scope')
    if not {SCRIPT_PREFIX + name for name in SCRIPT_DELTA - {'fixture_reconciliation.js'}} <= changed:
        raise ValueError('Restart candidate is missing the reconciliation/probe/boot-order fix')
    for name, version in ((BP_MANIFEST, [0, 9, 12]), (RP_MANIFEST, [0, 8, 6])):
        prior = isolated.read_json(previous / name)
        proposed = isolated.read_json(candidate / name)
        normalized = deepcopy(proposed)
        if proposed['header']['version'] != version or any(m['version'] != version for m in proposed['modules']):
            raise ValueError('Unexpected candidate pack version')
        normalized['header']['version'] = prior['header']['version']
        for module, old_module in zip(normalized['modules'], prior['modules']):
            module['version'] = old_module['version']
        if name == RP_MANIFEST:
            if proposed.get('capabilities') != ['pbr']:
                raise ValueError('Candidate lost Vibrant Visuals compatibility')
            if 'capabilities' in prior:
                normalized['capabilities'] = prior['capabilities']
            else:
                normalized.pop('capabilities')
        if normalized != prior:
            raise ValueError('Candidate changed unrelated manifest fields')
    return {'added_files': sorted(added), 'changed_files': sorted(changed), 'removed_files': [],
            'unchanged_files': len(old.keys() & new.keys()) - len(changed),
            'game_reducers_menus_assets_layouts_warps_structures_identical': True}


def validate_previous(proof, directory, operation, source_sha, games):
    if (proof.get('operation_id') != operation or proof.get('directory') != str(directory)
            or proof.get('candidate', {}).get('sha256') != source_sha
            or proof.get('production_modified') is not False or proof.get('image') != isolated.IMAGE
            or proof.get('network') != 'none' or proof.get('published_ports') != []
            or proof.get('final_stop', {}).get('stopped') is not True
            or proof.get('final_stop', {}).get('graceful_stop') is not True):
        raise ValueError('Previous clone identity, candidate or graceful-stop proof differs')
    counts = {game['id']: len(game['fixtures']) for game in games}
    quality, warp, resilience = (proof.get(name, {}) for name in ('quality', 'warp', 'resilience'))
    if (quality.get('server_quality_verified') is not True or quality.get('issues') != []
            or quality.get('report', {}).get('rules_schema') != 1
            or checker.validate_quality_report(quality.get('report'), quality.get('request_id'), counts)):
        raise ValueError('Previous all17 rules/quality evidence is not complete')
    if (warp.get('server_warp_destinations_verified') is not True or warp.get('issues') != []
            or checker.validate_warp_report(warp.get('report'), warp.get('request_id'),
                                            {game['id']: game['destination'] for game in games})):
        raise ValueError('Previous all17 warp evidence is not complete')
    reports = resilience.get('reports', {})
    if checker.validate_resilience_report(reports.get('destructive'), resilience.get('request_id'), counts,
            expected_path_counts=checker.resilience_path_counts(games)):
        raise ValueError('Previous destructive recovery evidence is not complete')
    restart = reports.get('restart', {})
    checks = restart.get('checks', [])
    if (restart.get('phase') != 'restart' or restart.get('request_id') != resilience.get('request_id')
            or len(checks) != 17 or {row.get('id') for row in checks} != set(checker.EXPECTED_IDS)
            or {row.get('id') for row in checks if row.get('ok') is not True} != {'sparrow'}
            or any(row.get('restart_persistence') is not True for row in checks if row['id'] != 'sparrow')
            or {row.get('id') for row in restart.get('failures', [])} != {'sparrow'}):
        raise ValueError('This limited retest is only for the isolated Sparrow restart identity failure')
    return {'quality': 'prior candidate all17 rules and lifecycle passed', 'warp': 'prior candidate all17 passed',
            'destructive': 'prior candidate all17 passed', 'restart': 'prior candidate Sparrow failed; retained unchanged',
            'source_candidate_sha256': source_sha, 'source_request_id': resilience['request_id']}


def run(directory, operation, previous_sha, candidate_bundle, candidate_sha, timeout=1200):
    if os.name != 'posix':
        raise ValueError('This helper runs only on the configured Linux BDS host')
    if str(uuid.UUID(operation)) != operation:
        raise ValueError('A canonical operation UUID is required')
    directory = isolated.require_plain_path(Path(directory))
    match = re.fullmatch(re.escape(operation) + r'-([0-9a-f]{32})', directory.name)
    if directory.parent != isolated.ISOLATED or not match:
        raise ValueError('Exact existing isolated clone path required')
    token = match.group(1)
    server = isolated.IsolatedServer(directory, operation, token)
    server.created = True  # Adopt only after the same immutable ownership checks.
    owned = server.inspect_owned()
    if owned['State']['Running'] or owned['State'].get('ExitCode') != 0:
        raise ValueError('Clone must already be stopped with successful exit status')
    if isolated.digest(server.binary_path()) != isolated.EXECUTABLES['/data/' + isolated.BINARY]['sha256']:
        raise ValueError('Stopped clone BDS executable differs from the inspected runtime')
    previous_proof_path = directory / 'isolated-resilience-proof.json'
    previous_proof_sha = isolated.digest(previous_proof_path)
    previous = isolated.read_json(previous_proof_path)
    if previous.get('container') != server.name:
        raise ValueError('Previous proof references a different container')
    previous_candidate = directory / 'candidate'
    old_report = isolated.read_json(previous_candidate / 'bundle-report.json')
    if isolated.digest(previous_candidate / 'bundle-report.json') != previous['candidate']['bundle_report_sha256']:
        raise ValueError('Previous bundle report changed')
    for name, expected in old_report['files'].items():
        if isolated.digest(isolated.safe_path(previous_candidate, name)) != expected:
            raise ValueError('Previous candidate changed: ' + name)
    runtime = isolated.read_json(directory / 'stage/behavior_packs/ichiyon_junket_bp/scripts/all17_original_generated.json')
    games = runtime if isinstance(runtime, list) else runtime['venues']
    reuse = validate_previous(previous, directory, operation, previous_sha, games)
    for pack, expected in previous['packs'].items():
        if isolated.tree_digest(directory / 'stage' / pack) != expected or isolated.tree_digest(directory / 'data' / pack) != expected:
            raise ValueError('Stopped clone pack contents changed: ' + pack)
    evidence = directory / ('restart-recheck-' + uuid.uuid4().hex)
    evidence.mkdir(mode=0o700)
    server.evidence_directory = evidence
    proof = {'scope': 'isolated_fixture_restart_recheck', 'operation_id': operation,
             'directory': str(directory), 'evidence_directory': str(evidence), 'container': server.name,
             'started_at': checker.utc_now(), 'production_modified': False, 'player_test': False,
             'mobile_test': False, 'full_match': False, 'issues': [], 'restart_snapshot_verified': False,
             'prior_proof': {'path': str(previous_proof_path), 'sha256': previous_proof_sha}, 'reused_evidence': reuse}
    saved_container = checker.CONTAINER
    started = False
    try:
        candidate = evidence / 'candidate'
        proof['candidate'] = isolated.extract_candidate(Path(candidate_bundle), candidate_sha, candidate)
        proof['candidate_delta'] = compare_candidate(previous_candidate, candidate)
        # Snapshot the clone's old pack stage; no live world is copied or opened.
        shutil.copytree(directory / 'stage', evidence / 'stage-before')
        server.inspect_owned()
        if server.inspect_owned()['State']['Running']:
            raise ValueError('Clone started before candidate installation')
        for pack in isolated.JUNKET_PACKS:
            target = isolated.safe_path(directory / 'stage', pack)
            if not target.resolve().is_relative_to((directory / 'stage').resolve()):
                raise ValueError('Stage target escaped the owned clone')
            shutil.rmtree(target)
            shutil.copytree(candidate / 'packs' / pack, target)
        installer = isolated.PackApplications(SimpleNamespace(PROJECT_DIR=directory, DATA_DIR=directory / 'data', WORLD_NAME=isolated.WORLD))
        installer._install(directory)
        for pack, expected in previous['packs'].items():
            wanted = isolated.tree_digest(candidate / 'packs' / pack) if pack in isolated.JUNKET_PACKS else expected
            if isolated.tree_digest(directory / 'stage' / pack) != wanted or isolated.tree_digest(directory / 'data' / pack) != wanted:
                raise ValueError('Clone-only pack install mismatch: ' + pack)
        checker.CONTAINER = server.name
        server.since = checker.utc_now()
        started = True
        isolated.docker('start', server.name)
        proof['startup'] = server.wait_ready()
        proof['snapshot'] = checker.check_resilience(evidence, operation, timeout=timeout,
            restart_callback=server.restart, snapshot_only=True, runtime_directory=directory)
        proof['issues'].extend(proof['snapshot']['issues'])
        proof['restart_snapshot_verified'] = proof['snapshot'].get('restart_snapshot_verified') is True
    except Exception as exc:
        proof['issues'].append(str(exc))
    finally:
        if started:
            try:
                proof['final_stop'] = server.cleanup()
                if proof['final_stop'].get('graceful_stop') is not True:
                    proof['issues'].append('Final clone stop was not confirmed graceful')
            except Exception as exc:
                proof['issues'].append('Clone cleanup: ' + str(exc))
        checker.CONTAINER = saved_container
        proof['prior_proof_unchanged'] = isolated.digest(previous_proof_path) == previous_proof_sha
        if not proof['prior_proof_unchanged']:
            proof['issues'].append('Previous failed proof was modified')
        proof['restart_snapshot_verified'] &= not proof['issues']
        proof['completed_at'] = checker.utc_now()
        isolated.write_json(evidence / 'restart-recheck-proof.json', proof)
    return proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--clone', type=Path, required=True)
    parser.add_argument('--operation', required=True)
    parser.add_argument('--previous-candidate-sha256', required=True)
    parser.add_argument('--candidate-bundle', type=Path, required=True)
    parser.add_argument('--candidate-sha256', required=True)
    parser.add_argument('--timeout', type=int, default=1200)
    args = parser.parse_args()
    if not 60 <= args.timeout <= 3600:
        parser.error('timeout must be 60..3600 seconds per phase')
    proof = run(args.clone, args.operation, args.previous_candidate_sha256,
                args.candidate_bundle, args.candidate_sha256, args.timeout)
    print(json.dumps(proof, ensure_ascii=False))
    return 0 if proof['restart_snapshot_verified'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
