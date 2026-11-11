"""Register a generated PRIVATE trial as a new local Bedrock world directory.

No running world, development pack or existing directory is overwritten.
This is file installation only, not confirmation that Minecraft loaded it.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
from zipfile import ZipFile


def install(archive_path, worlds_root):
    archive_path, worlds_root = archive_path.resolve(), worlds_root.resolve()
    if worlds_root.name != 'minecraftWorlds' or not worlds_root.is_dir():
        raise ValueError('Choose an existing local minecraftWorlds directory')
    with ZipFile(archive_path) as archive:
        if archive.testzip() is not None:
            raise ValueError('Invalid trial archive')
        evidence = json.loads(archive.read('provenance.json'))
        if evidence.get('distribution') != 'PRIVATE_LOCAL_TEST_ONLY_NO_REDISTRIBUTION':
            raise ValueError('Only the generated private reference trial can be installed')
        names = archive.namelist()
        for name in names:
            relative = PurePosixPath(name)
            if relative.is_absolute() or '..' in relative.parts or '\\' in name or ':' in name:
                raise ValueError('Unsafe archive entry')
        if len(names) != len(set(names)):
            raise ValueError('Duplicate archive entries')
        destination = worlds_root / ('junket-sparrow-reference-' + evidence['generation'])
        if destination.parent != worlds_root:
            raise ValueError('Invalid generation identifier')
        destination.mkdir(exist_ok=False)
        for entry in archive.infolist():
            target = destination.joinpath(*PurePosixPath(entry.filename).parts)
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            data = archive.read(entry)
            with target.open('xb') as stream:
                stream.write(data)
            if hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(data).digest():
                raise IOError(f'Installed file mismatch: {entry.filename}')
    result = {'destination': str(destination), 'files': len(names),
              'installation': 'NEW_DIRECTORY_BYTE_VERIFIED',
              'minecraft_client_launch': 'NOT_VERIFIED',
              'two_player_completion': 'NOT_VERIFIED',
              'existing_worlds_modified': False}
    (archive_path.parent / 'local-installation.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--worlds-root', type=Path, required=True)
    args = parser.parse_args()
    install(args.archive, args.worlds_root)
