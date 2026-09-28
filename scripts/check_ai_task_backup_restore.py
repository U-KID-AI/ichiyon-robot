"""Backup contract fixtures and opt-in, disposable LOCAL PostgreSQL rehearsal.

Default tests never connect to a database. To additionally execute pg_dump and
pg_restore against a newly created local cluster:
  python scripts/check_ai_task_backup_restore.py --postgres-bin /path/to/bin

No existing DB/container/service is used. The cluster listens only on loopback,
uses an ephemeral port and is stopped before its private TemporaryDirectory is
removed. All fixture credentials and payloads below are synthetic.
"""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import ai_task_backup as backup
import ai_task_storage_retention as collector


TARGET = 'a' * 40
PREVIOUS = 'b' * 40
POSTGRES_BIN = None
FILES = {
    '.env': b'APP_MODE=fixture\nTOKEN=synthetic-only\n',
    'data/runtime.json': b'{"live":true,"generation":7}\n',
    'data/gacha_probability_archive.legacy.json': b'{"required_live_archive":true}\n',
    'data/backups/minecraft-packs/20260901/behavior/manifest.json': b'{"uuid":"fixture-old-pack"}\n',
    'data/backups/minecraft-packs/20260901/prior-active.json': b'{"restore":"old-active-pack"}\n',
    'data/backups/legacy-state/unique-only.json': b'{"only_copy":"must survive"}\n',
    'assets/images/welcome.png': b'\x89PNG\r\n\x1a\nfixture-image',
    'secrets/service.json': b'{"fixture_key":"synthetic-only"}\n',
}


def digest(value):
    return hashlib.sha256(value).hexdigest()


def write_text(path, text):
    path.write_bytes(text.encode('utf-8'))


def paths_for(files):
    directories = set()
    for name in files:
        parts = name.split('/')
        directories.update('/'.join(parts[:index]) for index in range(1, len(parts)))
    return sorted(directories), sorted(files)


def inventory(files):
    directories, names = paths_for(files)
    result = [{'path': name, 'type': 'directory', 'size': 0, 'sha256': None}
              for name in directories]
    result.extend({'path': name, 'type': 'file', 'size': len(files[name]),
                   'sha256': digest(files[name])} for name in names)
    return sorted(result, key=lambda item: item['path'])


def write_tar(path, files, *, recovery=False, extra=None):
    directories, names = paths_for(files)
    if recovery:
        directories = [name for name in directories
                       if name == 'data/backups' or name.startswith('data/backups/')]
    with tarfile.open(str(path), 'w', format=tarfile.USTAR_FORMAT) as archive:
        for name in directories:
            member = tarfile.TarInfo(name)
            member.type = tarfile.DIRTYPE
            member.mode = 0o700
            archive.addfile(member)
        for name in names:
            member = tarfile.TarInfo(name)
            member.size = len(files[name])
            member.mode = 0o600
            archive.addfile(member, io.BytesIO(files[name]))
        if extra is not None:
            member, content = extra
            archive.addfile(member, None if content is None else io.BytesIO(content))


def release_fixture(root, sha):
    path = root / sha
    (path / 'src').mkdir(parents=True)
    write_text(path / 'REVISION', sha + '\n')
    write_text(path / 'src' / 'REVISION', sha + '\n')
    write_text(path / 'immutable-image.txt', 'ichiyon-robot-app:' + sha + '\n')
    write_text(path / 'rollback-images.txt', ''.join('sha256:' + item * 64 + '\n'
                                                   for item in ('1', '2', '3')))
    for name in ('compose.immutable.yml', 'persistence.txt', 'validate-immutable-compose.py'):
        write_text(path / name, 'fixture only\n')


class BackupFixture:
    def __init__(self, root, *, version=2, split=False, dump=None):
        self.root = root
        self.path = root / 'backups' / TARGET
        self.releases = root / 'releases'
        self.recovery = root / 'recovery'
        self.path.mkdir(parents=True)
        self.recovery.mkdir()
        release_fixture(self.releases, TARGET)
        release_fixture(self.releases, PREVIOUS)
        self.files = dict(FILES)
        self.version = version
        self.split = split
        self.archive = None
        self.manifest = None
        (self.path / 'production.dump').write_bytes(dump or b'PGDMP-synthetic-unit-fixture')
        write_text(self.path / 'previous', str(self.releases / PREVIOUS).replace('\\', '/') + '\n')
        infra = [[item * 64, 0, '2026-09-28T00:00:00Z'] for item in ('1', '2')]
        write_text(self.path / 'infra.json', json.dumps(infra))
        live = {name: content for name, content in self.files.items()
                if not split or not name.startswith('data/backups/')}
        write_tar(self.path / 'persistence.tar', live)
        if split:
            recovery_tar = self.recovery / 'draft.tar'
            write_tar(recovery_tar, {name: value for name, value in self.files.items()
                                    if name.startswith('data/backups/')}, recovery=True)
            archive_id = digest(recovery_tar.read_bytes())
            self.archive = self.recovery / archive_id
            self.archive.mkdir()
            recovery_tar.rename(self.archive / 'recovery.tar')
            write_text(self.archive / 'checksums.sha256', archive_id + '  recovery.tar\n')
            (self.archive / 'READY').touch()
        if version == 2:
            self.manifest = {
                'format': 'ichiyon-production-backup', 'version': 2,
                'target_release': TARGET, 'previous_release': PREVIOUS,
                'persistence': {'include': ['data', 'assets/images', 'secrets', '.env'],
                                'exclude': ['data/backups'] if split else []},
                'recovery': {'archive_id': self.archive.name} if split else None,
                'inventory': inventory(self.files),
            }
        (self.path / 'READY').touch()
        self.refresh()

    def refresh(self):
        names = ['production.dump', 'persistence.tar']
        if self.version == 2:
            write_text(self.path / 'manifest.json', json.dumps(self.manifest, sort_keys=True))
            names.extend(['previous', 'infra.json', 'manifest.json'])
        write_text(self.path / 'checksums.sha256', ''.join(
            digest((self.path / name).read_bytes()) + '  ' + name + '\n' for name in names))

    def validate(self, validator=None):
        return backup.validate_backup(self.path, releases_root=self.releases,
                                      recovery_root=self.recovery,
                                      dump_validator=validator or (lambda path: True))

    def restore(self, destination, validator=None):
        return backup.restore_files(self.path, destination, releases_root=self.releases,
                                    recovery_root=self.recovery,
                                    dump_validator=validator or (lambda path: True))


def file_map(root):
    return {str(path.relative_to(root)).replace('\\', '/'): path.read_bytes()
            for path in root.rglob('*') if path.is_file()}


def state(root):
    result = {}
    for path in [root] + sorted(root.rglob('*')):
        metadata = path.lstat()
        content = path.read_bytes() if path.is_file() and not path.is_symlink() else None
        result[str(path.relative_to(root))] = (metadata.st_mode, metadata.st_mtime_ns,
                                              digest(content) if content is not None else None)
    return result


class BackupContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ichiyon-backup-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def fixture(self, **kwargs):
        return BackupFixture(self.root / 'source', **kwargs)

    def collector_fixture(self, **kwargs):
        fixture = self.fixture(**kwargs)
        new_recovery = fixture.root / 'ichiyon-recovery-archives'
        fixture.recovery.rename(new_recovery)
        fixture.recovery = new_recovery
        if fixture.archive is not None:
            fixture.archive = new_recovery / fixture.archive.name
        paths = {'home': fixture.root, 'backups': fixture.path.parent, 'releases': fixture.releases}
        return fixture, paths

    def test_legacy_current_six_file_backup_validates(self):
        fixture = self.fixture(version=1)
        calls = []
        fixture.validate(lambda path: calls.append(path) or True)
        self.assertEqual(calls, [fixture.path / 'production.dump'])

    def test_v2_full_scope_validates(self):
        self.fixture().validate()

    def test_v2_split_scope_validates(self):
        self.fixture(split=True).validate()

    def test_legacy_release_reference_metadata_remains_supported(self):
        fixture = self.fixture(version=1)
        previous = fixture.releases / PREVIOUS
        (previous / 'src' / 'REVISION').unlink()
        write_text(previous / 'immutable-image.txt', 'release_sha=' + PREVIOUS + '\n'
                   + 'image_tag=ichiyon-robot-app:' + PREVIOUS + '\n'
                   + 'image_id=sha256:' + '4' * 64 + '\n')
        fixture.validate()

    def test_ready_alone_is_not_valid(self):
        fixture = self.fixture()
        (fixture.path / 'checksums.sha256').unlink()
        with self.assertRaises(backup.BackupError):
            fixture.validate()

    def test_dump_checksum_corruption_is_rejected(self):
        fixture = self.fixture()
        (fixture.path / 'production.dump').write_bytes(b'PGDMP-tampered')
        with self.assertRaises(backup.BackupError):
            fixture.validate()

    def test_pg_restore_rejection_is_failure_even_with_good_checksum(self):
        fixture = self.fixture()
        with self.assertRaises(backup.BackupError):
            fixture.validate(lambda path: False)

    def test_previous_checksum_corruption_is_rejected(self):
        fixture = self.fixture()
        write_text(fixture.path / 'previous', str(fixture.releases / TARGET) + '\n')
        with self.assertRaises(backup.BackupError):
            fixture.validate()

    def test_previous_release_reference_must_exist(self):
        fixture = self.fixture()
        (fixture.releases / PREVIOUS / 'REVISION').unlink()
        with self.assertRaises(backup.BackupError):
            fixture.validate()

    def test_manifest_inventory_cannot_omit_unique_archive_data(self):
        fixture = self.fixture(split=True)
        fixture.manifest['inventory'] = [item for item in fixture.manifest['inventory']
                                         if item['path'] != 'data/backups/legacy-state/unique-only.json']
        fixture.refresh()
        with self.assertRaises(backup.BackupError):
            fixture.validate()

    def test_manifest_exclusion_without_recovery_is_rejected(self):
        fixture = self.fixture(split=True)
        fixture.manifest['recovery'] = None
        fixture.refresh()
        with self.assertRaises(backup.BackupError):
            fixture.validate()

    def test_missing_recovery_payload_is_rejected(self):
        fixture = self.fixture(split=True)
        (fixture.archive / 'recovery.tar').unlink()
        with self.assertRaises(backup.BackupError):
            fixture.validate()

    def test_recovery_payload_checksum_corruption_is_rejected(self):
        fixture = self.fixture(split=True)
        with (fixture.archive / 'recovery.tar').open('ab') as stream:
            stream.write(b'tampered')
        with self.assertRaises(backup.BackupError):
            fixture.validate()

    def test_recursive_backup_cannot_remain_in_split_live_tar(self):
        fixture = self.fixture(split=True)
        write_tar(fixture.path / 'persistence.tar', fixture.files)
        fixture.refresh()
        with self.assertRaises(backup.BackupError):
            fixture.validate()

    def test_live_required_data_cannot_be_excluded(self):
        fixture = self.fixture()
        fixture.manifest['persistence']['exclude'] = ['data']
        fixture.refresh()
        with self.assertRaises(backup.BackupError):
            fixture.validate()

    def test_tar_path_traversal_is_rejected(self):
        fixture = self.fixture()
        member = tarfile.TarInfo('../escape')
        member.size = 1
        write_tar(fixture.path / 'persistence.tar', fixture.files, extra=(member, b'x'))
        fixture.refresh()
        with self.assertRaises(backup.BackupError):
            fixture.validate()

    def test_tar_symlink_escape_is_rejected(self):
        fixture = self.fixture()
        member = tarfile.TarInfo('data/link')
        member.type = tarfile.SYMTYPE
        member.linkname = '/home/ubuntu/ichiyon-shared/.env'
        write_tar(fixture.path / 'persistence.tar', fixture.files, extra=(member, None))
        fixture.refresh()
        with self.assertRaises(backup.BackupError):
            fixture.validate()

    def test_tar_directory_with_payload_is_rejected(self):
        fixture = self.fixture()
        member = tarfile.TarInfo('data/directory-with-payload')
        member.type = tarfile.DIRTYPE
        member.size = 1
        write_tar(fixture.path / 'persistence.tar', fixture.files, extra=(member, b'x'))
        fixture.refresh()
        with self.assertRaises(backup.BackupError):
            fixture.validate()

    def test_tar_hardlink_is_rejected(self):
        fixture = self.fixture()
        member = tarfile.TarInfo('data/hardlink')
        member.type = tarfile.LNKTYPE
        member.linkname = '.env'
        write_tar(fixture.path / 'persistence.tar', fixture.files, extra=(member, None))
        fixture.refresh()
        with self.assertRaises(backup.BackupError):
            fixture.validate()

    def test_recovery_modified_during_validation_is_rejected(self):
        fixture = self.fixture(split=True)
        original = backup.archive_inventory

        def mutate_after_read(path, **kwargs):
            result = original(path, **kwargs)
            if kwargs.get('recovery'):
                (fixture.archive / 'READY').write_bytes(b'changed')
            return result

        with patch('ai_task_backup.archive_inventory', side_effect=mutate_after_read):
            with self.assertRaises(backup.BackupError):
                fixture.validate()

    def test_manifest_boolean_size_is_rejected(self):
        fixture = self.fixture()
        fixture.manifest['inventory'][0]['size'] = False
        fixture.refresh()
        with self.assertRaises(backup.BackupError):
            fixture.validate()

    def test_tar_truncation_is_rejected_even_with_updated_checksum(self):
        fixture = self.fixture()
        path = fixture.path / 'persistence.tar'
        path.write_bytes(path.read_bytes()[:700])
        fixture.refresh()
        with self.assertRaises(backup.BackupError):
            fixture.validate()

    def test_recovery_manifest_path_escape_is_rejected(self):
        fixture = self.fixture(split=True)
        fixture.manifest['recovery']['archive_id'] = '../' + fixture.archive.name
        fixture.refresh()
        with self.assertRaises(backup.BackupError):
            fixture.validate()

    def test_restore_has_exact_original_tree_and_metadata(self):
        fixture = self.fixture(split=True)
        destination = self.root / 'restore'
        destination.mkdir()
        result = fixture.restore(destination)
        restored = Path(result['persistence'])
        self.assertEqual(file_map(restored), fixture.files)
        self.assertEqual((Path(result['metadata']) / 'previous').read_bytes(),
                         (fixture.path / 'previous').read_bytes())
        self.assertEqual((Path(result['metadata']) / 'production.dump').read_bytes(),
                         (fixture.path / 'production.dump').read_bytes())

    def test_restore_validation_failure_writes_nothing(self):
        fixture = self.fixture(split=True)
        destination = self.root / 'restore'
        destination.mkdir()
        (fixture.archive / 'READY').unlink()
        with self.assertRaises(backup.BackupError):
            fixture.restore(destination)
        self.assertEqual(list(destination.iterdir()), [])

    def test_restore_rejects_nonempty_destination(self):
        fixture = self.fixture()
        destination = self.root / 'restore'
        destination.mkdir()
        (destination / 'user-data').write_bytes(b'must remain')
        with self.assertRaises(backup.BackupError):
            fixture.restore(destination)
        self.assertEqual(file_map(destination), {'user-data': b'must remain'})

    def test_validation_is_read_only(self):
        fixture = self.fixture(split=True)
        before = state(fixture.root)
        with patch('os.unlink', side_effect=AssertionError('unlink is forbidden')), \
                patch('os.rename', side_effect=AssertionError('rename is forbidden')), \
                patch('shutil.rmtree', side_effect=AssertionError('rmtree is forbidden')), \
                patch('subprocess.run', side_effect=AssertionError('arbitrary command forbidden')):
            fixture.validate()
        self.assertEqual(state(fixture.root), before)

    def test_backup_root_late_symlink_swap_is_rejected(self):
        fixture = self.fixture()
        probe, moved = self.root / 'symlink-probe', fixture.path.parent / 'moved-backup'
        try:
            probe.symlink_to(fixture.path, target_is_directory=True)
            probe.unlink()
        except OSError:
            self.skipTest('host does not permit directory symlink fixtures')
        original = backup.release_contract
        calls = []

        def swap_after_last_metadata(root, sha):
            result = original(root, sha)
            calls.append(sha)
            if len(calls) == 3:
                fixture.path.rename(moved)
                fixture.path.symlink_to(moved, target_is_directory=True)
            return result

        try:
            with patch('ai_task_backup.release_contract', side_effect=swap_after_last_metadata):
                with self.assertRaises(backup.BackupError):
                    fixture.validate()
        finally:
            if fixture.path.is_symlink():
                fixture.path.unlink()
            if moved.exists():
                moved.rename(fixture.path)

    def test_collector_v2_full_is_verified_with_only_pg_restore_list(self):
        fixture, paths = self.collector_fixture()
        before = state(fixture.root)
        success = subprocess.CompletedProcess(['pg_restore', '--list'], 0, b'fixture TOC', b'')
        with patch('ai_task_backup.subprocess.run', return_value=success) as command:
            result = collector.read_backup(fixture.path, paths, {})
        self.assertEqual(result['validation'], 'verified')
        self.assertEqual(result['backup_format_version'], 2)
        self.assertTrue(result['checksum_verified'])
        self.assertEqual(result['previous_release'], PREVIOUS)
        self.assertEqual(result['recovery_archive_ids'], [])
        self.assertEqual(command.call_args.args[0], ['pg_restore', '--list'])
        self.assertEqual(command.call_count, 1)
        self.assertEqual(state(fixture.root), before)

    def test_collector_v2_split_keeps_validated_recovery_reference(self):
        fixture, paths = self.collector_fixture(split=True)
        success = subprocess.CompletedProcess(['pg_restore', '--list'], 0, b'fixture TOC', b'')
        with patch('ai_task_backup.subprocess.run', return_value=success):
            result = collector.read_backup(fixture.path, paths, {})
        self.assertEqual(result['validation'], 'verified')
        self.assertEqual(result['recovery_archive_ids'], [fixture.archive.name])
        self.assertEqual(result['target_release'], TARGET)
        self.assertEqual(result['previous_release'], PREVIOUS)

    def test_collector_missing_pg_restore_fails_closed(self):
        fixture, paths = self.collector_fixture()
        with patch('ai_task_backup.subprocess.run', side_effect=FileNotFoundError('fixture tool absent')):
            result = collector.read_backup(fixture.path, paths, {})
        self.assertEqual(result['validation'], 'invalid')
        self.assertFalse(result['checksum_verified'])
        self.assertFalse(result['ready'])
        self.assertEqual(result['previous_release'], PREVIOUS)

    def test_collector_rejected_dump_fails_closed(self):
        fixture, paths = self.collector_fixture()
        failure = subprocess.CompletedProcess(['pg_restore', '--list'], 1, b'', b'fixture invalid dump')
        with patch('ai_task_backup.subprocess.run', return_value=failure):
            result = collector.read_backup(fixture.path, paths, {})
        self.assertEqual(result['validation'], 'invalid')
        self.assertFalse(result['checksum_verified'])

    def test_collector_corrupt_v2_checksum_fails_before_pg_restore(self):
        fixture, paths = self.collector_fixture()
        (fixture.path / 'production.dump').write_bytes(b'PGDMP-corruption')
        with patch('ai_task_backup.subprocess.run', side_effect=AssertionError('must fail checksum first')):
            result = collector.read_backup(fixture.path, paths, {})
        self.assertEqual(result['validation'], 'invalid')
        self.assertFalse(result['checksum_verified'])

    def test_collector_v2_without_checksum_verification_is_incomplete(self):
        fixture, paths = self.collector_fixture()
        with patch('ai_task_backup.subprocess.run', side_effect=AssertionError('not an unverified shortcut')):
            result = collector.read_backup(fixture.path, paths, {}, verify_checksums=False)
        self.assertEqual(result['validation'], 'incomplete')
        self.assertFalse(result['checksum_verified'])

    def test_collector_v1_keeps_existing_no_subprocess_contract(self):
        fixture, paths = self.collector_fixture(version=1)
        with patch('ai_task_backup.subprocess.run', side_effect=AssertionError('v1 collector must not invoke tool')):
            result = collector.read_backup(fixture.path, paths, {})
        self.assertEqual(result['validation'], 'verified')
        self.assertTrue(result['checksum_verified'])
        self.assertEqual(result['restore_validation'],
                         'metadata_checksums_tar_scope_dump_header_not_full_restore')
        self.assertNotIn('backup_format_version', result)

    def test_deployment_keeps_full_v1_scope_and_does_not_activate_v2(self):
        remote = Path(__file__).resolve().parent / 'ai_task_deploy_remote.sh'
        script = remote.read_text(encoding='utf-8')
        self.assertIn('tar --dereference -C "$shared_root" -cf "$backup_stage/persistence.tar" '
                      'data assets/images secrets .env', script)
        self.assertNotIn('ai_task_backup', script)
        self.assertNotIn('ichiyon-recovery-archives', script)


class DisposablePostgres:
    """Host-local tools only; every DB process belongs to this exact temp cluster."""
    def __init__(self, binary_directory):
        self.bin = Path(binary_directory).resolve(strict=True)
        self.temp = tempfile.TemporaryDirectory(prefix='ichiyon-pg-rehearsal-')
        self.root = Path(self.temp.name).resolve()
        self.cluster = self.root / 'cluster'
        self.started = False
        self.start_attempted = False
        self.port = None
        self.env = {key: value for key, value in os.environ.items() if not key.startswith('PG')}
        self.env['PGCONNECT_TIMEOUT'] = '5'

    def run(self, executable, arguments, *, input_bytes=None, timeout=60):
        suffix = '.exe' if os.name == 'nt' else ''
        path = self.bin / (executable + suffix)
        if not path.is_file():
            raise RuntimeError('missing local PostgreSQL executable: ' + executable)
        if executable == 'pg_ctl':
            # A Windows postmaster can inherit pg_ctl's pipe handles. A regular
            # control log avoids communicate() waiting for the long-lived server
            # to close a pipe after pg_ctl has already exited successfully.
            log = self.root / 'control.log'
            with log.open('ab') as stream:
                result = subprocess.run([str(path)] + arguments, stdin=subprocess.DEVNULL,
                                        stdout=stream, stderr=subprocess.STDOUT, timeout=timeout,
                                        env=self.env,
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            if result.returncode:
                raise RuntimeError('pg_ctl failed: ' + log.read_bytes().decode('utf-8', errors='replace')[-1500:])
            return b''
        result = subprocess.run([str(path)] + arguments, input=input_bytes,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=timeout, env=self.env,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        if result.returncode:
            raise RuntimeError(executable + ' failed: ' + result.stderr.decode('utf-8', errors='replace')[-1500:])
        return result.stdout

    def __enter__(self):
        try:
            self.run('initdb', ['-D', str(self.cluster), '-U', 'ichiyon_fixture',
                                '--auth=trust', '--no-locale', '-E', 'UTF8'])
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1', 0))
                self.port = reservation.getsockname()[1]
            options = '-h 127.0.0.1 -p ' + str(self.port) + ' -F'
            if os.name != 'nt':
                # Linux socket lives inside this disposable directory too.
                options += ' -k ' + str(self.root)
            self.start_attempted = True
            self.run('pg_ctl', ['-D', str(self.cluster), '-l', str(self.root / 'postgres.log'),
                                '-w', '-t', '30', '-o', options, 'start'])
            self.started = True
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def connection(self, database='postgres'):
        return ['-h', '127.0.0.1', '-p', str(self.port), '-U', 'ichiyon_fixture', '-d', database]

    def sql(self, sql, database='postgres'):
        return self.run('psql', self.connection(database) + ['-X', '-A', '-t', '-v', 'ON_ERROR_STOP=1'],
                        input_bytes=sql.encode('utf-8')).strip()

    def dump(self):
        return self.run('pg_dump', self.connection() + ['-Fc'])

    def dump_validator(self, path):
        self.run('pg_restore', ['--list', str(path)])
        return True

    def __exit__(self, kind, value, traceback):
        # pg_ctl validates this private cluster's postmaster.pid; no process kill,
        # service operation, Docker command or production identity is involved.
        if self.started or (self.start_attempted and (self.cluster / 'postmaster.pid').exists()):
            self.run('pg_ctl', ['-D', str(self.cluster), '-w', '-t', '30', '-m', 'fast', 'stop'])
            self.started = False
        self.temp.cleanup()


@unittest.skipUnless(POSTGRES_BIN, 'use --postgres-bin for real disposable DB rehearsal')
class PostgreSQLRestoreRehearsalTests(unittest.TestCase):
    def test_real_dump_restore_and_exact_files_across_split_archive(self):
        with DisposablePostgres(POSTGRES_BIN) as postgres, \
                tempfile.TemporaryDirectory(prefix='ichiyon-real-backup-') as directory:
            postgres.sql("CREATE TABLE restore_probe(id serial PRIMARY KEY, label text NOT NULL, payload bytea);"
                         "INSERT INTO restore_probe(label,payload) VALUES ('runtime',decode('0001ff','hex')),"
                         "('archive-reference',decode('5047444d50','hex'));"
                         "CREATE TABLE schema_migrations(version text PRIMARY KEY);"
                         "INSERT INTO schema_migrations VALUES ('fixture_001'),('fixture_002');")
            query = "SELECT id,label,encode(payload,'hex') FROM restore_probe ORDER BY id;"
            expected = postgres.sql(query)
            migration_set = postgres.sql('SELECT version FROM schema_migrations ORDER BY version;')
            dump = postgres.dump()
            root = Path(directory)
            for version, split in ((1, False), (2, False), (2, True)):
                fixture = BackupFixture(root / ('v%d-%s' % (version, split)),
                                        version=version, split=split, dump=dump)
                fixture.validate(postgres.dump_validator)
                if version == 2:
                    fixed_recovery = fixture.root / 'ichiyon-recovery-archives'
                    fixture.recovery.rename(fixed_recovery)
                    fixture.recovery = fixed_recovery
                    if fixture.archive is not None:
                        fixture.archive = fixed_recovery / fixture.archive.name
                    paths = {'home': fixture.root, 'backups': fixture.path.parent,
                             'releases': fixture.releases}
                    with patch.dict(os.environ, {'PATH': str(postgres.bin) + os.pathsep + os.environ['PATH']}):
                        measured = collector.read_backup(fixture.path, paths, {})
                    self.assertEqual(measured['validation'], 'verified')
                    self.assertTrue(measured['checksum_verified'])
            destination = root / 'restored'
            destination.mkdir()
            result = fixture.restore(destination, postgres.dump_validator)
            self.assertEqual(file_map(Path(result['persistence'])), FILES)
            self.assertEqual((Path(result['metadata']) / 'previous').read_bytes(),
                             (fixture.path / 'previous').read_bytes())
            postgres.sql('CREATE DATABASE fixture_restored;')
            postgres.run('pg_restore', postgres.connection('fixture_restored') +
                         ['--no-owner', '--no-privileges', '--exit-on-error',
                          str(Path(result['metadata']) / 'production.dump')])
            self.assertEqual(postgres.sql(query, 'fixture_restored'), expected)
            self.assertEqual(postgres.sql('SELECT version FROM schema_migrations ORDER BY version;',
                                          'fixture_restored'), migration_set)
            self.assertEqual(postgres.sql("INSERT INTO restore_probe(label) VALUES ('after-restore') RETURNING id;",
                                          'fixture_restored').splitlines()[0], b'3')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--postgres-bin')
    arguments, unittest_arguments = parser.parse_known_args()
    POSTGRES_BIN = arguments.postgres_bin
    # Evaluate opt-in after parsing instead of requiring a shell environment var.
    PostgreSQLRestoreRehearsalTests.__unittest_skip__ = not bool(POSTGRES_BIN)
    PostgreSQLRestoreRehearsalTests.__unittest_skip_why__ = 'use --postgres-bin for real disposable DB rehearsal'
    unittest.main(argv=[__file__] + unittest_arguments)
