import base64
import csv
import hashlib
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import junket_verified_dependencies as deps


class VerifiedDependencyTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source, self.target = self.root / 'source', self.root / 'target'
        self.source.mkdir()
        self.files = {'nbtlib/__init__.py': b'__version__ = "2.0.4"\n',
                      'nbtlib/tag.py': b'fixture import source\n',
                      'nbtlib-2.0.4.dist-info/METADATA': b'Name: nbtlib\nVersion: 2.0.4\n',
                      'bin/nbt': b'fixture unused CLI\n'}
        self.rows = []
        for name, value in self.files.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
            self.rows.append([deps.CLI_RECORD if name == 'bin/nbt' else name,
                              'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(value).digest()).decode().rstrip('='),
                              str(len(value))])
        self.rows += [['nbtlib/__pycache__/tag.cpython-310.pyc', '', ''], [deps.RECORD, '', '']]
        self.record()

    def record(self):
        with (self.source / deps.RECORD).open('w', encoding='utf-8', newline='') as stream:
            csv.writer(stream).writerows(self.rows)
        self.pin = deps.sha256(self.source / deps.RECORD)

    def validate(self):
        return deps.validate_distribution(self.source, self.pin)

    def test_exact_hashes_cli_relocation_and_no_unverified_cache_copy(self):
        proof = self.validate()
        self.assertEqual(proof['checked_record_files'], 4)
        self.assertEqual(proof['relocated_cli']['verified_at'], 'bin/nbt')
        self.assertFalse(proof['relocated_cli']['copied'])
        self.assertEqual(set(proof['verified_files']), set(self.files) - {'bin/nbt'})

    def test_modified_record_or_code_or_cli_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'RECORD fingerprint'):
            deps.validate_distribution(self.source, '0' * 64)
        for name in ('nbtlib/tag.py', 'bin/nbt'):
            path = self.source / name
            before = path.read_bytes()
            path.write_bytes(b'changed')
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'content mismatch'):
                self.validate()
            path.write_bytes(before)

    def test_any_other_traversal_is_rejected_even_with_a_matching_record_pin(self):
        self.rows.append(['../../bin/other', '', ''])
        self.record()
        with self.assertRaisesRegex(ValueError, 'escapes'):
            self.validate()

    def test_unrecorded_and_unhashed_import_code_are_rejected(self):
        extra = self.source / 'nbtlib/injected.py'
        extra.write_text('unexpected', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Unrecorded'):
            self.validate()
        self.rows.append(['nbtlib/injected.py', '', ''])
        self.record()
        with self.assertRaisesRegex(ValueError, 'Unhashed'):
            self.validate()

    def test_wrong_version_is_rejected(self):
        (self.source / 'nbtlib-2.0.4.dist-info/METADATA').write_text('Name: nbtlib\nVersion: 9.9.9\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'nbtlib 2.0.4'):
            self.validate()

    def test_copy_imports_with_control_python_and_preserves_source(self):
        before = {p.relative_to(self.source): p.read_bytes() for p in self.source.rglob('*') if p.is_file()}
        result = {'version': '2.0.4', 'leveldb_imported': True, 'little_endian_roundtrip': True}
        with patch.object(deps.subprocess, 'run', return_value=SimpleNamespace(stdout=json.dumps(result))) as run:
            proof = deps.provision(self.source, self.target, Path('/control/python'), Path('/existing/project'), self.pin)
        command = run.call_args.args[0]
        self.assertEqual(command[:3], [str(Path('/control/python')), '-B', '-c'])
        self.assertEqual(command[-2:], [str(self.target), str(Path('/existing/project'))])
        self.assertNotIn('pip', command)
        self.assertIn("('.venv-leveldb','.venv-nbt')", command[3])
        self.assertTrue(proof['verified'])
        self.assertFalse((self.target / 'bin').exists())
        self.assertFalse(list(self.target.rglob('*.pyc')))
        self.assertEqual(before, {p.relative_to(self.source): p.read_bytes() for p in self.source.rglob('*') if p.is_file()})
        with self.assertRaises(FileExistsError):
            deps.provision(self.source, self.target, Path('/control/python'), self.root, self.pin)

    def test_import_failure_preserves_artifacts_and_is_not_success(self):
        failure = subprocess.CalledProcessError(1, ['python'], stderr='numpy import failed')
        with patch.object(deps.subprocess, 'run', side_effect=failure), self.assertRaises(subprocess.CalledProcessError):
            deps.provision(self.source, self.target, Path('/control/python'), self.root, self.pin)
        self.assertTrue((self.target / 'nbtlib/__init__.py').is_file())


if __name__ == '__main__':
    unittest.main()
