"""Fixed-target runner entry for the explicit private JUNKET world migration.

This is a native world migration, not the ordinary app/DB-catalog release or a
fallback for a failed managed release. The server adapter reuses the installed
Control API transaction; its persistent service also owns crash recovery.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
REMOTE_BASE = '/home/ubuntu/junket-native-releases'
TOOLS = ('junket_native_release.py', 'junket_native_world.py', 'junket_catalog_world.py',
         'junket_structure.py', 'build_junket_warp.py', 'minecraft_cosmetics_apply.py', 'minecraft_storage.py',
         'minecraft_junket_overlay.py', 'check_junket_native_runtime.py', 'junket_venue_correction.py',
         'junket_barriers.py', 'junket_private_profile.py', 'junket_verified_dependencies.py', 'junket_static_overlay.py', 'junket_bank_world.py')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ssh_host(value):
    """Require an explicit SSH destination without options or shell syntax."""
    if not isinstance(value, str) or not re.fullmatch(
            r'(?:[A-Za-z_][A-Za-z0-9_.-]*@)?[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', value):
        raise argparse.ArgumentTypeError('Expected an explicit SSH hostname or user@hostname')
    return value


REMOTE_PREPARE = r'''
import hashlib,json,pathlib,shutil,subprocess,sys,zipfile
root=pathlib.Path(__file__).resolve().parent
metadata=json.loads((root/'transport.json').read_text())
project=pathlib.Path('/home/ubuntu/minecraft-bedrock-creative')
control=pathlib.Path('/opt/ichiyon-minecraft-control')
for relative,expected in metadata['files'].items():
 p=(root/relative).resolve()
 if not p.is_relative_to(root) or hashlib.sha256(p.read_bytes()).hexdigest()!=expected:
  raise RuntimeError('transport fingerprint mismatch: '+relative)
installed=control/'minecraft_cosmetics_apply.py'
oldhash=hashlib.sha256(installed.read_bytes()).hexdigest()
if oldhash not in (metadata['previous_apply_sha256'],metadata['files']['tools/minecraft_cosmetics_apply.py']):
 raise RuntimeError('Installed Control API differs from the reviewed baseline')
latest=json.loads((project/'cosmetics-applications/latest.json').read_text())
if latest['status'] not in ('succeeded','failed','recovery_failed'):
 raise RuntimeError('Managed release already active')
subprocess.run([str(control/'.venv/bin/python'),str(root/'tools/junket_verified_dependencies.py'),
 '--baseline-operation',metadata['dependency_baseline'],
 '--destination',str(root/'python-deps'),'--proof',str(root/'python-deps-proof.json')],check=True)
bundle=root/'bundle'
bundle.mkdir()
with zipfile.ZipFile(root/'bundle.zip') as archive:
 for info in archive.infolist():
  p=(bundle/info.filename).resolve()
  if not p.is_relative_to(bundle) or info.filename.startswith('/'):
   raise RuntimeError('invalid bundle path')
 archive.extractall(bundle)
backup=root/'control-before'
backup.mkdir()
for name in ('minecraft_storage.py','minecraft_cosmetics_apply.py','minecraft_junket_overlay.py','junket_static_overlay.py'):
 if (control/name).exists():shutil.copy2(control/name,backup/name)
 subprocess.run(['sudo','install','-o','ubuntu','-g','ubuntu','-m','644',str(root/'tools'/name),str(control/name)],check=True)
subprocess.run([str(control/'.venv/bin/python'),'-m','py_compile',str(control/'minecraft_storage.py'),str(control/'minecraft_cosmetics_apply.py'),str(control/'minecraft_junket_overlay.py'),str(control/'junket_static_overlay.py')],check=True)
subprocess.run(['sudo','systemctl','restart','ichiyon-minecraft-control.service'],check=True)
subprocess.run(['sudo','systemctl','is-active','--quiet','ichiyon-minecraft-control.service'],check=True)
python=str(control/'.venv/bin/python')
entry=str(root/'tools/junket_native_release.py')
operation=metadata['operation_id']
correction=['--packs-and-warps'] if metadata.get('packs_and_warps') else (['--packs-only'] if metadata.get('packs_only') else [])
subprocess.run([python,entry,'--bundle',str(bundle),'--operation',operation,*correction],check=True)
unit='ichiyon-junket-native-'+operation+'.service'
content='\n'.join([
 '[Unit]','Description=Private JUNKET native world migration '+operation,
 'After=docker.service ichiyon-minecraft-control.service','Wants=docker.service',
 '[Service]','Type=oneshot','User=ubuntu','Group=ubuntu',
 'WorkingDirectory='+str(project),
 'ExecStart='+python+' '+entry+' --bundle '+str(bundle)+' --operation '+operation+' --apply --resume'+(' '+correction[0] if correction else ''),
 'RemainAfterExit=yes','Restart=on-failure','RestartSec=5','TimeoutStartSec=0','TimeoutStopSec=300',
 '[Install]','WantedBy=multi-user.target',''])
unitpath=root/unit
unitpath.write_text(content)
subprocess.run(['sudo','install','-o','root','-g','root','-m','644',str(unitpath),'/etc/systemd/system/'+unit],check=True)
subprocess.run(['sudo','systemctl','daemon-reload'],check=True)
subprocess.run(['sudo','systemctl','enable',unit],check=True)
subprocess.run(['sudo','systemctl','start','--no-block',unit],check=True)
print(json.dumps({'operation_id':operation,'unit':unit,'status':'dispatched'}),flush=True)
'''


def resume_metadata(args, operation):
    """Validate the immutable dispatched transport; never prepare it again."""
    metadata = json.loads((args.output / 'transport.json').read_text(encoding='utf-8'))
    expected = {'operation_id': operation, 'host': ssh_host(args.host), 'world': 'ichiyon-creative-flat',
                'packs_only': args.packs_only, 'packs_and_warps': args.packs_and_warps}
    if args.dependency_baseline is not None:
        expected['dependency_baseline'] = str(uuid.UUID(args.dependency_baseline))
    if any(metadata.get(key) != value for key, value in expected.items()):
        raise ValueError('Resume identity or correction flags differ from the dispatched operation')
    if metadata['files'].get('bundle.zip') != digest(args.bundle):
        raise ValueError('Resume bundle differs from the dispatched operation')
    with ZipFile(args.output / 'transport.zip') as archive:
        if json.loads(archive.read('transport.json')) != metadata:
            raise ValueError('Resume transport metadata mismatch')
        if set(archive.namelist()) != set(metadata['files']) | {'transport.json'}:
            raise ValueError('Resume transport inventory mismatch')
        for name, expected_hash in metadata['files'].items():
            if hashlib.sha256(archive.read(name)).hexdigest() != expected_hash:
                raise ValueError('Resume transport fingerprint mismatch: ' + name)
    return metadata


def main():
    # SSH emits UTF-8 even on a Windows host whose default locale is CP932.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--host', type=ssh_host, required=True,
                        help='Explicit authorized SSH destination, also bound to resume metadata')
    parser.add_argument('--ssh-key', type=Path, required=True)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--dependency-baseline',
                        help='Retained successful native release UUID providing verified dependencies; required for new preparation')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--operation', default=str(uuid.uuid4()))
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--resume', action='store_true',
                        help='Collect an already dispatched operation; never upload or deploy again')
    corrections = parser.add_mutually_exclusive_group()
    corrections.add_argument('--packs-only', action='store_true')
    corrections.add_argument('--packs-and-warps', action='store_true')
    parser.add_argument('--warp-check', action='store_true')
    parser.add_argument('--quality-check', action='store_true')
    parser.add_argument('--resilience-check', action='store_true')
    args = parser.parse_args()
    if not args.resume and not args.dependency_baseline:
        parser.error('--dependency-baseline is required for a new transport')
    if args.dependency_baseline is not None:
        args.dependency_baseline = str(uuid.UUID(args.dependency_baseline))
    # Local preparation is also a capacity-producing operation. Warn before
    # constructing a transport; collection/recovery remains available.
    sys.path.insert(0, str(ROOT / 'scripts/minecraft'))
    from minecraft_storage import check_capacity
    capacity = check_capacity(args.output.parent, purpose='recovery' if args.resume else 'development')
    if capacity['warnings']:
        print('Minecraft storage warning: ' + ','.join(capacity['warnings'])
              + ' free_bytes=' + str(capacity['free_bytes']), file=sys.stderr)
    operation = str(uuid.UUID(args.operation))
    if args.resume and not args.apply:
        parser.error('--resume requires --apply to collect the existing operation')
    transport = args.output / 'transport.zip'
    if args.resume:
        metadata = resume_metadata(args, operation)
    else:
        args.output.mkdir(parents=True, exist_ok=False)
        files = {f'tools/{name}': (ROOT / 'scripts/minecraft' / name).read_bytes() for name in TOOLS}
        files['bundle.zip'] = args.bundle.read_bytes()
        files['remote_prepare.py'] = REMOTE_PREPARE.encode('utf-8')
        with ZipFile(args.checkpoint) as checkpoint:
            previous = hashlib.sha256(checkpoint.read('scripts/minecraft/minecraft_cosmetics_apply.py')).hexdigest()
        metadata = {'schema': 1, 'operation_id': operation, 'host': args.host,
                    'dependency_baseline': args.dependency_baseline,
                    'storage_preflight': capacity,
                    'packs_only': args.packs_only, 'packs_and_warps': args.packs_and_warps,
                    'world': 'ichiyon-creative-flat', 'previous_apply_sha256': previous,
                    'files': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
        files['transport.json'] = json.dumps(metadata, indent=2).encode('utf-8')
        with ZipFile(transport, 'x', ZIP_DEFLATED) as archive:
            for name, data in files.items():
                archive.writestr(name, data)
        (args.output / 'transport.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    if not args.apply:
        print(json.dumps({'status': 'prepared', 'transport': str(transport), 'operation_id': operation,
                          'storage_capacity': capacity}))
        return
    options = ['-F', 'none', '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
               '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=15', '-i', str(args.ssh_key)]
    remote = REMOTE_BASE + '/' + operation
    def ssh(command, capture=False):
        return subprocess.run(['ssh', *options, args.host, command], check=True, text=True,
                              encoding='utf-8', capture_output=capture)
    if args.resume:
        if json.loads(ssh('cat ' + remote + '/transport.json', capture=True).stdout) != metadata:
            raise ValueError('Remote resume transport differs from the local dispatched operation')
    else:
        ssh('mkdir -p ' + REMOTE_BASE + ' && mkdir ' + remote)
        subprocess.run(['scp', *options, str(transport), args.host + ':' + remote + '/transport.zip'], check=True)
        # UUID-only destination and hex digest are the only interpolated values.
        sha = digest(transport)
        verify = "import pathlib,hashlib,zipfile;p=pathlib.Path('"+remote+"/transport.zip');assert hashlib.sha256(p.read_bytes()).hexdigest()=='"+sha+"';zipfile.ZipFile(p).extractall(p.parent)"
        ssh('python3 -c "' + verify + '"')
        ssh('python3 ' + remote + '/remote_prepare.py')
    proof = '/home/ubuntu/minecraft-bedrock-creative/junket-world-applications/' + operation + '/native-release-proof.json'
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        response = ssh('if test -f ' + proof + '; then cat ' + proof + '; else echo PENDING; fi', capture=True).stdout.strip()
        if response != 'PENDING':
            result = json.loads(response)
            (args.output / 'native-release-proof.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(result, ensure_ascii=False), flush=True)
            unit = 'ichiyon-junket-native-' + operation + '.service'
            logs = ssh('sudo journalctl -u ' + unit + ' --no-pager -n 160 -o cat', capture=True).stdout
            (args.output / 'operation.log').write_text(logs, encoding='utf-8')
            unit_finished = 'sudo systemctl disable ' + unit
            ssh(unit_finished)
            runtime_check = None
            if result.get('status') == 'succeeded':
                runtime_check = subprocess.run(['ssh', *options, args.host, '/opt/ichiyon-minecraft-control/.venv/bin/python ' + remote +
                    '/tools/check_junket_native_runtime.py --operation ' + operation +
                    (' --warp-check' if args.warp_check or args.packs_and_warps else '') +
                    (' --quality-check' if args.quality_check else '') +
                    (' --resilience-check' if args.resilience_check else '')],
                    text=True, encoding='utf-8', capture_output=True)
                (args.output / 'runtime-check.log').write_text(runtime_check.stdout + runtime_check.stderr,
                                                               encoding='utf-8')
            # Retrieve the transaction's own evidence once; do not reconstruct
            # or repeatedly query the server state in a parallel monitor.
            directory = proof.rsplit('/', 1)[0]
            collect = ("import json,pathlib;p=pathlib.Path('" + directory + "');"
                       "names=('preflight.json','full-backup.json','native-proof.json','native-extension-proof.json','warp-proof.json',"
                       "'native-error.json','preload-new-chunks.json','runtime-proof.json','warp-runtime-proof.json','quality-runtime-proof.json','resilience-runtime-proof.json','static-overlay-proof.json','bank-world-proof.json','bank-runtime-proof.json');"
                       "e={n:json.loads((p/n).read_text()) for n in names if (p/n).is_file()};"
                       "d=pathlib.Path('" + remote + "/python-deps-proof.json');"
                       "e.update({'python-deps-proof.json':json.loads(d.read_text())} if d.is_file() else {});"
                       "print(json.dumps(e))")
            evidence = json.loads(ssh('python3 -c "' + collect + '"', capture=True).stdout)
            for name, value in evidence.items():
                (args.output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
            # Recovery is needed only while pending. Retain all unit files and
            # backups, but do not run terminal migrations on every host boot.
            if result.get('status') != 'succeeded':
                raise SystemExit(1)
            if runtime_check and runtime_check.returncode:
                print(json.dumps({'status': 'world_applied_runtime_attention', 'operation_id': operation,
                                  'evidence': str(args.output / 'runtime-proof.json')}), flush=True)
                raise SystemExit(3)
            if runtime_check:
                print(runtime_check.stdout, flush=True)
            return
        time.sleep(10)
    print(json.dumps({'status': 'pending', 'operation_id': operation, 'proof': proof,
                      'message': 'Server operation remains owned by its persistent service; do not start a duplicate.'}))
    raise SystemExit(2)


if __name__ == '__main__':
    main()
