"""Offline checks for trusted Minecraft runtime transport.

No SSH connection, Docker daemon, credentials, or production BDS is used.
"""

import io
import json
import shlex
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from dataclasses import replace

from ai_task_deploy_config import DeploymentError
from ai_task_minecraft_runtime import (
    ExactMergeSource,
    MinecraftDeployConfig,
    ProductionMinecraftDeployAdapter,
    parse_proof,
)
from ai_task_minecraft_deploy import prepare_pack_archive
from ai_task_process import ProcessResult


SHA = "a" * 40
UUID = "12345678-1234-1234-1234-123456789abc"
PACK = "minecraft/resource_packs/runtime_test/"


def pack_archive():
    manifest = json.dumps(
        {
            "header": {
                "uuid": UUID,
                "version": [1, 2, 3],
            }
        }
    ).encode()

    stream = io.BytesIO()

    with tarfile.open(fileobj=stream, mode="w") as tar:
        for name, data in (
            (PACK + "manifest.json", manifest),
            (PACK + "textures/test.txt", b"ok"),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

    return stream.getvalue()


class RuntimeChecks(unittest.TestCase):
    def make_config(self, root):
        ssh = root / "ssh"
        key = root / "key"
        known = root / "known_hosts"

        for path in (ssh, key, known):
            path.write_text("x", encoding="utf-8")

        return MinecraftDeployConfig(
            ssh_path=ssh,
            ssh_host="127.0.0.1",
            ssh_user="ubuntu",
            ssh_key_path=key,
            known_hosts_path=known,
            data_root="/srv/minecraft/data",
            world_name="world",
            container="minecraft-bedrock",
            excluded_roots=(root / "excluded",),
        )

    def test_config_accepts_fixed_safe_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.make_config(Path(tmp))
            config.validate()
            replace(config, data_root="/srv/Minecraft data/\u65e5\u672c", world_name="World's \u65e5\u672c").validate()

    def test_config_rejects_unsafe_remote_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            cases = (
                {"data_root": "/srv/minecraft/../escape"},
                {"data_root": "relative/path"},
                {"world_name": "../world"},
                {"container": "bad/container"},
                {"ssh_user": "root"},
            )

            base = self.make_config(root)

            for override in cases:
                values = {
                    "ssh_path": base.ssh_path,
                    "ssh_host": base.ssh_host,
                    "ssh_user": base.ssh_user,
                    "ssh_key_path": base.ssh_key_path,
                    "known_hosts_path": base.known_hosts_path,
                    "data_root": base.data_root,
                    "world_name": base.world_name,
                    "container": base.container,
                    "excluded_roots": base.excluded_roots,
                    "timeout": base.timeout,
                    "max_output_bytes": base.max_output_bytes,
                }
                values.update(override)

                with self.subTest(override=override):
                    with self.assertRaises(DeploymentError):
                        MinecraftDeployConfig(**values).validate()

    def test_current_main_sha_is_local_only(self):
        source = ExactMergeSource(
            Path("/tmp/runtime-source"),
            Path("/usr/bin/git"),
        )

        source._require_repo = Mock()
        source._run = Mock(
            return_value=SimpleNamespace(
                returncode=0,
                stdout=SHA + "\n",
                stderr="",
            )
        )

        self.assertEqual(source.current_main_sha(), SHA)
        source._require_repo.assert_called_once_with()
        source._run.assert_called_once_with(
            ("rev-parse", "origin/main")
        )

    def test_source_repo_accepts_git_file_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").write_text(
                "gitdir: ../.git/worktrees/runtime-source\n",
                encoding="utf-8",
            )
            source = ExactMergeSource(
                root,
                Path("/usr/bin/git"),
            )
            source._run = Mock(
                side_effect=[
                    SimpleNamespace(
                        returncode=0,
                        stdout="../.git/worktrees/runtime-source\n",
                        stderr="",
                    ),
                    SimpleNamespace(
                        returncode=0,
                        stdout=str(root.resolve()) + "\n",
                        stderr="",
                    ),
                    SimpleNamespace(
                        returncode=0,
                        stdout="https://github.com/U-KID-AI/ichiyon-robot.git\n",
                        stderr="",
                    ),
                ]
            )

            source._require_repo()

            self.assertEqual(
                [call.args[0] for call in source._run.call_args_list],
                [
                    ("rev-parse", "--git-dir"),
                    ("rev-parse", "--show-toplevel"),
                    ("remote", "get-url", "origin"),
                ],
            )

    def test_git_failure_preserves_stderr(self):
        source = ExactMergeSource(Path('/tmp/runtime-source'), Path('/usr/bin/git'))
        with patch('ai_task_minecraft_runtime.subprocess.run', return_value=SimpleNamespace(
                returncode=128, stdout='', stderr='remote unavailable; token=fixture-secret')):
            with self.assertRaisesRegex(DeploymentError, 'exit=128.*remote unavailable') as error:
                source._run(('fetch', 'origin', 'main'))
        self.assertNotIn('fixture-secret', str(error.exception))

    def test_transport_warnings_quoting_and_refresh_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = replace(self.make_config(Path(tmp)), data_root="/srv/Minecraft data",
                             world_name="World's \u65e5\u672c")
            source = Mock()
            raw = pack_archive()
            source.pack_archive.return_value = raw
            digest = prepare_pack_archive(raw)[1]
            proof = (f'MINECRAFT_DEPLOY_RESULT=SUCCESS\nDEPLOYED_COMMIT_SHA={SHA}\n'
                     f'MINECRAFT_TREE_SHA256={digest}\nMINECRAFT_CHANGED=0\n')
            adapter = ProductionMinecraftDeployAdapter(config, source)
            with patch('ai_task_minecraft_runtime.subprocess.Popen') as popen, patch(
                    'ai_task_minecraft_runtime.communicate_bounded', return_value=ProcessResult(0, proof, 'SSH warning')):
                self.assertEqual(adapter.deploy(SHA, refresh_source=False).deployed_commit_sha, SHA)
            source.pack_archive.assert_called_once_with(SHA, refresh_source=False)
            argv = popen.call_args.args[0]
            self.assertEqual(shlex.split(argv[-1]), ['/usr/bin/python3', '-', SHA,
                                                   config.data_root, config.world_name, config.container])
            for result in (ProcessResult(12, '', 'docker failed; password=fixture-secret'),
                           ProcessResult(0, proof, '', timed_out=True),
                           ProcessResult(0, proof, '', stopped=True),
                           ProcessResult(0, proof, '', stdin_cleanup_failed=True)):
                with patch('ai_task_minecraft_runtime.subprocess.Popen'), patch(
                        'ai_task_minecraft_runtime.communicate_bounded', return_value=result):
                    with self.assertRaisesRegex(DeploymentError, 'transport failed') as error:
                        adapter.deploy(SHA)
                self.assertNotIn('fixture-secret', str(error.exception))
                if result.returncode:
                    self.assertIn('docker failed', str(error.exception))

    def test_full_transport_stderr_is_not_limited_by_proof_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.make_config(Path(tmp))
            source = Mock()
            source.pack_archive.return_value = pack_archive()
            adapter = ProductionMinecraftDeployAdapter(config, source)
            diagnostic = (f'BEGIN {config.ssh_host} {config.ssh_key_path}\n' +
                          'Minecraft diagnostic\n' * 10000 + 'END token=fixture-secret\n')

            def launch(*args, **kwargs):
                kwargs['stderr'].write(diagnostic.encode('utf-8'))
                kwargs['stderr'].flush()
                kwargs['stdout'].write(b'full stdout diagnostic\n' * 10000)
                kwargs['stdout'].flush()
                return object()

            with patch('ai_task_minecraft_runtime.subprocess.Popen', side_effect=launch), patch(
                    'ai_task_minecraft_runtime.communicate_bounded', return_value=ProcessResult(1, '', '')):
                with self.assertRaises(DeploymentError) as error:
                    adapter.deploy(SHA)
            detail = str(error.exception)
            self.assertIn(f'BEGIN {config.ssh_host} {config.ssh_key_path}', detail)
            self.assertIn('END token=[redacted]', detail)
            self.assertNotIn('fixture-secret', detail)
            self.assertEqual(detail.count('Minecraft diagnostic\n'), 10000)
            self.assertEqual(detail.count('full stdout diagnostic\n'), 10000)

    def test_local_commit_validation_never_fetches(self):
        source = ExactMergeSource(
            Path("/tmp/runtime-source"),
            Path("/usr/bin/git"),
        )

        source.current_main_sha = Mock(return_value=SHA)
        source._fetch_main = Mock(
            side_effect=AssertionError(
                "idle catch-up must not fetch"
            )
        )
        source._run = Mock(
            side_effect=[
                SimpleNamespace(
                    returncode=0,
                    stdout="commit\n",
                    stderr="",
                ),
                SimpleNamespace(
                    returncode=0,
                    stdout="",
                    stderr="",
                ),
                SimpleNamespace(
                    returncode=0,
                    stdout=(
                        SHA
                        + " "
                        + ("b" * 40)
                        + "\n"
                    ),
                    stderr="",
                ),
            ]
        )

        source._require_commit(
            SHA,
            refresh_source=False,
        )

        source._fetch_main.assert_not_called()
        source.current_main_sha.assert_called_once_with()

        stale = "c" * 40
        source.current_main_sha.reset_mock(
            return_value=True
        )
        source.current_main_sha.return_value = SHA

        with self.assertRaises(DeploymentError):
            source._require_commit(
                stale,
                refresh_source=False,
            )

        source._fetch_main.assert_not_called()

    def test_changed_files_uses_exact_first_parent(self):
        source = ExactMergeSource(
            Path("/tmp/runtime-source"),
            Path("/usr/bin/git"),
        )

        source._require_commit = Mock()
        source._run = Mock(
            return_value=SimpleNamespace(
                returncode=0,
                stdout=(
                    "minecraft/behavior_packs/a/manifest.json"
                    "\0bot/main.py\0"
                ),
                stderr="",
            )
        )

        files = source.changed_files(SHA)

        self.assertEqual(
            files,
            [
                "minecraft/behavior_packs/a/manifest.json",
                "bot/main.py",
            ],
        )

        source._run.assert_called_once_with(
            (
                "diff",
                "--name-only",
                "-z",
                SHA + "^1",
                SHA,
                "--",
            )
        )

    def test_changed_files_rejects_unsafe_path(self):
        source = ExactMergeSource(
            Path("/tmp/runtime-source"),
            Path("/usr/bin/git"),
        )

        source._require_commit = Mock()
        source._run = Mock(
            return_value=SimpleNamespace(
                returncode=0,
                stdout="minecraft/../escape\0",
                stderr="",
            )
        )

        with self.assertRaises(DeploymentError):
            source.changed_files(SHA)

    def test_pack_archive_is_validated_before_transport(self):
        raw = pack_archive()

        source = ExactMergeSource(
            Path("/tmp/runtime-source"),
            Path("/usr/bin/git"),
        )

        source._require_commit = Mock()
        source._run = Mock(
            return_value=SimpleNamespace(
                returncode=0,
                stdout=raw,
                stderr=b"archive warning",
            )
        )

        returned = source.pack_archive(SHA)

        self.assertEqual(returned, raw)
        files, digest = prepare_pack_archive(returned)
        self.assertIn(PACK + "manifest.json", files)
        self.assertEqual(len(digest), 64)

    def test_proof_requires_success_exact_sha_and_hash(self):
        tree_hash = "b" * 64

        good = (
            "MINECRAFT_DEPLOY_RESULT=SUCCESS\n"
            f"DEPLOYED_COMMIT_SHA={SHA}\n"
            f"MINECRAFT_TREE_SHA256={tree_hash}\n"
            "MINECRAFT_CHANGED=1\n"
        )

        proof = parse_proof(good, SHA, tree_hash)

        self.assertEqual(proof.deployed_commit_sha, SHA)
        self.assertEqual(
            proof.verified_targets,
            frozenset({"minecraft"}),
        )

        bad_values = (
            '',
            good.replace('SUCCESS', 'FAILED'),
            good.replace(SHA, "c" * 40),
            good.replace(tree_hash, "d" * 64),
            good.replace(f'MINECRAFT_TREE_SHA256={tree_hash}\n', ''),
            good + 'MINECRAFT_DEPLOY_RESULT=FAILED\n',
        )

        for value in bad_values:
            with self.subTest(value=value):
                with self.assertRaises(DeploymentError):
                    parse_proof(value, SHA, tree_hash)
        for value in (good + 'EXTRA=diagnostic\n', 'preflight ok\n' + good,
                      good.rstrip('\n'), good * 2, good.replace('MINECRAFT_CHANGED=1', 'MINECRAFT_CHANGED=yes'),
                      good.replace('MINECRAFT_CHANGED=1\n', '')):
            self.assertEqual(parse_proof(value, SHA, tree_hash).deployed_commit_sha, SHA)
        with self.assertRaises(DeploymentError) as error:
            parse_proof('BDS did not start', SHA, tree_hash, stderr='container exited password=fixture-secret')
        self.assertIn('BDS did not start', str(error.exception))
        self.assertIn('container exited', str(error.exception))
        self.assertNotIn('fixture-secret', str(error.exception))


if __name__ == "__main__":
    unittest.main()
