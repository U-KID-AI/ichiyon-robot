"""Reuse the pinned successful release's private nbtlib; never invoke pip.

Only verified import files are copied to a new release directory. The historical
pip --target CLI relocation is checked explicitly but never copied or executed.
"""
from __future__ import annotations
import argparse
import base64
import csv
from email.parser import Parser
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import uuid
import subprocess

PROJECT = Path('/home/ubuntu/minecraft-bedrock-creative')
CONTROL_PYTHON = Path('/opt/ichiyon-minecraft-control/.venv/bin/python')
RECORD = 'nbtlib-2.0.4.dist-info/RECORD'
RECORD_SHA256 = 'd28ea9ee3f789d9fb65c46ab2fe94d09d86cde2f262f2fbc3ad0cfb119f3cf1d'
CLI_RECORD = '../../bin/nbt'


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def plain(path):
    for item in (path, *path.parents):
        if item.is_symlink() or (hasattr(item, 'is_junction') and item.is_junction()):
            raise ValueError('Linked dependency path is not allowed')
    return path


def validate_distribution(source, expected_record_sha=RECORD_SHA256):
    plain(source)
    record = plain(source / RECORD)
    if sha256(record) != expected_record_sha:
        raise ValueError('Baseline nbtlib RECORD fingerprint differs from the inspected successful release')
    metadata = Parser().parsestr(plain(source / 'nbtlib-2.0.4.dist-info/METADATA').read_text(encoding='utf-8'))
    if metadata.get('Name') != 'nbtlib' or metadata.get('Version') != '2.0.4':
        raise ValueError('Baseline distribution must be nbtlib 2.0.4')
    checked, copy_files, ignored_cache, seen = {}, {}, [], set()
    with record.open(newline='', encoding='utf-8') as stream:
        for row in csv.reader(stream):
            if len(row) != 3 or row[0] in seen:
                raise ValueError('Invalid or duplicate dependency RECORD row')
            name, encoded, size = row
            seen.add(name)
            relative = PurePosixPath(name)
            is_cli = name == CLI_RECORD
            if not is_cli and (relative.is_absolute() or '..' in relative.parts or '\\' in name or ':' in name
                    or relative.as_posix() != name or relative.parts[0] not in ('nbtlib', 'nbtlib-2.0.4.dist-info')):
                raise ValueError('Dependency RECORD path escapes the permitted distribution')
            path = plain(source / ('bin/nbt' if is_cli else name))
            is_cache = '__pycache__' in relative.parts and relative.suffix == '.pyc'
            if not encoded:
                if size or (name != RECORD and not is_cache):
                    raise ValueError('Unhashed dependency import file')
                if is_cache:
                    ignored_cache.append(name)
                continue
            if not encoded.startswith('sha256=') or not size.isdigit():
                raise ValueError('Dependency RECORD requires SHA256 and byte size')
            try:
                expected = base64.urlsafe_b64decode(encoded[7:] + '=' * (-len(encoded[7:]) % 4)).hex()
            except Exception as error:
                raise ValueError('Malformed dependency RECORD hash') from error
            if len(expected) != 64 or not path.is_file() or path.stat().st_size != int(size) or sha256(path) != expected:
                raise ValueError('Dependency RECORD content mismatch: ' + name)
            checked[name] = expected
            if not is_cli and not is_cache:
                copy_files[name] = expected
    if RECORD not in seen or CLI_RECORD not in checked or 'nbtlib/__init__.py' not in copy_files:
        raise ValueError('Pinned dependency inventory is incomplete')
    actual = set()
    for folder in ('nbtlib', 'nbtlib-2.0.4.dist-info'):
        for path in plain(source / folder).rglob('*'):
            plain(path)
            if path.is_file() and not ('__pycache__' in path.parts and path.suffix == '.pyc'):
                actual.add(path.relative_to(source).as_posix())
    if actual != set(copy_files) | {RECORD}:
        raise ValueError('Unrecorded or missing dependency import file')
    return {'distribution': 'nbtlib', 'version': '2.0.4', 'record_sha256': expected_record_sha,
            'checked_record_files': len(checked), 'verified_files': copy_files,
            'excluded_unhashed_cache': ignored_cache,
            'relocated_cli': {'record_path': CLI_RECORD, 'verified_at': 'bin/nbt',
                              'sha256': checked[CLI_RECORD], 'copied': False, 'executed': False}}


IMPORT_CHECK = r'''
import io,json,pathlib,sys
target=pathlib.Path(sys.argv[1]).resolve();project=pathlib.Path(sys.argv[2])
sys.path.insert(0,str(target))
for name in ('.venv-leveldb','.venv-nbt'):
 paths=list((project/name/'lib').glob('python*/site-packages'))
 if len(paths)!=1:raise RuntimeError('Existing native-world Python runtime is unavailable: '+name)
 sys.path.append(str(paths[0]))
import nbtlib,numpy,leveldb
if nbtlib.__version__!='2.0.4' or pathlib.Path(nbtlib.__file__).resolve()!=target/'nbtlib/__init__.py':
 raise RuntimeError('Control Python did not import the verified private nbtlib')
value=nbtlib.File({'probe':nbtlib.Int(17)});buffer=io.BytesIO();value.write(buffer,byteorder='little');buffer.seek(0)
if int(nbtlib.File.parse(buffer,byteorder='little')['probe'])!=17:raise RuntimeError('NBT little-endian roundtrip failed')
print(json.dumps({'version':nbtlib.__version__,'module':str(pathlib.Path(nbtlib.__file__).resolve()),
 'numpy_version':numpy.__version__,'leveldb_imported':True,'little_endian_roundtrip':True}))
'''


def provision(source, destination, control_python, project, expected_record_sha=RECORD_SHA256):
    verified = validate_distribution(source, expected_record_sha)
    plain(destination)
    destination.mkdir(mode=0o700, exist_ok=False)
    files = verified['verified_files'] | {RECORD: verified['record_sha256']}
    for relative, expected in files.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, target)
        if sha256(target) != expected:
            raise ValueError('Dependency changed during verified copy: ' + relative)
    # -B forbids bytecode writes; the current control interpreter uses exactly
    # the existing native adapter's two dependency paths, not a changed venv.
    imported = subprocess.run([str(control_python), '-B', '-c', IMPORT_CHECK, str(destination), str(project)],
                              check=True, text=True, capture_output=True)
    result = json.loads(imported.stdout.strip())
    return {**verified, 'source': str(source), 'destination': str(destination),
            'copied_files': len(files), 'control_python': str(control_python),
            'import': result, 'verified': True, 'system_venv_modified': False, 'pip_invoked': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-operation', required=True, help='Explicit retained successful release UUID')
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--proof', type=Path, required=True)
    args = parser.parse_args()
    baseline_id = str(uuid.UUID(args.baseline_operation))
    source = Path('/home/ubuntu/junket-native-releases') / baseline_id / 'python-deps'
    if args.proof.exists():
        raise FileExistsError('Preserve the previous dependency proof; use a fresh release operation')
    proof = {'verified': False, 'baseline_operation': baseline_id, 'source': str(source),
             'system_venv_modified': False, 'pip_invoked': False}
    try:
        baseline = json.loads((PROJECT / 'junket-world-applications' / baseline_id / 'native-release-proof.json').read_text())
        if (baseline.get('operation_id') != baseline_id or baseline.get('status') != 'succeeded'
                or baseline.get('installed') is not True or baseline.get('world') != 'ichiyon-creative-flat'):
            raise ValueError('Dependency source must be the fixed successful native release')
        proof.update(provision(source, args.destination, CONTROL_PYTHON, PROJECT))
    except Exception as error:
        proof['error'] = str(error)
        if isinstance(error, subprocess.CalledProcessError):
            proof['import_stderr'] = error.stderr
        raise
    finally:
        with args.proof.open('x', encoding='utf-8') as output:
            json.dump(proof, output, ensure_ascii=False, indent=2)
            output.write('\n')
    print(json.dumps({'private_dependency_verified': True, 'proof': str(args.proof)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
