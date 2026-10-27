"""Read the actual BDS startup report after a completed native migration.

This checks server-side fixture initialization, never a client playthrough.
Only --warp-check sends the explicit server self-test event. That test creates
temporary test actors and loads destination chunks; it does not move players.
"""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

from build_junket_warp import EXPECTED_IDS

ROOT = Path('/home/ubuntu/minecraft-bedrock-creative/junket-world-applications')
CONTAINER = 'minecraft-bedrock-creative'
WARP_MARKER = '[JUNKET WARP] destination proof: '
WARP_PROGRESS = '[JUNKET WARP] destination progress: '
QUALITY_MARKER = '[JUNKET QUALITY] proof: '
HEALTH_RECONCILE_MARKER = '[JUNKET] fixed fixture health reconcile: '
DAMAGE_MARKER = '[JUNKET] fixed fixture damage: '
QUALITY_STAGES = ('entry', 'reception', 'start', 'transition', 'finished', 'rematch', 'exit', 'reentry', 'reinitialize')
RESILIENCE_MARKER = '[JUNKET RESILIENCE] proof: '
PANEL_MARKER = '[JUNKET PANEL] initialized: '


def panel_initialization_evidence(output):
    latest = {}
    for line in output.splitlines():
        if PANEL_MARKER in line:
            record = json.JSONDecoder().raw_decode(line.split(PANEL_MARKER, 1)[1])[0]
            latest[record['id']] = record
    valid = set(latest) == set(EXPECTED_IDS)
    for record in latest.values():
        legacy = record.get('legacy', [])
        valid &= (record.get('controls') == 1 and record.get('handler_bound') is True
                  and record.get('originalSupportsPreserved') is True and len(legacy) == 4
                  and {entry.get('action') for entry in legacy} == {'reception', 'return', 'reset', 'repair'}
                  and all(entry.get('status') in ('restored', 'protected') for entry in legacy))
    return {'verified': bool(valid), 'venues': list(latest.values()), 'player_input_test': False}


def resilience_path_counts(games):
    """Count precisely the declared cells visited by checkAccessPaths in BDS."""
    if (len(games) != len(EXPECTED_IDS)
            or {game.get('id') for game in games} != set(EXPECTED_IDS)):
        raise ValueError('Expected paths for 17 distinct known venues')
    counts = {}
    for game in games:
        controls = game.get('service_controls', [])
        if (len(controls) != 1 or controls[0].get('action') != 'menu'
                or not isinstance(controls[0].get('path'), list) or not controls[0]['path']):
            raise ValueError(game['id'] + ': declared panel access path is missing')
        path = controls[0]['path']
        if any(not isinstance(cell, list) or len(cell) != 3
               or any(type(value) not in (int, float) or not math.isfinite(value) for value in cell)
               for cell in path):
            raise ValueError(game['id'] + ': declared panel access path is invalid')
        counts[game['id']] = len(path)
    return counts


def validate_resilience_report(report, request_id, expected_counts, phase='destructive', *, expected_path_counts):
    if not isinstance(report, dict) or report.get('request_id') != request_id or report.get('phase') != phase:
        return ['No matching completed resilience report']
    issues = []
    if (set(expected_path_counts) != set(EXPECTED_IDS)
            or any(type(count) is not int or count <= 0 for count in expected_path_counts.values())):
        return ['Expected positive path counts for 17 distinct known venues']
    if report.get('ok') is not True or report.get('failures') != []:
        issues.append('Server resilience probe failed')
    if report.get('player_test') is not False or report.get('mobile_test') is not False:
        issues.append('Server probe must not claim a player or mobile test')
    checks = report.get('checks', [])
    if (not isinstance(checks, list) or len(checks) != 17 or not all(isinstance(c, dict) for c in checks)
            or {c.get('id') for c in checks} != set(EXPECTED_IDS)):
        return issues + ['Expected 17 distinct resilience checks']
    for check in checks:
        gid = check['id']
        if check.get('ok') is not True or check.get('fixture_count') != expected_counts.get(gid):
            issues.append(gid + ': failed fixture validation')
        access = check.get('access', {})
        if (access.get('server_actor_collision_checks') is not True or access.get('player_walk_test') is not False
                or type(access.get('checked_path_cells')) is not int
                or access['checked_path_cells'] != expected_path_counts[gid]):
            issues.append(gid + ': actual server collision checks incomplete')
        controls = check.get('controls', [])
        if (not isinstance(controls, list) or len(controls) != 1
                or {c.get('action') for c in controls} != {'menu'}
                or any(c.get('stand_clear') is not True or not c.get('sign_text') for c in controls)):
            issues.append(gid + ': controls/access evidence incomplete')
        for control in controls:
            legacy = control.get('legacy_controls', [])
            if (control.get('handler_bound') is not True or len(legacy) != 4
                    or {c.get('action') for c in legacy} != {'reception', 'return', 'reset', 'repair'}
                    or any(c.get('status') not in ('restored', 'protected') for c in legacy)):
                issues.append(gid + ': panel binding or legacy migration incomplete')
        if phase == 'restart':
            if check.get('restart_persistence') is not True:
                issues.append(gid + ': restart persistence unverified')
            continue
        attack, physics, recovery = (check.get(k, {}) for k in ('attack', 'physics', 'recovery'))
        if type(attack.get('tested')) is not int or attack['tested'] < 2 or attack.get('health_identity_position_preserved') is not True:
            issues.append(gid + ': attack/health/location evidence incomplete')
        if (physics.get('tested') != attack.get('tested') or physics.get('anchor_position_restored') is not True
                or physics.get('final_velocity_zero') is not True or type(physics.get('immediate_velocity_max')) not in (int, float)):
            issues.append(gid + ': impulse/velocity evidence incomplete')
        if any(recovery.get(field) is not True for field in ('deleted_restored', 'displacement_restored',
                'duplicates_removed', 'original_block_restored', 'controls_restored', 'foreign_actor_preserved')):
            issues.append(gid + ': destructive recovery evidence incomplete')
        if check.get('idempotent') is not True:
            issues.append(gid + ': repeated recovery unverified')
    return issues


def validate_snapshot_report(report, request_id, expected_roles, phase):
    """A read-only checkpoint is not evidence of attacks/recovery or gameplay."""
    if (phase not in ('snapshot', 'snapshot_restart') or not isinstance(report, dict)
            or report.get('request_id') != request_id or report.get('phase') != phase
            or report.get('scope') != 'fixture_snapshot_only'):
        return ['No matching completed snapshot-only report']
    if set(expected_roles) != set(EXPECTED_IDS):
        return ['Expected fixture roles for every known venue']
    issues = []
    if report.get('ok') is not True or report.get('failures') != []:
        issues.append('Server fixture snapshot failed')
    if any(report.get(field) is not False for field in ('player_test', 'mobile_test', 'full_match')):
        issues.append('Snapshot must not claim a player/mobile/full-match test')
    checks = report.get('checks', [])
    if (not isinstance(checks, list) or len(checks) != 17 or not all(isinstance(c, dict) for c in checks)
            or {c.get('id') for c in checks} != set(EXPECTED_IDS)):
        return issues + ['Expected 17 distinct snapshot checks']
    seen = set()
    for check in checks:
        gid = check['id']
        identities = check.get('identities', {})
        if (check.get('ok') is not True or check.get('snapshot_only') is not True
                or check.get('fixture_count') != len(expected_roles[gid])
                or not isinstance(identities, dict) or set(identities) != set(expected_roles[gid])
                or any(not isinstance(value, str) or not value for value in identities.values())):
            issues.append(gid + ': complete fixture identity snapshot required')
            continue
        if len(set(identities.values())) != len(identities) or seen.intersection(identities.values()):
            issues.append(gid + ': duplicate fixture identity')
        seen.update(identities.values())
        if phase == 'snapshot_restart' and check.get('restart_persistence') is not True:
            issues.append(gid + ': restart persistence unverified')
        readiness = check.get('actor_readiness', {}).get(phase, {})
        if readiness.get('settled') is not True:
            issues.append(gid + ': actor readiness did not settle')
        if any(key in check for key in ('attack', 'physics', 'damage_injected', 'recovery', 'idempotent')):
            issues.append(gid + ': snapshot contains destructive-test claims')
    return issues


def check_resilience(directory, operation, timeout=1200, restart_callback=None, *, snapshot_only=False, runtime_directory=None):
    request_id = str(uuid.uuid4())
    runtime = json.loads(((runtime_directory or directory) / 'stage/behavior_packs/ichiyon_junket_bp/scripts/all17_original_generated.json').read_text(encoding='utf-8-sig'))
    games = runtime if isinstance(runtime, list) else runtime['venues']
    counts = {g['id']: len(g['fixtures']) for g in games}
    path_counts = resilience_path_counts(games)
    roles = {g['id']: [fixture['role'] for fixture in g['fixtures']] for g in games}
    evidence_prefix = 'restart-snapshot' if snapshot_only else 'resilience'
    if snapshot_only and restart_callback is None:
        raise ValueError('Snapshot retest requires an explicitly owned isolated restart callback')
    proof = {'operation_id': operation, 'request_id': request_id, 'server_resilience_verified': False,
             'player_test': False, 'mobile_test': False, 'issues': [], 'reports': {}, 'restart': None,
             'expected_path_cells': path_counts}
    proof['scope'] = 'fixture_snapshot_only' if snapshot_only else 'fixture_resilience'
    before = collect_tickingareas(directory, evidence_prefix + '-before')
    proof['tickingareas_before'] = before
    try:
        for phase in (('snapshot', 'snapshot_restart') if snapshot_only else ('destructive', 'restart')):
            if phase in ('restart', 'snapshot_restart'):
                # Reuse the existing managed lock and graceful stop/start. This
                # is one requested restart test, not another pack deployment.
                if restart_callback is not None:
                    proof['restart'] = restart_callback()
                else:
                    sys.path.insert(0, '/opt/ichiyon-minecraft-control')
                    import minecraft_control_api as api
                    from minecraft_cosmetics_apply import PackApplications, deployment_lock, TERMINAL
                    if str(api.PROJECT_DIR) != '/home/ubuntu/minecraft-bedrock-creative' or api.WORLD_NAME != 'ichiyon-creative-flat':
                        raise ValueError('Unexpected restart target')
                    with deployment_lock():
                        application = PackApplications(api)
                        active = application._read('active.json', {})
                        latest = application._read('latest.json', {})
                        if active.get('operation_id') != operation or latest.get('status') not in TERMINAL:
                            raise RuntimeError('Managed deployment changed before restart test')
                        stopped = False
                        try:
                            application._stop()
                            stopped = True
                            application._start()
                        except Exception:
                            if stopped:
                                application._start()
                            raise
                    proof['restart'] = {'graceful_stop': True, 'healthy_start': True, 'same_operation': operation}
                # The regular bootstrap owns initialization. Wait for its final
                # report before checking persisted entity identities.
                since = utc_now()
                send_console('scriptevent junket:installall')
                deadline = time.monotonic() + 240
                while True:
                    output = read_logs(since)
                    report, _ = parse_runtime_reports(output)
                    if report and report.get('ok') is True and len(report.get('completed', [])) == 17:
                        break
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Restart fixture initialization did not complete')
                    time.sleep(2)
            since = utc_now()
            send_console('scriptevent junket:resiliencecheck ' + phase + ' ' + request_id)
            deadline = time.monotonic() + timeout
            report = None
            while True:
                output = read_logs(since)
                (directory / ('resilience-' + phase + '.log')).write_text(output, encoding='utf-8')
                for line in output.splitlines():
                    if RESILIENCE_MARKER in line:
                        candidate = json.JSONDecoder().raw_decode(line.split(RESILIENCE_MARKER, 1)[1])[0]
                        if candidate.get('request_id') == request_id and candidate.get('phase') == phase:
                            report = candidate
                if report is not None or time.monotonic() >= deadline:
                    break
                time.sleep(2)
            proof['reports'][phase] = report
            if snapshot_only:
                proof['issues'].extend(validate_snapshot_report(report, request_id, roles, phase))
                if phase == 'snapshot_restart' and not proof['issues']:
                    expected = {row['id']: row['identities'] for row in proof['reports']['snapshot']['checks']}
                    actual = {row['id']: row['identities'] for row in report['checks']}
                    if expected != actual:
                        proof['issues'].append('Fixture identities changed across restart')
            else:
                proof['issues'].extend(validate_resilience_report(report, request_id, counts, phase,
                                                                  expected_path_counts=path_counts))
            errors = runtime_errors(output)
            if errors:
                proof['issues'].extend(errors)
            if proof['issues']:
                break
    except Exception as exc:
        proof['issues'].append(str(exc))
    finally:
        try:
            proof['tickingareas_after'] = collect_tickingareas(directory, evidence_prefix + '-after')
            if proof['tickingareas_after'] != before:
                proof['issues'].append('Tickingareas changed during resilience/restart test')
        except Exception as exc:
            proof['issues'].append(str(exc))
    passed = not proof['issues'] and proof['restart'] is not None
    proof['server_resilience_verified'] = passed and not snapshot_only
    if snapshot_only:
        proof['restart_snapshot_verified'] = passed
    (directory / (evidence_prefix + '-runtime-proof.json')).write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')
    return proof


def validate_quality_report(report, request_id, expected_counts):
    issues = []
    if not isinstance(report, dict) or report.get('schema') != 1 or report.get('request_id') != request_id:
        return ['No matching completed quality report']
    if report.get('ok') is not True or report.get('failures') != []:
        issues.append('Server quality probe failed')
    if report.get('player_test') is not False:
        issues.append('Quality probe must not claim a player test')
    checks = report.get('checks', [])
    if (not isinstance(checks, list) or len(checks) != 17
            or not all(isinstance(item, dict) for item in checks)
            or {item.get('id') for item in checks} != set(EXPECTED_IDS)):
        return issues + ['Expected 17 distinct quality checks']
    for item in checks:
        gid = item['id']
        if report.get('rules_schema') == 1:
            trace = item.get('rule_progression', {})
            terminal = trace.get('phase', trace.get('terminal', {}).get('phase'))
            identity = trace.get('game', trace.get('gameId'))
            if (identity != gid or terminal != 'finished' or trace.get('client_play') is True
                    or not isinstance(trace.get('actions'), int) or trace['actions'] < 0):
                issues.append(gid + ': missing installed-reducer start-to-finish proof')
        if item.get('fixture_count') != expected_counts.get(gid):
            issues.append(gid + ': fixture count differs from bundle')
        for field in ('identities_preserved', 'health_preserved', 'labels_valid', 'sentinel_preserved', 'cleaned'):
            if item.get(field) is not True:
                issues.append(gid + ': ' + field + ' not verified')
        if type(item.get('health_recoveries_during_test')) is not int or item['health_recoveries_during_test'] != 0:
            issues.append(gid + ': health recovery must not mask lifecycle damage')
        stages = item.get('stages', [])
        if (not isinstance(stages, list) or len(stages) != len(QUALITY_STAGES)
                or not all(isinstance(stage, dict) for stage in stages)
                or {stage.get('name') for stage in stages} != set(QUALITY_STAGES)
                or any(stage.get('ok') is not True for stage in stages)):
            issues.append(gid + ': incomplete lifecycle stages')
    return issues


def check_quality(directory, operation, timeout=720):
    request_id = str(uuid.uuid4())
    proof = {'operation_id': operation, 'request_id': request_id, 'server_quality_verified': False,
             'player_test': False, 'report': None, 'issues': [], 'scripting_error_lines': [],
             'tickingareas_before': None, 'tickingareas_after': None, 'tickingareas_preserved': False}
    since = None
    try:
        runtime = json.loads((directory / 'stage/behavior_packs/ichiyon_junket_bp/scripts/all17_original_generated.json').read_text(encoding='utf-8-sig'))
        games = runtime if isinstance(runtime, list) else runtime.get('venues', runtime.get('games', []))
        counts = {game['id']: len(game['fixtures']) for game in games}
        proof['tickingareas_before'] = collect_tickingareas(directory, 'quality-before')
        since = utc_now()
        send_console('scriptevent junket:qualitycheck ' + request_id)
        deadline = time.monotonic() + timeout
        while True:
            output = read_logs(since)
            (directory / 'quality-runtime.log').write_text(output, encoding='utf-8')
            for line in output.splitlines():
                if QUALITY_MARKER in line:
                    candidate = json.JSONDecoder().raw_decode(line.split(QUALITY_MARKER, 1)[1])[0]
                    if candidate.get('request_id') == request_id:
                        proof['report'] = candidate
            if proof['report'] is not None or time.monotonic() >= deadline:
                break
            time.sleep(2)
        proof['issues'].extend(validate_quality_report(proof['report'], request_id, counts))
    except Exception as exc:
        proof['issues'].append(str(exc))
    finally:
        if proof['tickingareas_before'] is not None:
            try:
                proof['tickingareas_after'] = collect_tickingareas(directory, 'quality-after')
                proof['tickingareas_preserved'] = proof['tickingareas_before'] == proof['tickingareas_after']
                if not proof['tickingareas_preserved']:
                    proof['issues'].append('Tickingarea listings changed during quality test')
            except Exception as exc:
                proof['issues'].append(str(exc))
        if since is not None:
            try:
                output = read_logs(since)
                (directory / 'quality-runtime.log').write_text(output, encoding='utf-8')
                proof['scripting_error_lines'] = runtime_errors(output)
                if proof['scripting_error_lines']:
                    proof['issues'].append('Actor or Scripting errors occurred during quality test')
            except Exception as exc:
                proof['issues'].append(str(exc))
    proof['server_quality_verified'] = not proof['issues'] and proof['tickingareas_preserved']
    (directory / 'quality-runtime-proof.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')
    return proof


def runtime_errors(output):
    return [line for line in output.splitlines()
            if (' ERROR]' in line and any(f'[{kind}]' in line for kind in ('Scripting', 'Actor', 'Item', 'Json')))
            or any(f'[{kind}][error]' in line for kind in ('Scripting', 'Actor', 'Item', 'Json'))
            or '[JUNKET ORIGINAL] bootstrap:' in line or '[JUNKET ORIGINAL] fixture setup:' in line]


def read_logs(since):
    result = subprocess.run(['docker', 'logs', '--since', since, CONTAINER],
                            text=True, capture_output=True, timeout=20, check=True)
    return result.stdout + result.stderr


def send_console(command):
    """Use the native release adapter's fixed-container console transport."""
    result = subprocess.run(['docker', 'top', CONTAINER, '-eo', 'pid,comm'],
                            text=True, capture_output=True, timeout=20, check=True)
    rows = [line.split() for line in result.stdout.splitlines()[1:]]
    pids = [row[0] for row in rows if len(row) == 2 and row[0].isdigit()
            and row[1].startswith('bedrock_server')]
    if len(pids) != 1:
        raise RuntimeError('Unable to identify this container BDS console')
    result = subprocess.run(['sudo', 'tee', '/proc/' + pids[0] + '/fd/0'],
                            input=command + '\n', text=True, capture_output=True, timeout=20)
    if result.returncode:
        raise RuntimeError('BDS console submission failed: ' + result.stderr[-500:])
    # Submission alone never establishes that BDS executed the command.


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def parse_tickingarea_listing(output):
    """Normalize the actual console list, retaining dimension/name/coordinates."""
    headers, entries, summary = [], [], []
    dimension = None
    listing_started = False
    reported_count = None
    for raw in output.splitlines():
        # Docker's captured console stream can prefix a BDS line with NUL.
        # Strip framing bytes only at the start; never alter area names/data.
        line = re.sub(r'\x1b\[[0-9;]*m', '', raw.lstrip('\x00')).strip()
        line = re.sub(r'\u00a7[0-9a-z]', '', line, flags=re.I)
        line = re.sub(r'^\[(?:\d{4}-[^\]]*|INFO|WARN|ERROR)\]\s*', '', line).strip()
        line = ' '.join(line.split())
        count = re.fullmatch(r'(?:There are\s+)?(\d+)(?:/\d+)?\s+ticking areas?(?:\s+in use)?\.?', line, re.I)
        if re.match(r'List of all ticking areas\b', line, re.I):
            listing_started = True
        elif re.match(r'ticking\s+areas?\s+in\b', line, re.I) or (
                listing_started and re.fullmatch(r'[\w .:/-]+:', line)):
            listing_started = True
            dimension = line
            headers.append(line)
        elif re.search(r'(?:there are )?no ticking areas', line, re.I):
            reported_count = 0
            summary.append(line)
        elif count:
            reported_count = int(count.group(1))
            summary.append(line)
        elif dimension and not line.startswith('[') and ':' in line and re.search(r'-?\d+', line):
            entries.append(dimension + ' | ' + line)
    # The final count proves the whole multi-line reply was collected. Never
    # accept a header, truncated entries, or an unrecognized entry silently.
    if reported_count is None or len(entries) != reported_count:
        return None
    return {'headers': sorted(headers), 'entries': sorted(entries), 'summary': sorted(summary),
            'reported_count': reported_count}


def collect_tickingareas(directory, label, timeout=20):
    since = utc_now()
    send_console('tickingarea list all-dimensions')
    deadline = time.monotonic() + timeout
    previous = None
    while True:
        output = read_logs(since)
        (directory / ('warp-tickingareas-' + label + '.log')).write_text(output, encoding='utf-8')
        listing = parse_tickingarea_listing(output)
        # Wait for a complete, stable response instead of accepting tee's exit.
        if listing is not None and listing == previous:
            return listing
        previous = listing
        if time.monotonic() >= deadline:
            raise RuntimeError('Unrecognized or incomplete tickingarea list: ' + label)
        time.sleep(1)


def parse_warp_reports(output, request_id):
    report, progress = None, {}
    for line in output.splitlines():
        marker = WARP_MARKER if WARP_MARKER in line else WARP_PROGRESS if WARP_PROGRESS in line else None
        if marker is None:
            continue
        item = json.JSONDecoder().raw_decode(line.split(marker, 1)[1])[0]
        if item.get('request_id') != request_id:
            continue
        if marker == WARP_MARKER:
            report = item
        else:
            progress[item['id']] = item
    return report, list(progress.values())


def validate_warp_report(report, request_id, destinations):
    issues = []
    if not isinstance(report, dict):
        return ['No completed destination report for this request']
    if report.get('schema') != 1 or report.get('request_id') != request_id:
        issues.append('Unexpected destination report schema or request')
    if report.get('ok') is not True or report.get('failures') != []:
        issues.append('The server reported destination failures')
    if report.get('player_test') is not False or report.get('test_entity') != 'minecraft:armor_stand':
        issues.append('Unexpected test actor or player-test claim')
    checks = report.get('checks', [])
    if (not isinstance(checks, list) or len(checks) != 17 or not all(isinstance(item, dict) for item in checks)
            or {item.get('id') for item in checks} != set(EXPECTED_IDS)):
        return issues + ['Expected exactly 17 distinct game destination checks']
    for item in checks:
        gid = item['id']
        for field in ('loaded_after', 'safe', 'teleported', 'cleaned'):
            if item.get(field) is not True:
                issues.append(gid + ': ' + field + ' was not confirmed')
        if not isinstance(item.get('loaded_before'), bool):
            issues.append(gid + ': missing initial chunk-loading evidence')
        actual = item.get('location', {})
        expected = destinations.get(gid)
        if (expected is None or not isinstance(actual, dict)
                or any(type(actual.get(axis)) not in (int, float) or not math.isfinite(actual[axis])
                       or abs(actual[axis] - expected[axis]) > 0.01 for axis in 'xyz')):
            issues.append(gid + ': actual teleport coordinates differ from the staged destination')
    return issues


def check_warp_destinations(directory, operation, timeout=480):
    request_id = str(uuid.uuid4())
    proof = {'operation_id': operation, 'request_id': request_id,
             'server_warp_destinations_verified': False, 'player_test': False,
             'external_client_connection_verified': False, 'two_player_playthrough': False,
             'client_rendering_verified': False, 'tickingareas_before': None,
             'tickingareas_after': None, 'tickingareas_preserved': False,
             'report': None, 'progress': [], 'issues': [], 'scripting_error_lines': []}
    since = None
    try:
        runtime = json.loads((directory / 'stage/behavior_packs/ichiyon_junket_bp/scripts/all17_original_generated.json').read_text(encoding='utf-8-sig'))
        games = runtime if isinstance(runtime, list) else runtime.get('venues', runtime.get('games', []))
        destinations = {game['id']: game['destination'] for game in games}
        bank_config = directory / 'stage/behavior_packs/ichiyon_junket_bp/scripts/bank_hub_generated.js'
        bank = None
        if bank_config.is_file():
            bank = json.loads(bank_config.read_text(encoding='utf-8').split('=', 1)[1].strip().rstrip(';'))
            if not bank.get('enabled'):
                bank = None
        proof['tickingareas_before'] = collect_tickingareas(directory, 'before')
        since = utc_now()
        send_console(('scriptevent junket:bankcheck ' if bank else 'scriptevent junket:warpcheck all ') + request_id)
        deadline = time.monotonic() + timeout
        while True:
            output = read_logs(since)
            (directory / 'warp-runtime.log').write_text(output, encoding='utf-8')
            proof['report'], proof['progress'] = parse_warp_reports(output, request_id)
            if bank:
                for line in output.splitlines():
                    if '[JUNKET_BANK_CHECK] ' in line:
                        record = json.JSONDecoder().raw_decode(line.split('[JUNKET_BANK_CHECK] ', 1)[1])[0]
                        if record.get('request_id') == request_id:
                            proof['bank'] = record
            if (proof['report'] is not None and (not bank or proof.get('bank'))) or time.monotonic() >= deadline:
                break
            time.sleep(2)
        proof['issues'].extend(validate_warp_report(proof['report'], request_id, destinations))
        if bank:
            record = proof.get('bank', {})
            arrival = record.get('return', {})
            at = arrival.get('location', {})
            if (record.get('ok') is not True or arrival.get('safe') is not True or arrival.get('teleported') is not True
                    or [at.get(k) for k in 'xyz'] != bank['arrival']['location']
                    or record.get('repeated', {}).get('added') != 0):
                proof['issues'].append('Bank fixtures/return/repeated initialization not verified')
            (directory / 'bank-runtime-proof.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception as exc:
        proof['issues'].append(str(exc))
    finally:
        if proof['tickingareas_before'] is not None:
            try:
                proof['tickingareas_after'] = collect_tickingareas(directory, 'after')
                proof['tickingareas_preserved'] = proof['tickingareas_before'] == proof['tickingareas_after']
                if not proof['tickingareas_preserved']:
                    proof['issues'].append('Tickingarea listings changed during the test')
            except Exception as exc:
                proof['issues'].append(str(exc))
        if since is not None:
            try:
                output = read_logs(since)
                (directory / 'warp-runtime.log').write_text(output, encoding='utf-8')
                proof['scripting_error_lines'] = runtime_errors(output)
                if proof['scripting_error_lines']:
                    proof['issues'].append('Actor or Scripting errors occurred during the test')
            except Exception as exc:
                proof['issues'].append(str(exc))
    checks = (proof['report'] or {}).get('checks', [])
    if not isinstance(checks, list) or not all(isinstance(item, dict) for item in checks):
        checks = []
    proof['initially_unloaded_game_ids'] = [item['id'] for item in checks if item.get('loaded_before') is False]
    proof['initially_unloaded_game_count'] = len(proof['initially_unloaded_game_ids'])
    proof['cold_destination_load_observed'] = bool(proof['initially_unloaded_game_ids'])
    proof['all17_initially_unloaded_verified'] = (len(checks) == 17
        and proof['initially_unloaded_game_count'] == 17
        and set(proof['initially_unloaded_game_ids']) == set(EXPECTED_IDS))
    proof['test_entities_removed'] = len(checks) == 17 and all(item.get('cleaned') is True for item in checks)
    proof['server_warp_destinations_verified'] = not proof['issues'] and proof['tickingareas_preserved']
    (directory / 'warp-runtime-proof.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')
    return proof


def parse_runtime_reports(output):
    """Keep partial evidence without promoting it to a completed installation."""
    report = None
    progress = {}
    for line in output.splitlines():
        marker = '[JUNKET ORIGINAL] runtime fixtures initialized: '
        progress_marker = '[JUNKET ORIGINAL] runtime fixture progress: '
        if marker in line:
            report = json.JSONDecoder().raw_decode(line.split(marker, 1)[1])[0]
        elif progress_marker in line:
            item = json.JSONDecoder().raw_decode(line.split(progress_marker, 1)[1])[0]
            progress[item['id']] = item
    return report, list(progress.values())


def parse_fixture_health_reconciliations(output, marker=HEALTH_RECONCILE_MARKER):
    records = []
    for line in output.splitlines():
        if marker in line:
            records.append(json.JSONDecoder().raw_decode(line.split(marker, 1)[1])[0])
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--operation', required=True)
    parser.add_argument('--warp-check', action='store_true', help='Run the explicit BDS destination/temporary-actor self-test')
    parser.add_argument('--quality-check', action='store_true', help='Exercise fixture lifecycles with synthetic seats; requires no players or matches')
    parser.add_argument('--resilience-check', action='store_true', help='Attack/displace/delete/recover original venues, then test one graceful BDS restart; empty server only')
    args = parser.parse_args()
    operation = str(uuid.UUID(args.operation))
    directory = ROOT / operation
    release = json.loads((directory / 'native-release-proof.json').read_text())
    if release['operation_id'] != operation or release['status'] != 'succeeded':
        raise ValueError('A succeeded native migration is required')
    since = release['runtime']['container']['started_at']
    deadline = time.monotonic() + 240
    report = None
    while True:
        output = read_logs(since)
        report, progress = parse_runtime_reports(output)
        if report is not None or time.monotonic() >= deadline:
            break
        time.sleep(5)
    (directory / 'runtime-startup.log').write_text(output, encoding='utf-8')
    completed = report.get('completed', []) if report else []
    errors = runtime_errors(output)
    manifest = json.loads((directory / 'stage/behavior_packs/ichiyon_junket_bp/scripts/all17_original_generated.json').read_text(encoding='utf-8-sig'))
    games = manifest if isinstance(manifest, list) else manifest['venues']
    panel_required = any(game.get('legacy_service_controls') for game in games)
    panels = panel_initialization_evidence(output)
    proof = {'operation_id': operation, 'server_started_at': since,
             'server_fixture_initialization': bool(report and report.get('ok') is True
                 and len(completed) == 17 and {g['id'] for g in completed} == set(EXPECTED_IDS)),
             'fixture_report': report, 'fixture_progress': progress,
             'fixture_health_reconciliations': parse_fixture_health_reconciliations(output),
             'fixed_fixture_damage': parse_fixture_health_reconciliations(output, DAMAGE_MARKER),
             'initialization_report_complete': report is not None,
             'service_panels_required': panel_required, 'service_panels': panels,
             'scripting_error_lines': errors,
             'transport_configuration_error': 'TRANSPORT TYPE ERROR' in output,
             'external_client_connection_verified': False,
             'two_player_playthrough': False, 'client_rendering_verified': False}
    (directory / 'runtime-proof.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(proof, ensure_ascii=False), flush=True)
    if not proof['server_fixture_initialization'] or errors or (panel_required and not panels['verified']):
        raise SystemExit(1)
    if args.quality_check:
        quality_proof = check_quality(directory, operation)
        print(json.dumps(quality_proof, ensure_ascii=False), flush=True)
        if not quality_proof['server_quality_verified']:
            raise SystemExit(1)
    if args.warp_check:
        warp_proof = check_warp_destinations(directory, operation)
        print(json.dumps(warp_proof, ensure_ascii=False), flush=True)
        if not warp_proof['server_warp_destinations_verified']:
            raise SystemExit(1)
    if args.resilience_check:
        resilience_proof = check_resilience(directory, operation)
        print(json.dumps(resilience_proof, ensure_ascii=False), flush=True)
        if not resilience_proof['server_resilience_verified']:
            raise SystemExit(1)


if __name__ == '__main__':
    main()
