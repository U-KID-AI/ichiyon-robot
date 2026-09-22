"""Offline checks for trusted Minecraft runtime transport.

No SSH connection, Docker daemon, credentials, or production BDS is used.
"""

import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from ai_task_deploy_config import DeploymentSafetyError
from ai_task_minecraft_runtime import (
    ExactMergeSource,
    MinecraftDeployConfig,
    parse_proof,
)
from ai_task_minecraft_deploy import prepare_pack_archive


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
                    with self.assertRaises(DeploymentSafetyError):
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
                    SimpleNamespace(
                        returncode=0,
                        stdout="",
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
                    ("status", "--porcelain=v1"),
                ],
            )

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

        with self.assertRaises(DeploymentSafetyError):
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

        with self.assertRaises(DeploymentSafetyError):
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
                stderr=b"",
            )
        )

        returned = source.pack_archive(SHA)

        self.assertEqual(returned, raw)
        files, digest = prepare_pack_archive(returned)
        self.assertIn(PACK + "manifest.json", files)
        self.assertEqual(len(digest), 64)

    def test_proof_requires_exact_sha_hash_and_changed_flag(self):
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
            good.replace(SHA, "c" * 40),
            good.replace(tree_hash, "d" * 64),
            good.replace(
                "MINECRAFT_CHANGED=1",
                "MINECRAFT_CHANGED=yes",
            ),
            good + "EXTRA=unexpected\n",
        )

        for value in bad_values:
            with self.subTest(value=value):
                with self.assertRaises(DeploymentSafetyError):
                    parse_proof(value, SHA, tree_hash)


if __name__ == "__main__":
    unittest.main()
