"""Private bank + companion candidate on the existing stopped-backup BDS runner.

The adapter only changes its disposable clone. Container lifecycle, runtime
pinning, world extraction, all17 checks and graceful restart stay in the existing
isolated-resilience helper. This module has no production stop/deploy endpoint.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
import json
from pathlib import Path
import re
import shutil
import time
import uuid

import check_junket_isolated_resilience as base
from minecraft_cosmetics_apply import check_tree, content_hash, safe_path

BASELINE_PACKS = (
    'behavior_packs/ichiyon_avatar_bp', 'behavior_packs/import_structures',
    'behavior_packs/ichiyon_junket_bp', 'resource_packs/ichiyon_junket_rp',
    'resource_packs/ichiyon_core_rp', 'resource_packs/ichiyon_records_rp')
EXTRA_METADATA = {'bank-plan.json', 'bank-source.json', 'bank-entities.json', 'bank-heads-proof.json'}
EXTRA_PREFIXES = ('static-overlay/', 'assets/ichiyon_junket_original_bp/structures/junket/')
PROBE_MARKER = '[CompanionExpansionResult] '
BANK_MARKER = '[JUNKET_BANK_CHECK] '


def area_identity(listing):
    # The remove command and explicit list command can both emit the same
    # count within a single second. Preserve all real entries/count evidence.
    return {**listing, 'summary': sorted(set(listing.get('summary', [])))}


def json_report(output, marker, request_id):
    found = []
    for line in output.splitlines():
        if marker not in line:
            continue
        try:
            value = json.loads(line.split(marker, 1)[1].strip())
        except (ValueError, TypeError):
            continue
        if value.get('request_id') == request_id:
            found.append(value)
    return found[-1] if found else None


def changed_pack_files(before, after):
    def inventory(root):
        check_tree(root)
        return {p.relative_to(root).as_posix():base.digest(p) for p in root.rglob('*') if p.is_file()}
    old,new=inventory(before),inventory(after)
    return sorted(path for path in set(old)|set(new) if old.get(path)!=new.get(path))


def validate_unchanged_junket_reuse(proof_path, directory, candidate, plan, structure):
    """Carry evidence, never turn unexecuted tests into new passes.

    The only permitted production change is the independently tested glasses
    effect-name normalizer. All other pack bytes and the native placement inputs
    must be identical to the completed isolated run whose evidence is reused.
    """
    proof_path=base.require_plain_path(proof_path)
    prior=base.read_json(proof_path)
    old_directory=base.require_plain_path(Path(prior['directory']))
    if (not old_directory.resolve().is_relative_to(base.ISOLATED.resolve())
            or proof_path.resolve()!=old_directory/'isolated-resilience-proof.json'
            or prior.get('production_modified') is not False or prior.get('network')!='none'
            or prior.get('image')!=base.IMAGE or not prior.get('completed_at')
            or prior.get('final_stop',{}).get('graceful_stop') is not True):
        raise ValueError('Reuse requires a completed, gracefully stopped isolated native proof')
    previous_candidate=prior.get('candidate',{})
    if base.digest(Path(previous_candidate['path']))!=previous_candidate.get('sha256'):
        raise ValueError('Previous candidate archive no longer matches its verified fingerprint')
    differences={}
    for pack,expected in prior['packs'].items():
        old=old_directory/'stage'/pack;new=directory/'stage'/pack
        if base.tree_digest(old)!=expected:
            raise ValueError('Previous evidence stage was changed: '+pack)
        delta=changed_pack_files(old,new)
        if delta:
            if pack!='behavior_packs/import_structures' or delta!=['scripts/narita_glasses_core.js']:
                raise ValueError('JUNKET evidence reuse rejected changed files: '+pack+' '+str(delta))
            differences[pack]=delta
    if not differences:
        raise ValueError('No targeted glasses correction was found for the reuse path')
    for name in ('source-layouts.json','junket-catalog.json','bank-plan.json'):
        if base.digest(old_directory/'candidate'/name)!=base.digest(candidate/name):
            raise ValueError('Native source/layout input changed: '+name)
    old_bank=prior.get('extension_preparation',{}).get('bank',{}).get('bank',{})
    if (base.digest(structure)!=old_bank.get('source_structure_sha256')
            or plan.get('plan_sha256')!=old_bank.get('plan_sha256')):
        raise ValueError('Native bank structure or placement changed')
    runtime=base.read_json(directory/'stage/behavior_packs/ichiyon_junket_bp/scripts/all17_original_generated.json')
    games=runtime if isinstance(runtime,list) else runtime['venues']
    counts={g['id']:len(g['fixtures']) for g in games}
    paths=base.checker.resilience_path_counts(games)
    destinations={g['id']:g['destination'] for g in games}
    errors=base.checker.validate_quality_report(prior['quality'].get('report'),prior['quality'].get('request_id'),counts)
    resilience=prior['resilience']
    if (prior['quality'].get('server_quality_verified') is not True
            or prior['quality'].get('issues') or prior['quality'].get('scripting_error_lines')
            or resilience.get('server_resilience_verified') is not True or resilience.get('issues')
            or area_identity(prior['quality'].get('tickingareas_before',{}))!=area_identity(prior['quality'].get('tickingareas_after',{}))):
        errors.append('Prior quality/resilience had unresolved failures')
    for phase in ('destructive','restart'):
        errors+=base.checker.validate_resilience_report(resilience.get('reports',{}).get(phase),resilience.get('request_id'),counts,phase,expected_path_counts=paths)
    if (resilience.get('restart',{}).get('graceful_stop') is not True
            or not area_identity(resilience.get('tickingareas_before',{}))==area_identity(resilience.get('tickingareas_after',{}))):
        errors.append('Prior restart/area preservation evidence is incomplete')
    bank={}
    for phase in ('before_restart','after_restart'):
        extension=prior.get('extension_'+phase,{})
        report=extension.get('bank',{});bank[phase]=report
        # These old probe failures are unrelated to the bank evidence. Any
        # native/schema/unknown issue remains disqualifying.
        if set(extension.get('issues',[]))-{'Companion/effectAdd native probe did not pass','Expansion probe changed existing tickingarea listings'}:
            errors.append('Prior bank extension has an unresolved native issue: '+phase)
        errors+=base.checker.validate_warp_report(report.get('outbound'),report.get('request_id'),destinations)
        if (report.get('ok') is not True or report.get('return',{}).get('safe') is not True
                or report.get('return',{}).get('teleported') is not True
                or not area_identity(extension.get('tickingareas_before',{}))==area_identity(extension.get('tickingareas_after',{}))):
            errors.append('Prior bank or existing tickingarea evidence is incomplete: '+phase)
    if errors:
        raise ValueError('Prior bank/quality/resilience evidence cannot be reused: '+'; '.join(errors))
    metadata={'scope':'inherited_unchanged_junket_evidence','proof_path':str(proof_path),'proof_sha256':base.digest(proof_path),
        'candidate_sha256':previous_candidate['sha256'],'changed_files':differences,
        'bank':bank,'executed_in_this_run':False}
    inherited={}
    for key in ('quality','resilience'):
        inherited[key]=deepcopy(prior[key])
        inherited[key].update(scope=metadata['scope'],executed_in_this_run=False,inherited_proof_sha256=metadata['proof_sha256'])
    inherited['warp']={'report':bank['before_restart']['outbound'],'issues':[],'player_test':False,
        'server_warp_destinations_verified':True,'executed_in_this_run':False,'scope':metadata['scope'],
        'inherited_proof_sha256':metadata['proof_sha256']}
    return {**metadata,'checks':inherited}


class BankExpansionAdapter:
    def storage_fingerprint(self):
        """Bind every external adapter input before reusing completed evidence."""
        return {'schema': 1, 'kind': 'bank-expansion',
                'baseline_stage': base.tree_digest(self.baseline),
                'baseline_proof': self.baseline_proof,
                'probe_source_sha256': base.digest(self.probe_source),
                'reuse_proof_sha256': base.digest(self.reuse_path) if self.reuse_path is not None else None}

    def __init__(self, baseline_stage, baseline_proof, probe_source, reuse_proof=None):
        self.baseline = base.require_plain_path(Path(baseline_stage))
        self.baseline_proof = base.read_json(Path(baseline_proof))
        self.probe_source = base.require_plain_path(Path(probe_source))
        self.stage_evidence = {}
        self.world_evidence = {}
        self.candidate = None
        self.reuse_path = Path(reuse_proof) if reuse_proof else None
        self.reuse = None
        active = self.baseline_proof.get('records', {}).get('cosmetics-applications/active.json', self.baseline_proof)
        if not isinstance(active.get('packs'), list) or not isinstance(active.get('operation_id'), str):
            raise ValueError('Pinned active-pack baseline proof required')
        self.active = active
        self.baseline_id = str(uuid.UUID(active['operation_id']))
        # The stopped native backup predates subsequent managed releases. Load
        # every pinned active pack, including DB-managed skins, so that cloning
        # the old backup cannot roll back unrelated current managed content.
        self.expected = {item['path']: item for item in active['packs']}
        if not set(BASELINE_PACKS) <= set(self.expected):
            raise ValueError('Baseline proof is missing one of the six affected packs')
        if len(self.expected) != len(active['packs']):
            raise ValueError('Duplicate active baseline pack identities')
        self.baseline_packs = tuple(sorted(self.expected))
        for pack in self.baseline_packs:
            source = safe_path(self.baseline, pack)
            check_tree(source)
            if content_hash(source) != self.expected[pack]['content_hash']:
                raise ValueError('Active baseline pack hash differs: ' + pack)
        if not self.probe_source.is_file() or self.probe_source.name != 'structure_companions_bds_probe.js':
            raise ValueError('Explicit isolated companion probe source required')

    def extract_candidate(self, archive, sha256, destination):
        proof = base.extract_candidate(archive, sha256, destination,
            extra_metadata=EXTRA_METADATA, extra_prefixes=EXTRA_PREFIXES)
        for name in ('bank-plan.json', 'bank-source.json', 'static-overlay/manifest.json'):
            if not (destination / name).is_file():
                raise ValueError('Bank candidate missing ' + name)
        source = base.read_json(destination / 'bank-source.json')
        relative = source['structure_path'].replace('\\', '/')
        if not relative.startswith(EXTRA_PREFIXES[1]):
            raise ValueError('Bank structure must use its private fixed asset root')
        structure = safe_path(destination, relative)
        if base.digest(structure) != source['structure_sha256']:
            raise ValueError('Bank original structure hash mismatch')
        self.candidate = destination
        self.structure = structure
        self.plan = base.read_json(destination / 'bank-plan.json')
        proof['bank_source_sha256'] = base.digest(destination / 'bank-source.json')
        proof['static_overlay_manifest_sha256'] = base.digest(destination / 'static-overlay/manifest.json')
        return proof

    def install_stage(self, operation_dir, directory, packs, candidate=None):
        if candidate is None:
            raise ValueError('Bank expansion requires a complete candidate')
        def mutate(stage):
            if set(packs) != set(self.baseline_packs):
                raise ValueError('Current active pack inventory differs from backup-stage inventory')
            for pack in self.baseline_packs:
                target, source = safe_path(stage, pack), safe_path(self.baseline, pack)
                if base.read_json(target / 'manifest.json')['header']['uuid'] != self.expected[pack]['uuid']:
                    raise ValueError('Baseline pack identity differs from stopped-backup stage: ' + pack)
                if not target.resolve().is_relative_to(stage.resolve()):
                    raise ValueError('Baseline target escaped isolated stage')
                shutil.rmtree(target)
                shutil.copytree(source, target)
                if content_hash(target) != self.expected[pack]['content_hash']:
                    raise ValueError('Baseline pack changed during copy: ' + pack)
            # Carry forward the managed compiler's metadata, not a new guessed inventory.
            shutil.copy2(self.baseline / 'cosmetics-build.json', stage / 'cosmetics-build.json')
            protected = {}
            for pack in packs:
                if pack in BASELINE_PACKS:
                    continue
                current = content_hash(safe_path(stage, pack))
                if current != self.expected[pack]['content_hash']:
                    raise ValueError('Unaffected pack changed since stopped backup: ' + pack)
                protected[pack] = current
            from junket_static_overlay import apply_overlay
            overlay = apply_overlay(candidate / 'static-overlay', stage, base.digest(candidate / 'static-overlay/manifest.json'))
            self.stage_evidence = {'active_operation': self.baseline_id, 'baseline': str(self.baseline),
                'baseline_packs': {p:self.expected[p]['content_hash'] for p in self.baseline_packs},
                'candidate_affected_packs':list(BASELINE_PACKS),
                'protected_packs': protected, 'static_overlay': overlay}
        result = base.install_stage(operation_dir, directory, packs, candidate=candidate, stage_mutator=mutate)
        base.write_json(directory / 'bank-expansion-stage-proof.json', self.stage_evidence)
        return result

    def prepare_world(self, directory, candidate, archive):
        from junket_bank_world import apply_bank
        result = apply_bank(directory / 'data/worlds' / base.WORLD, self.plan, self.structure,
            directory / 'bank-world-rollback.json', full_backup=archive, world_is_stopped=True)
        self.world_evidence = result
        base.write_json(directory / 'bank-world-proof.json', result)
        return {'bank':result, 'stage':'bank-expansion-stage-proof.json'}

    def prepare_runtime(self, directory):
        if self.reuse_path is not None:
            self.reuse = validate_unchanged_junket_reuse(self.reuse_path, directory, self.candidate, self.plan, self.structure)
        scripts = directory / 'data/behavior_packs/import_structures/scripts'
        target = scripts / self.probe_source.name
        if target.exists():
            raise ValueError('Isolated-only companion probe already exists in production candidate')
        shutil.copy2(self.probe_source, target)
        main = scripts / 'main.js'
        original = base.digest(main)
        hook = '''
// Isolated bank-expansion test hook; never part of the deployable stage.
import { system as companionProbeSystem } from "@minecraft/server";
import { runCompanionProbe } from "./structure_companions_bds_probe.js";
let companionProbeBusy = false;
companionProbeSystem.afterEvents.scriptEventReceive.subscribe(event => {
  if (event.id !== "ichiyon:isolated_companion_probe" || companionProbeBusy || !/^[a-f0-9-]{36}$/.test(event.message)) return;
  companionProbeBusy = true;
  const request_id = event.message;
  runCompanionProbe({x:-3000,y:120,z:-3000}).then(result => {
    console.warn("[CompanionExpansionResult] " + JSON.stringify({request_id,...result}));
  }).catch(error => console.warn("[CompanionExpansionResult] " + JSON.stringify({request_id,pass:false,errors:[String(error)]})))
    .finally(() => {companionProbeBusy=false;});
});
'''
        with main.open('a', encoding='utf-8') as stream:
            stream.write(hook)
        properties = directory / 'data/server.properties'
        properties.write_text(properties.read_text().replace('difficulty=peaceful', 'difficulty=normal'), encoding='utf-8')
        return {'scope':'clone_data_only', 'production_stage_probe_free':True, 'main_before_sha256':original,
                'main_after_sha256':base.digest(main), 'probe_sha256':base.digest(target),
                'probe_location':[-3000,120,-3000], 'difficulty':'normal for hostile-heal exclusion test'}

    def wait_report(self, directory, phase, since, marker, request_id, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            output = base.checker.read_logs(since)
            report = json_report(output, marker, request_id)
            if report is not None:
                (directory / (phase + '.log')).write_text(output, encoding='utf-8')
                return report, base.checker.runtime_errors(output)
            time.sleep(2)
        (directory / (phase + '.log')).write_text(output, encoding='utf-8')
        raise TimeoutError('Isolated probe report missing: ' + phase)

    def check_runtime(self, directory, server, phase, timeout):
        result = {'phase':phase, 'issues':[], 'client_visual':False, 'pc_player':False, 'mobile_player':False}
        result['tickingareas_before'] = base.checker.collect_tickingareas(directory, phase + '-expansion-before')
        request = str(uuid.uuid4())
        if self.reuse is None:
            since = base.checker.utc_now()
            base.checker.send_console('scriptevent junket:bankcheck ' + request)
            bank, errors = self.wait_report(directory, phase + '-bank', since, BANK_MARKER, request, min(timeout,360))
            result['bank'] = bank
            result['issues'].extend(errors)
            runtime = base.read_json(directory / 'stage/behavior_packs/ichiyon_junket_bp/scripts/all17_original_generated.json')
            games = runtime if isinstance(runtime, list) else runtime.get('venues', runtime.get('games', []))
            destinations = {game['id']:game['destination'] for game in games}
            result['issues'].extend(base.checker.validate_warp_report(bank.get('outbound'),request,destinations))
            if (bank.get('ok') is not True or bank.get('return', {}).get('safe') is not True
                    or bank.get('return', {}).get('teleported') is not True or bank.get('player_test') is not False
                    or len(bank.get('warps',[]))!=17 or set(bank.get('warps',[]))!=set(base.checker.EXPECTED_IDS)):
                result['issues'].append('Bank runtime report did not pass')
        else:
            result['bank'] = deepcopy(self.reuse['bank'][phase])
            result['bank']['executed_in_this_run'] = False
            result['bank']['inherited_proof_sha256'] = self.reuse['proof_sha256']
        # One bounded temporary area, exclusively this test's name; no existing areas are removed.
        area = 'companion_probe_' + request.split('-')[0]
        try:
            base.checker.send_console('tickingarea add circle -3000 120 -3000 1 ' + area + ' true')
            time.sleep(1)
            since = base.checker.utc_now()
            base.checker.send_console('scriptevent ichiyon:isolated_companion_probe ' + request)
            companion, errors = self.wait_report(directory, phase + '-companions', since, PROBE_MARKER, request, min(timeout,180))
            result['companions'] = companion
            result['issues'].extend(errors)
            if companion.get('pass') is not True:
                result['issues'].append('Companion/effectAdd native probe did not pass')
        finally:
            base.checker.send_console('tickingarea remove ' + area)
            result['tickingareas_after'] = base.checker.collect_tickingareas(directory, phase + '-expansion-after')
            result['tickingareas_preserved'] = area_identity(result['tickingareas_before']) == area_identity(result['tickingareas_after'])
            if not result['tickingareas_preserved']:
                result['issues'].append('Expansion probe changed existing tickingarea listings')
        base.write_json(directory / (phase + '-bank-expansion-proof.json'), result)
        self.last_runtime = result
        return result

    def reuse_standard_checks(self):
        return self.reuse

    def check_warp(self, directory, operation):
        # Bank.check already invokes the exact existing all17 safe-warp probe.
        # Validate and reuse its evidence instead of running the same 17 loads twice.
        result = self.last_runtime
        report = result['bank']['outbound']
        cold = [item['id'] for item in report['checks'] if item['loaded_before'] is False]
        proof = {'operation_id':operation, 'request_id':report['request_id'], 'report':report,
            'source':'bank outbound using the existing safe_warp.checkDestinations',
            'server_warp_destinations_verified':not result['issues'] and result['tickingareas_preserved'],
            'tickingareas_preserved':result['tickingareas_preserved'],
            'initially_unloaded_game_ids':cold,'initially_unloaded_game_count':len(cold),
            'cold_destination_load_observed':bool(cold),'all17_initially_unloaded_verified':len(cold)==17,
            'player_test':False,'client_rendering_verified':False,'two_player_playthrough':False,
            'issues':list(result['issues'])}
        base.write_json(directory / 'warp-runtime-proof.json',proof)
        return proof


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--operation',required=True,help='Succeeded native operation E owning the stopped full backup')
    parser.add_argument('--baseline-stage',type=Path,required=True)
    parser.add_argument('--baseline-proof',type=Path,required=True,help='Captured managed active.json or production-start.json')
    parser.add_argument('--candidate-bundle',type=Path,required=True)
    parser.add_argument('--candidate-sha256',required=True)
    parser.add_argument('--probe-source',type=Path,default=Path(__file__).resolve().parents[1]/'structure_companions_bds_probe.js')
    parser.add_argument('--timeout',type=int,default=1200)
    parser.add_argument('--reuse-proof',type=Path,help='Completed prior isolated proof; reuse only exact unchanged JUNKET content')
    args=parser.parse_args()
    if not 60<=args.timeout<=3600: parser.error('timeout must be 60..3600 seconds per phase')
    adapter=BankExpansionAdapter(args.baseline_stage,args.baseline_proof,args.probe_source,args.reuse_proof)
    proof=base.run(args.operation,args.timeout,args.candidate_bundle,args.candidate_sha256,adapter=adapter)
    print(json.dumps(proof,ensure_ascii=False))
    return 0 if proof['isolated_server_resilience_verified'] else 1


if __name__=='__main__':
    raise SystemExit(main())
