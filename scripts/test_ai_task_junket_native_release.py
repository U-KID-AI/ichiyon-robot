"""Dispatched-release result recovery must not dispatch another deployment."""
import argparse
import ast
import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import ai_task_junket_native_release as release


class ResumeTests(unittest.TestCase):
    operation = '10000000-0000-4000-8000-000000000007'

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.args = SimpleNamespace(output=root, bundle=root / 'candidate.zip',
                                    host='ubuntu@minecraft.example.invalid', dependency_baseline=None,
                                    packs_only=True, packs_and_warps=False)
        self.args.bundle.write_bytes(b'unchanged candidate')
        self.payload = {'bundle.zip': self.args.bundle.read_bytes(), 'remote_prepare.py': b'never run'}
        self.metadata = {'schema': 1, 'operation_id': self.operation, 'host': self.args.host,
                         'world': 'ichiyon-creative-flat', 'packs_only': True, 'packs_and_warps': False,
                         'files': {k: hashlib.sha256(v).hexdigest() for k, v in self.payload.items()}}
        (root / 'transport.json').write_text(json.dumps(self.metadata), encoding='utf-8')
        self.write_archive()

    def write_archive(self):
        with ZipFile(self.args.output / 'transport.zip', 'w') as archive:
            archive.writestr('transport.json', json.dumps(self.metadata))
            for name, data in self.payload.items():
                archive.writestr(name, data)

    def test_original_transport_is_accepted(self):
        self.assertEqual(release.resume_metadata(self.args, self.operation), self.metadata)

    def test_host_is_explicit_and_rejects_ssh_options_or_shell_syntax(self):
        self.assertEqual(release.ssh_host(self.args.host), self.args.host)
        for value in ('', '-oProxyCommand=example', 'user@host;command', 'user@host/path',
                      'user@host:22', 'user@host\ncommand', 'user@$(command)'):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                release.ssh_host(value)

    def test_dependency_verification_precedes_control_changes_and_never_invokes_pip(self):
        self.assertIn('junket_verified_dependencies.py', release.TOOLS)
        self.assertNotIn("'-m','pip'", release.REMOTE_PREPARE)
        self.assertLess(release.REMOTE_PREPARE.index("tools/junket_verified_dependencies.py"),
                        release.REMOTE_PREPARE.index("['sudo','install'"))
        self.assertIn("'--proof',str(root/'python-deps-proof.json')", release.REMOTE_PREPARE)
        self.assertIn("'--baseline-operation',metadata['dependency_baseline']", release.REMOTE_PREPARE)

    def test_private_static_overlay_is_shipped_and_installed_for_future_catalog_updates(self):
        self.assertIn('junket_static_overlay.py', release.TOOLS)
        self.assertIn('minecraft_storage.py', release.TOOLS)
        self.assertIn("for name in ('minecraft_storage.py','minecraft_cosmetics_apply.py','minecraft_junket_overlay.py','junket_static_overlay.py'):", release.REMOTE_PREPARE)
        self.assertIn("str(control/'junket_static_overlay.py')", release.REMOTE_PREPARE)

    def test_transport_contains_all_local_python_dependencies(self):
        source = release.ROOT / 'scripts/minecraft'
        shipped = {Path(name).stem for name in release.TOOLS}
        # This authenticated service is intentionally reused from the installed revision.
        provided_by_control_service = {'minecraft_control_api'}
        for name in release.TOOLS:
            path = source / name
            self.assertTrue(path.is_file(), name)
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                modules = ([node.module] if isinstance(node, ast.ImportFrom) else
                           [alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
                for module in modules:
                    if module and (source / (module.split('.')[0] + '.py')).is_file():
                        self.assertIn(module.split('.')[0], shipped | provided_by_control_service,
                                      name + ' imports ' + module)

    def test_identity_flags_and_bundle_mismatch_are_rejected(self):
        for key, value in [('host', 'different'), ('world', 'different'),
                           ('operation_id', 'different'), ('packs_only', False)]:
            with self.subTest(key=key):
                altered = dict(self.metadata, **{key: value})
                (self.args.output / 'transport.json').write_text(json.dumps(altered), encoding='utf-8')
                with self.assertRaises(ValueError):
                    release.resume_metadata(self.args, self.operation)
        (self.args.output / 'transport.json').write_text(json.dumps(self.metadata), encoding='utf-8')
        self.args.bundle.write_bytes(b'different candidate')
        with self.assertRaisesRegex(ValueError, 'bundle differs'):
            release.resume_metadata(self.args, self.operation)

    def test_resume_rejects_a_different_explicit_dependency_baseline(self):
        self.args.dependency_baseline = '10000000-0000-4000-8000-000000000008'
        with self.assertRaisesRegex(ValueError, 'Resume identity'):
            release.resume_metadata(self.args, self.operation)

    def test_transport_payload_tampering_is_rejected(self):
        self.payload['remote_prepare.py'] = b'changed'
        self.write_archive()
        with self.assertRaisesRegex(ValueError, 'fingerprint mismatch'):
            release.resume_metadata(self.args, self.operation)

    def test_resume_collects_utf8_without_upload_or_deployment(self):
        calls = []
        proof = {'status': 'succeeded', 'operation_id': self.operation, 'message': '本番 → 完了'}
        def run(command, **kwargs):
            calls.append(command)
            self.assertEqual(command[0], 'ssh')
            self.assertEqual(kwargs.get('encoding'), 'utf-8')
            remote = command[-1]
            if remote.startswith('cat '):
                output = json.dumps(self.metadata)
            elif remote.startswith('if test -f '):
                output = json.dumps(proof, ensure_ascii=False)
            elif 'journalctl' in remote:
                output = '安全停止 → 保存完了'
            elif 'check_junket_native_runtime.py' in remote:
                output = '{"initialized": true}'
            elif remote.startswith('python3 -c '):
                output = '{"runtime-proof.json": {"initialized": true}}'
            elif remote.startswith('sudo systemctl disable '):
                output = ''  # Existing terminal-operation boot cleanup is retained.
            else:
                self.fail('Unexpected remote action: ' + remote)
            return SimpleNamespace(stdout=output, stderr='', returncode=0)
        argv = ['release', '--bundle', str(self.args.bundle), '--ssh-key', 'existing-key',
                '--host', self.args.host,
                '--checkpoint', 'unused-on-resume', '--output', str(self.args.output),
                '--operation', self.operation, '--packs-only', '--apply', '--resume']
        with patch.object(release.sys, 'argv', argv), patch.object(release.sys, 'stdout', io.StringIO()), \
             patch.object(release.subprocess, 'run', side_effect=run):
            release.main()
        self.assertEqual(len(calls), 6)
        recovered = json.loads((self.args.output / 'native-release-proof.json').read_text(encoding='utf-8'))
        self.assertEqual(recovered, proof)
        self.assertFalse(any('remote_prepare.py' in c[-1] or 'mkdir ' in c[-1] or
                             'systemctl start' in c[-1] for c in calls))


if __name__ == '__main__':
    unittest.main()
