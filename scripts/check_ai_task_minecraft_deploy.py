"""Offline checks only: no SSH, credentials, Docker or live filesystem data."""
import base64
import ast
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ai_task_deploy import DeploymentResult as AppDeploymentResult
from ai_task_deploy_config import DeploymentError
from ai_task_minecraft_deploy import (
    DeploymentResult, required_targets, verify_deployment,
    TargetDeployAdapter, prepare_pack_archive, sync_world_json,
)

SHA = "a" * 40
UUID = "12345678-1234-1234-1234-123456789abc"
OTHER_UUID = "12345678-1234-1234-1234-123456789def"
MANIFEST = json.dumps({"header": {"uuid": UUID, "version": [1, 0, 30]}}).encode()
PACK = "minecraft/resource_packs/arbitrary_pack/"


def archive(extra=(), manifest=MANIFEST):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as tar:
        for name, data, kind in [(PACK + "manifest.json", manifest, tarfile.REGTYPE), *extra]:
            info = tarfile.TarInfo(name)
            info.type = kind
            info.size = len(data) if kind == tarfile.REGTYPE else 0
            if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                info.linkname = "../../outside"
            tar.addfile(info, io.BytesIO(data))
    return stream.getvalue()


def remote_namespace():
    source = Path(__file__).with_name('ai_task_minecraft_deploy_remote.py')
    tree = ast.parse(source.read_text(encoding='utf-8'))
    tree.body = [node for node in tree.body if not isinstance(node, ast.Try)
                 and not (isinstance(node, ast.Import) and any(a.name == 'fcntl' for a in node.names))]
    namespace = {'fcntl': SimpleNamespace(flock=Mock(), LOCK_EX=2)}
    exec(compile(tree, str(source), 'exec'), namespace)
    return namespace


class MinecraftChecks(unittest.TestCase):
    def test_deployment_directory_links_are_allowed(self):
        remote = remote_namespace()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp).resolve()
            with patch.object(Path, 'is_symlink', return_value=True):
                remote['validate_normal_dir'](path)
            self.assertTrue(remote['current_tree_matches'](path, {}))

    def test_remote_transaction_preserves_world_and_restores_failed_app(self):
        for fail_start, fail_rollback in ((False, False), (True, False), (True, True)):
            with self.subTest(fail_start=fail_start, fail_rollback=fail_rollback), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                data = root / 'data'
                world = data / 'worlds/world'
                live = data / 'resource_packs/arbitrary_pack'
                for path in (data / 'behavior_packs', world, live, root / 'home'):
                    path.mkdir(parents=True, exist_ok=True)
                old_manifest = MANIFEST.replace(b'30', b'29')
                (live / 'manifest.json').write_bytes(old_manifest)
                (live / 'old.txt').write_bytes(b'old')
                (world / 'level.dat').write_bytes(b'world data must survive')
                (world / 'db').mkdir()
                (world / 'db/000001.ldb').write_bytes(b'world database must survive')
                unrelated = data / 'resource_packs/unmanaged'
                unrelated.mkdir()
                (unrelated / 'web.png').write_bytes(b'unmanaged asset')
                for kind in ('behavior', 'resource'):
                    (world / f'world_{kind}_packs.json').write_bytes(b'[]\n')
                remote = remote_namespace()
                remote['ARCHIVE_B64'] = base64.b64encode(archive()).decode()
                remote['wait_stable'] = Mock()
                remote['inspect_container'] = Mock(return_value={'State': {'Running': True}})
                starts = 0

                def docker(args):
                    nonlocal starts
                    if args[0] == 'start':
                        starts += 1
                        if fail_start and (starts == 1 or fail_rollback):
                            raise RuntimeError('container start failed')
                    return ''

                remote['docker'] = Mock(side_effect=docker)
                with patch.object(sys, 'argv', ['remote', SHA, str(data), 'world', 'container']), \
                        patch.object(Path, 'home', return_value=root / 'home'), patch('sys.stdout', new_callable=io.StringIO) as output:
                    if fail_start:
                        with self.assertRaisesRegex(RuntimeError, 'container start failed'):
                            remote['main']()
                        self.assertEqual(output.getvalue(), '')
                        if not fail_rollback:
                            self.assertEqual((live / 'old.txt').read_bytes(), b'old')
                            self.assertEqual((live / 'manifest.json').read_bytes(), old_manifest)
                            self.assertEqual((world / 'world_resource_packs.json').read_bytes(), b'[]\n')
                    else:
                        remote['main']()
                        self.assertIn('MINECRAFT_CHANGED=1', output.getvalue())
                        self.assertEqual((live / 'manifest.json').read_bytes(), MANIFEST)
                        remote['docker'].reset_mock()
                        output.seek(0)
                        output.truncate()
                        remote['main']()
                        self.assertIn('MINECRAFT_CHANGED=0', output.getvalue())
                        remote['docker'].assert_not_called()
                self.assertEqual((world / 'level.dat').read_bytes(), b'world data must survive')
                self.assertEqual((world / 'db/000001.ldb').read_bytes(), b'world database must survive')
                self.assertEqual((unrelated / 'web.png').read_bytes(), b'unmanaged asset')
                self.assertEqual(list(data.glob('.ichiyon-ai-stage-*')), [])
                self.assertEqual(len(list(data.glob('.ichiyon-ai-backup-*'))), int(fail_rollback))

    def test_remote_docker_failure_keeps_stderr(self):
        remote = remote_namespace()
        with patch.object(subprocess, 'run', return_value=SimpleNamespace(
                returncode=5, stdout='', stderr='daemon connection refused')) as run:
            with self.assertRaisesRegex(RuntimeError, 'exit=5.*daemon connection refused'):
                remote['docker'](['inspect', 'fixture'])
        self.assertEqual(run.call_args.kwargs['stderr'], subprocess.PIPE)

    def test_git_catch_up_never_touches_web_managed_packs(self):
        import ast
        source = Path(__file__).with_name('ai_task_minecraft_deploy_remote.py')
        tree = ast.parse(source.read_text(encoding='utf-8'))
        # Load the actual transaction without its entrypoint or OS-specific lock
        # import, then execute main against a temporary filesystem and no Docker.
        tree.body = [node for node in tree.body if not isinstance(node, ast.Try)
                     and not (isinstance(node, ast.Import) and any(a.name == 'fcntl' for a in node.names))]
        namespace = {'fcntl':SimpleNamespace(flock=Mock(), LOCK_EX=2)}
        exec(compile(tree, str(source), 'exec'), namespace)
        namespace['ARCHIVE_B64'] = base64.b64encode(archive()).decode()
        read_state = namespace['read_state'] = Mock(side_effect=AssertionError('legacy path reached'))
        docker = namespace['docker'] = Mock()
        health = namespace['wait_stable'] = Mock()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / 'data'
            for path in (data/'behavior_packs',data/'resource_packs',data/'worlds/world',root/'home',root/'cosmetics-applications'):
                path.mkdir(parents=True, exist_ok=True)
            texture = data/'resource_packs/user-skin.png'
            texture.write_bytes(b'keep uploaded image')
            marker = root/'cosmetics-applications/active.json'
            with patch.object(sys, 'argv', ['remote',SHA,str(data),'world','container']), patch.object(Path, 'home', return_value=root/'home'):
                marker.write_text('{}', encoding='utf-8')
                with self.assertRaisesRegex(RuntimeError, 'deployment rejected'):
                    namespace['main']()
                read_state.assert_not_called(); docker.assert_not_called(); health.assert_not_called()
                self.assertEqual(texture.read_bytes(), b'keep uploaded image')
                marker.unlink()
                marker.mkdir()  # A damaged marker also fails closed.
                with self.assertRaisesRegex(RuntimeError, 'deployment rejected'):
                    namespace['main']()
                read_state.assert_not_called(); docker.assert_not_called()
                marker.rmdir()
                with self.assertRaisesRegex(AssertionError, 'legacy path reached'):
                    namespace['main']()
                read_state.assert_called_once()

    def test_world_sync(self):
        existing = [{"pack_id": UUID, "version": [1, 0, 29], "extra": True},
                    {"pack_id": OTHER_UUID, "version": [2, 3, 4]}]
        synced = json.loads(sync_world_json(json.dumps(existing), [MANIFEST]))
        self.assertEqual(synced, [{**existing[0], "version": [1, 0, 30]}, existing[1]])
        self.assertEqual(json.loads(sync_world_json(b"[]", [MANIFEST])),
                         [{"pack_id": UUID, "version": [1, 0, 30]}])
        self.assertEqual(sync_world_json(json.dumps(synced), [MANIFEST]),
                         sync_world_json(json.dumps(existing), [MANIFEST]))

    def test_malformed_json(self):
        for value in (b"{", b"{}", b"null", b"[NaN]", b"[1]", b'[{"pack_id":"x"}]',
                      b'[{"pack_id":1,"pack_id":2}]', b"\xff"):
            with self.subTest(value=value), self.assertRaises(DeploymentError):
                sync_world_json(value, [MANIFEST])
        for version in ([True, 0, 0], [-1, 0, 0], [1, 2], "1.0.0"):
            with self.assertRaises(DeploymentError):
                sync_world_json(b"[]", [json.dumps({"header": {"uuid": UUID, "version": version}})])
        with self.assertRaises(DeploymentError):
            sync_world_json(b"[]", [MANIFEST, MANIFEST])

    def test_archive_identity_and_path_rejections(self):
        files, digest = prepare_pack_archive(archive())
        self.assertEqual(files, {PACK + "manifest.json": MANIFEST})
        self.assertEqual(digest, prepare_pack_archive(archive())[1])
        self.assertNotEqual(digest, prepare_pack_archive(archive([(PACK + "new.txt", b"x", tarfile.REGTYPE)]))[1])
        for name in ("../outside", "/absolute", PACK + "../escape", PACK + "x/../../escape",
                     "minecraft/server.properties", PACK + "./a"):
            with self.subTest(name=name), self.assertRaises(DeploymentError):
                prepare_pack_archive(archive([(name, b"x", tarfile.REGTYPE)]))
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE):
            with self.assertRaises(DeploymentError):
                prepare_pack_archive(archive([(PACK + "link", b"", kind)]))
        with self.assertRaises(DeploymentError):
            prepare_pack_archive(archive(manifest=b"{}"))
        with self.assertRaises(DeploymentError):
            prepare_pack_archive(archive([(PACK + "manifest.json", MANIFEST, tarfile.REGTYPE)]))

    def test_pack_paths_and_hashes_match_remote(self):
        remote = remote_namespace()
        names = ('textures/space name.png', 'textures/\u65e5\u672c\u8a9e.png', '.hidden',
                 'file:stream', 'a\\b', "punctuation'$%.txt", 'trailing.', 'line\nbreak')
        raw = archive([(PACK + name, b'asset', tarfile.REGTYPE) for name in names])
        files, digest = prepare_pack_archive(raw)
        remote_files, packs, remote_digest = remote['parse_archive'](raw)
        self.assertEqual(files, remote_files)
        self.assertEqual(digest, remote_digest)
        state = remote['desired_state'](packs, digest)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.json'
            path.write_bytes(remote['canonical_json'](state))
            self.assertEqual(remote['read_state'](path), state)

    def test_pack_archive_has_no_policy_size_or_count_limit(self):
        remote = remote_namespace()
        raw = archive([(PACK + 'large.bin', b'x' * (64 * 1024 * 1024 + 1), tarfile.REGTYPE)])
        files, digest = prepare_pack_archive(raw)
        remote_files, _, remote_digest = remote['parse_archive'](raw)
        self.assertEqual(len(files[PACK + 'large.bin']), 64 * 1024 * 1024 + 1)
        self.assertEqual(digest, remote_digest)
        del raw, files, remote_files
        raw = archive([(PACK + str(i), b'', tarfile.REGTYPE) for i in range(10001)])
        self.assertEqual(len(prepare_pack_archive(raw)[0]), 10002)
        self.assertEqual(len(remote['parse_archive'](raw)[0]), 10002)

    def adapter(self, changed):
        source = Mock()
        source.changed_files.return_value = changed
        source.current_main_sha.return_value = SHA
        apps = Mock()
        apps.deploy.return_value = AppDeploymentResult(SHA, "apps verified")
        bds = Mock()
        bds.deploy.return_value = DeploymentResult(SHA, "BDS verified", frozenset({"minecraft"}))
        factory = Mock(return_value=bds)
        return TargetDeployAdapter(apps, source, factory), apps, bds, factory

    def test_non_minecraft_never_initializes_bds(self):
        adapter, apps, bds, factory = self.adapter(["bot/music.py", "admin/main.py", "docs/minecraft.md"])
        factory.side_effect = DeploymentError("BDS configuration absent")
        verify_deployment(adapter.deploy(SHA), SHA, frozenset({"apps"}))
        factory.assert_not_called()
        bds.deploy.assert_not_called()
        apps.deploy.assert_called_once_with(SHA, stop_event=None)

    def test_minecraft_required_and_failure_blocks_completion(self):
        adapter, _, bds, factory = self.adapter([PACK + "manifest.json"])
        proof = adapter.deploy(SHA)
        verify_deployment(proof, SHA, required_targets([PACK + "manifest.json"]))
        factory.assert_called_once_with()
        bds.deploy.assert_called_once_with(SHA, stop_event=None)
        for failure in (DeploymentError("restart failed"), DeploymentError("health failed")):
            bds.deploy.side_effect = failure
            with self.assertRaises(DeploymentError):
                adapter.deploy(SHA)

    def test_proof_missing_wrong_sha_or_target(self):
        for proof in (SimpleNamespace(deployed_commit_sha=SHA),
                      DeploymentResult("b" * 40, "ok"), DeploymentResult(SHA, "manual work remains")):
            with self.assertRaises(DeploymentError):
                verify_deployment(proof, SHA, frozenset({"apps", "minecraft"}))

    def test_existing_app_proof_cannot_prove_minecraft(self):
        proof = AppDeploymentResult(SHA, "apps verified")
        verify_deployment(proof, SHA, frozenset({"apps"}))
        with self.assertRaises(DeploymentError):
            verify_deployment(proof, SHA, frozenset({"apps", "minecraft"}))

    def test_invalid_changed_paths_fail_before_deployment(self):
        for paths in (None, "minecraft/a", ["/minecraft/a"], ["minecraft/../a"],
                      ["minecraft//a"], [None]):
            adapter, apps, bds, factory = self.adapter(paths)
            with self.subTest(paths=paths), self.assertRaises(DeploymentError):
                adapter.deploy(SHA)
            apps.deploy.assert_not_called()
            factory.assert_not_called()

    def test_catch_up_failure_isolated_and_retried(self):
        adapter, apps, bds, _ = self.adapter(["bot/main.py"])
        bds.deploy.side_effect = DeploymentError("offline")
        self.assertFalse(adapter.catch_up())
        verify_deployment(adapter.deploy(SHA), SHA, frozenset({"apps"}))
        bds.deploy.side_effect = None
        self.assertTrue(adapter.catch_up())
        self.assertEqual(bds.deploy.call_count, 2)
        for call in bds.deploy.call_args_list:
            self.assertEqual(call.args, (SHA,))
            self.assertEqual(
                call.kwargs,
                {
                    "stop_event": None,
                    "refresh_source": False,
                },
            )
        self.assertEqual(apps.deploy.call_count, 1)

    def test_stopped_never_deploys(self):
        adapter, apps, bds, _ = self.adapter([PACK + "manifest.json"])
        stop = threading.Event()
        stop.set()
        with self.assertRaises(DeploymentError):
            adapter.deploy(SHA, stop_event=stop)
        self.assertFalse(adapter.catch_up(stop_event=stop))
        apps.deploy.assert_not_called()
        bds.deploy.assert_not_called()


    def test_remote_atomic_paths_reject_dangling_symlinks(self):
        remote_path = Path(__file__).with_name(
            "ai_task_minecraft_deploy_remote.py"
        )

        import ast

        tree = ast.parse(
            remote_path.read_text(encoding="utf-8"),
            filename=str(remote_path),
        )

        wanted = {
            "fail",
            "read_state",
            "write_atomic",
        }

        functions = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name in wanted
        ]

        self.assertEqual(
            {node.name for node in functions},
            wanted,
        )

        namespace = {"os": os}

        exec(
            compile(
                ast.Module(
                    body=functions,
                    type_ignores=[],
                ),
                str(remote_path),
                "exec",
            ),
            namespace,
        )

        write_atomic = namespace["write_atomic"]
        read_state = namespace["read_state"]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside.json"
            state = root / ".ichiyon-ai-managed-packs.json"
            atomic_tmp = root / (
                ".ichiyon-ai-managed-packs.json.ichiyon-tmp"
            )

            try:
                atomic_tmp.symlink_to(outside)
            except OSError as exc:
                if getattr(exc, 'winerror', None) == 1314:
                    self.skipTest('Windows symlink privilege unavailable')
                raise

            with self.assertRaises(RuntimeError):
                write_atomic(state, b"{}\n")

            self.assertFalse(outside.exists())
            self.assertTrue(atomic_tmp.is_symlink())
            self.assertFalse(state.exists())

            atomic_tmp.unlink()
            state.symlink_to(outside)

            with self.assertRaises(RuntimeError):
                read_state(state)

            self.assertFalse(outside.exists())
            self.assertTrue(state.is_symlink())


    @unittest.skipIf(os.name == 'nt', 'POSIX fake executable and fcntl integration')
    def test_remote_rollback_failure_preserves_backup(self):
        """A failed rollback must retain the original pack backup for recovery."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            behavior = data / "behavior_packs"
            resource = data / "resource_packs"
            world = data / "worlds" / "world"
            home = root / "home"
            fake_bin = root / "bin"

            for directory in (
                behavior,
                resource,
                world,
                home,
                fake_bin,
            ):
                directory.mkdir(parents=True, exist_ok=True)

            live_pack = resource / "arbitrary_pack"
            live_pack.mkdir()

            old_manifest = json.dumps(
                {
                    "header": {
                        "uuid": UUID,
                        "version": [1, 0, 29],
                    }
                }
            ).encode()

            (live_pack / "manifest.json").write_bytes(
                old_manifest
            )
            (live_pack / "old.txt").write_bytes(b"old")

            (world / "world_behavior_packs.json").write_text(
                "[]\n",
                encoding="utf-8",
            )
            (world / "world_resource_packs.json").write_text(
                json.dumps(
                    [
                        {
                            "pack_id": UUID,
                            "version": [1, 0, 29],
                        }
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            raw_archive = archive(
                [
                    (
                        PACK + "new.txt",
                        b"new",
                        tarfile.REGTYPE,
                    )
                ]
            )

            remote_path = Path(__file__).with_name(
                "ai_task_minecraft_deploy_remote.py"
            )
            template = remote_path.read_text(
                encoding="utf-8"
            )

            placeholder = "__ICHYON_ARCHIVE_BASE64__"
            self.assertEqual(
                template.count(placeholder),
                1,
            )

            rendered = template.replace(
                placeholder,
                base64.b64encode(
                    raw_archive
                ).decode("ascii"),
            )

            # Keep the production stability algorithm intact while making
            # this isolated fake-runtime test complete immediately.
            self.assertIn("time.sleep(5)", rendered)
            rendered = rendered.replace(
                "time.sleep(5)",
                "time.sleep(0)",
            )

            fake_docker = fake_bin / "docker"
            fake_docker.write_text(
                """#!/usr/bin/env python3
import json
import os
import sys

state = os.environ["FAKE_DOCKER_STATE"]

try:
    count = int(open(state, encoding="utf-8").read())
except (FileNotFoundError, ValueError):
    count = 0

args = sys.argv[1:]

if args and args[0] == "inspect":
    count += 1
    with open(state, "w", encoding="utf-8") as stream:
        stream.write(str(count))

    # Initial health verification consumes three inspect calls.
    # The fourth confirms the container was running before mutation.
    # The fifth is rollback's first inspect and deliberately fails.
    if count >= 5:
        sys.exit(1)

    print(json.dumps([{
        "State": {
            "Running": True,
            "Health": {"Status": "healthy"},
        }
    }]))
    sys.exit(0)

if args and args[0] == "stop":
    sys.exit(0)

# The first start fails, causing the primary transaction to enter
# rollback. Rollback then fails at its inspect above.
if args and args[0] == "start":
    sys.exit(1)

sys.exit(1)
""",
                encoding="utf-8",
            )
            fake_docker.chmod(0o755)

            env = os.environ.copy()
            env["PATH"] = (
                str(fake_bin)
                + os.pathsep
                + env.get("PATH", "")
            )
            env["HOME"] = str(home)
            env["FAKE_DOCKER_STATE"] = str(
                root / "docker-state"
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    rendered,
                    SHA,
                    str(data),
                    "world",
                    "minecraft-bedrock",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=20,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")

            stages = list(
                data.glob(".ichiyon-ai-stage-*")
            )
            backups = list(
                data.glob(".ichiyon-ai-backup-*")
            )

            self.assertEqual(stages, [])
            self.assertEqual(len(backups), 1)

            preserved = (
                backups[0]
                / "resource_packs"
                / "arbitrary_pack"
            )

            self.assertEqual(
                (preserved / "manifest.json").read_bytes(),
                old_manifest,
            )
            self.assertEqual(
                (preserved / "old.txt").read_bytes(),
                b"old",
            )


if __name__ == "__main__":
    unittest.main()
