"""Offline publishing regression tests using real worktrees and local bare remotes."""

import io
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from ai_task_git import GitAdapter, GitDiffCheckError, GitOperationError
from ai_task_publish import GitPublisher, PublishDiffCheckError, PublishError


TASK_ID = UUID("00000000-0000-0000-0000-000000000001")


class PublishTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        git = shutil.which("git")
        if not git:
            raise RuntimeError("git is required for publishing regression checks")
        cls.git_path = Path(git).resolve()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ai-publish-check-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.source = self.directory / "source"
        self.remote = self.directory / "remote.git"
        self.hooks = self.directory / "hooks"
        self.source.mkdir()
        self.hooks.mkdir()
        self.commands = []
        self.git("init", "--bare", str(self.remote), cwd=self.directory)
        self.git("init", cwd=self.source)
        self.git("symbolic-ref", "HEAD", "refs/heads/main")
        for key, value in (
            ("user.name", "AI Publisher Test"),
            ("user.email", "publisher-test@invalid.local"),
            ("commit.gpgsign", "false"),
            ("tag.gpgsign", "false"),
            ("push.gpgsign", "false"),
            ("core.hooksPath", self.hooks.as_posix()),
            ("core.autocrlf", "false"),
            ("core.safecrlf", "false"),
            ("core.whitespace", "trailing-space,space-before-tab"),
            ("diff.renames", "true"),
            ("merge.ff", "true"),
        ):
            self.git("config", key, value)
        self.git("config", "core.hooksPath", self.hooks.as_posix(), cwd=self.remote)
        self.git("remote", "add", "origin", str(self.remote))
        self.write(self.source, "tracked.txt", b"BASE\n")
        self.write(self.source, "deleted.txt", b"DELETE\n")
        self.write(self.source, "rename-old.txt", b"KEEP THIS RENAME CONTENT\n")
        self.git("add", "-A")
        self.git("commit", "-m", "baseline")
        self.base = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("push", "origin", "HEAD:refs/heads/main")
        self.adapter = GitAdapter(self.source, self.git_path)
        self.worktree = self.adapter.add_worktree(TASK_ID, self.directory / "worktrees", self.base)
        self.publisher = GitPublisher(self.git_path, runner=self.record_run, popen=self.record_popen)

    def git(self, *args, cwd=None, check=True, binary=False):
        options = {} if binary else {"encoding": "utf-8", "errors": "replace"}
        result = subprocess.run(
            [str(self.git_path), *args], cwd=str(cwd or self.source), shell=False,
            capture_output=True, text=not binary, timeout=30, check=False, **options,
        )
        if check and result.returncode:
            self.fail(f"git {args!r} failed ({result.returncode})\n{result.stdout}\n{result.stderr}")
        return result

    def record_run(self, argv, **kwargs):
        self.commands.append((argv[1:], kwargs))
        return subprocess.run(argv, **kwargs)

    def record_popen(self, argv, **kwargs):
        self.commands.append((argv[1:], kwargs))
        return subprocess.Popen(argv, **kwargs)

    @staticmethod
    def write(root, relative, data):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def head(self):
        return self.git("rev-parse", "HEAD", cwd=self.worktree).stdout.strip()

    def blob(self, relative, revision="HEAD"):
        return self.git("show", f"{revision}:{relative}", cwd=self.worktree, binary=True).stdout

    def test_normal_add_commit_push_all_paths_and_real_index(self):
        self.write(self.worktree, "tracked.txt", b"CHANGED\r\n")
        self.write(self.worktree, "new file.txt", b"NEW\r\n")
        (self.worktree / "deleted.txt").unlink()
        self.git("mv", "rename-old.txt", "rename new.txt", cwd=self.worktree)
        extras = {
            ".gitattributes": b"*.txt text eol=lf\nassets/*.txt -text -diff\n",
            "AGENTS.md": b"new instructions\n",
            ".codex/config.toml": b"value = true\n",
            ".github/workflows/check.yml": b"name: test\n",
            "scripts/ai_task_publish.py": b"print('updated')\n",
            "assets/opaque.txt": b"\xff\xfe\0\r\n",
            "-leading-dash.txt": b"allowed\n",
            "nested/\u65e5\u672c\u8a9e.txt": b"allowed\n",
            ".gitignore": b"ignored.txt\n",
        }
        for name, value in extras.items():
            self.write(self.worktree, name, value)
        self.write(self.worktree, "ignored.txt", b"normal gitignore semantics\n")
        expected = set(extras) | {"tracked.txt", "new file.txt", "deleted.txt", "rename-old.txt", "rename new.txt"}
        self.assertEqual(set(self.adapter.changed_files(self.worktree, self.base)), expected)
        self.assertEqual(set(self.adapter.staged_files(self.worktree)), {"rename-old.txt", "rename new.txt"})
        # Deliberately stale input must not restrict what Git stages or commits.
        result = self.publisher.commit(self.worktree, TASK_ID, self.base, ["no-longer-present.txt"])
        self.assertEqual(self.head(), result.commit_sha)
        self.assertEqual(self.git("log", "-1", "--format=%an <%ae>", cwd=self.worktree).stdout.strip(),
                         "AI Publisher Test <publisher-test@invalid.local>")
        self.assertNotEqual(result.commit_sha, self.base)
        self.assertEqual(result.tree_sha, self.git("rev-parse", "HEAD^{tree}", cwd=self.worktree).stdout.strip())
        self.assertEqual(set(result.changed_files), expected)
        self.assertEqual(self.adapter.changed_files(self.worktree), [])
        self.assertEqual(self.adapter.staged_files(self.worktree), [])
        self.assertEqual(set(self.adapter.changed_files(self.worktree, self.base)), expected)
        self.assertIn("tracked.txt", self.adapter.diff_stat(self.worktree, self.base))
        self.assertIn("+CHANGED", self.adapter.diff(self.worktree, self.base))
        self.assertEqual(self.blob("tracked.txt"), b"CHANGED\n")
        self.assertEqual(self.blob("new file.txt"), b"NEW\n")
        self.assertEqual(self.blob("assets/opaque.txt"), extras["assets/opaque.txt"])
        self.assertEqual(self.blob("rename new.txt"), b"KEEP THIS RENAME CONTENT\n")
        self.assertNotEqual(self.git("cat-file", "-e", "HEAD:deleted.txt", cwd=self.worktree, check=False).returncode, 0)
        self.assertNotEqual(self.git("cat-file", "-e", "HEAD:ignored.txt", cwd=self.worktree, check=False).returncode, 0)
        self.adapter.validate_worktree(TASK_ID, self.worktree, self.base)
        self.assertEqual(self.adapter.snapshot(self.worktree).head, result.commit_sha)
        self.assertEqual(self.publisher.push_task_branch(self.worktree, TASK_ID, result.commit_sha), result.commit_sha)
        self.assertEqual(self.git("rev-parse", GitAdapter.expected_branch(TASK_ID), cwd=self.remote).stdout.strip(), result.commit_sha)
        commands = [args for args, _ in self.commands]
        self.assertIn(["add", "-A"], commands)
        self.assertIn(["diff", "--cached", "--check"], commands)
        self.assertIn(["commit", "-m", f"chore(ai): task {TASK_ID}"], commands)
        self.assertIn(["push", "origin", f"HEAD:refs/heads/{GitAdapter.expected_branch(TASK_ID)}"], commands)
        self.assertFalse(any(args[0] in {"hash-object", "read-tree", "write-tree", "commit-tree", "update-index", "restore", "reset"} for args in commands))
        self.assertTrue(all(options["env"].get("GIT_INDEX_FILE") == os.environ.get("GIT_INDEX_FILE")
                            for _, options in self.commands))
        self.assertFalse(any("--no-verify" in args or any(arg.startswith("--force") for arg in args) for args in commands))

    def test_whitespace_failure_preserves_index_and_retry_restages_repairs(self):
        self.write(self.worktree, "tracked.txt", b"tracked trailing whitespace   \n")
        self.write(self.worktree, "new.txt", b"new trailing whitespace   \n")
        with self.assertRaises(PublishDiffCheckError) as caught:
            self.publisher.commit(self.worktree, TASK_ID, self.base)
        error = caught.exception
        self.assertIn("tracked.txt", error.stdout)
        self.assertIn("new.txt", error.diagnostics)
        self.assertIn("trailing whitespace", str(error))
        self.assertEqual(self.head(), self.base)
        self.assertEqual(self.blob("new.txt", ":0"), b"new trailing whitespace   \n")
        self.assertEqual(set(self.adapter.staged_files(self.worktree)), {"tracked.txt", "new.txt"})
        self.write(self.worktree, "tracked.txt", b"tracked repaired\n")
        self.write(self.worktree, "new.txt", b"new repaired\n")
        self.publisher.validate_candidate_diff_check(self.worktree, self.base, [])
        self.assertEqual(self.blob("new.txt", ":0"), b"new repaired\n")
        result = self.publisher.commit(self.worktree, TASK_ID, self.base)
        self.assertEqual(self.blob("tracked.txt"), b"tracked repaired\n")
        self.assertEqual(self.blob("new.txt"), b"new repaired\n")
        self.assertEqual(self.adapter.changed_files(self.worktree), [])
        self.assertEqual(self.publisher.push_task_branch(self.worktree, TASK_ID, result.commit_sha), result.commit_sha)

    def test_clean_retries_reuse_codex_commit_and_can_advance_remote(self):
        self.assertEqual(self.publisher.commit(self.worktree, TASK_ID, self.base).commit_sha, self.base)
        self.write(self.worktree, "codex.txt", b"already committed\n")
        self.git("add", "-A", cwd=self.worktree)
        self.git("commit", "-m", "Codex implementation", cwd=self.worktree)
        codex_head = self.head()
        stopped = threading.Event()
        result = self.publisher.commit(self.worktree, TASK_ID, self.base, stop_event=stopped)
        self.assertEqual(result.commit_sha, codex_head)
        self.assertEqual(result.changed_files, ("codex.txt",))
        self.adapter.validate_worktree(TASK_ID, self.worktree, self.base)
        for _ in range(2):
            self.assertEqual(self.publisher.push_task_branch(self.worktree, TASK_ID, codex_head, stop_event=stopped), codex_head)
        self.write(self.worktree, "repair.txt", b"CI repair\n")
        repaired = self.publisher.commit(self.worktree, TASK_ID, self.base, stop_event=stopped)
        self.assertNotEqual(repaired.commit_sha, codex_head)
        self.assertEqual(self.publisher.push_task_branch(self.worktree, TASK_ID, repaired.commit_sha, stop_event=stopped), repaired.commit_sha)
        self.assertEqual(self.publisher.commit(self.worktree, TASK_ID, self.base), repaired)

    def test_file_count_and_size_are_not_publisher_policies(self):
        for index in range(2001):
            self.write(self.worktree, f"many/{index}.txt", b"allowed\n")
        self.write(self.worktree, "large.bin", b"\0" * (51 * 1024 * 1024))
        result = self.publisher.commit(self.worktree, TASK_ID, self.base, [])
        self.assertEqual(len(result.changed_files), 2002)
        self.assertEqual(int(self.git("cat-file", "-s", "HEAD:large.bin", cwd=self.worktree).stdout), 51 * 1024 * 1024)

    def test_normal_git_identity_environment_is_respected(self):
        identity = {
            "GIT_AUTHOR_NAME": "Environment Author", "GIT_AUTHOR_EMAIL": "author@invalid.local",
            "GIT_COMMITTER_NAME": "Environment Committer", "GIT_COMMITTER_EMAIL": "committer@invalid.local",
        }
        self.write(self.worktree, "identity.txt", b"identity from environment\n")
        with patch.dict(os.environ, identity):
            self.publisher.commit(self.worktree, TASK_ID, self.base)
        self.assertEqual(self.git("log", "-1", "--format=%an <%ae>%n%cn <%ce>", cwd=self.worktree).stdout,
                         "Environment Author <author@invalid.local>\nEnvironment Committer <committer@invalid.local>\n")

    def test_inherited_git_configuration_reaches_both_process_modes(self):
        global_config = self.directory / "global.gitconfig"
        global_config.write_text("[publisher]\n\tfixture = inherited-global\n", encoding="utf-8")
        configuration = {
            "GIT_CONFIG_GLOBAL": str(global_config),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "user.name", "GIT_CONFIG_VALUE_0": "Configured Author",
            "GIT_CONFIG_KEY_1": "user.email", "GIT_CONFIG_VALUE_1": "configured@invalid.local",
        }
        with patch.dict(os.environ, configuration):
            for number, stop_event in enumerate((None, threading.Event())):
                global_value = self.publisher._checked(("config", "--get", "publisher.fixture"),
                    cwd=self.worktree, stop_event=stop_event)
                self.assertEqual(global_value.stdout.strip(), "inherited-global")
                self.write(self.worktree, f"configured-{number}.txt", b"inherited Git configuration\n")
                self.publisher.commit(self.worktree, TASK_ID, self.base, stop_event=stop_event)
                identity = self.git("log", "-1", "--format=%an <%ae>", cwd=self.worktree).stdout.strip()
                self.assertEqual(identity, "Configured Author <configured@invalid.local>")

    @unittest.skipIf(os.name == "nt", "Windows filenames/symlinks depend on host privileges")
    def test_symlinks_and_unusual_posix_names_are_normal_git_paths(self):
        names = ["line\nbreak.txt", "tab\tname.txt", "colon:name.txt", "back\\slash.txt", 'quote"name.txt']
        for name in names:
            self.write(self.worktree, name, b"content\n")
        (self.worktree / "link").symlink_to("../outside-target")
        self.assertEqual(set(self.adapter.changed_files(self.worktree)), set(names) | {"link"})
        result = self.publisher.commit(self.worktree, TASK_ID, self.base)
        self.assertEqual(set(result.changed_files), set(names) | {"link"})
        self.assertEqual(self.blob("link"), b"../outside-target")
        self.assertTrue(self.git("ls-tree", "HEAD", "--", "link", cwd=self.worktree).stdout.startswith("120000"))

    def test_commit_hook_failure_reports_both_streams_and_preserves_changes(self):
        hook = self.hooks / "pre-commit"
        hook.write_bytes(b"#!/bin/sh\nprintf 'hook stdout\\n'\nprintf 'hook stderr\\n' >&2\nexit 1\n")
        hook.chmod(0o755)
        self.write(self.worktree, "tracked.txt", b"hook repair remains\n")
        with self.assertRaises(PublishError) as caught:
            self.publisher.commit(self.worktree, TASK_ID, self.base)
        self.assertIn("hook stdout", str(caught.exception))
        self.assertIn("hook stderr", str(caught.exception))
        self.assertEqual(self.head(), self.base)
        self.assertEqual(self.blob("tracked.txt", ":0"), b"hook repair remains\n")
        hook.unlink()
        self.assertNotEqual(self.publisher.commit(self.worktree, TASK_ID, self.base).commit_sha, self.base)

    def test_real_non_fast_forward_push_error_does_not_force_or_rollback(self):
        self.write(self.worktree, "task.txt", b"local task\n")
        result = self.publisher.commit(self.worktree, TASK_ID, self.base)
        self.write(self.source, "competing.txt", b"other history\n")
        self.git("add", "-A")
        self.git("commit", "-m", "competing history")
        competing = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("push", "origin", f"HEAD:refs/heads/{GitAdapter.expected_branch(TASK_ID)}")
        with self.assertRaises(PublishError) as caught:
            self.publisher.push_task_branch(self.worktree, TASK_ID, result.commit_sha)
        self.assertIn("[rejected]", caught.exception.stderr)
        self.assertIn("failed to push", str(caught.exception))
        self.assertEqual(self.head(), result.commit_sha)
        self.assertEqual(self.git("rev-parse", GitAdapter.expected_branch(TASK_ID), cwd=self.remote).stdout.strip(), competing)

    def test_integrate_main_merges_without_reset_and_supports_next_repair(self):
        self.write(self.worktree, "task.txt", b"first task\n")
        first = self.publisher.commit(self.worktree, TASK_ID, self.base)
        self.publisher.push_task_branch(self.worktree, TASK_ID, first.commit_sha)
        self.git("merge", "--no-ff", "--no-edit", GitAdapter.expected_branch(TASK_ID))
        self.write(self.source, "main-after-merge.txt", b"later main change\n")
        self.git("add", "-A")
        self.git("commit", "-m", "main followup")
        main_head = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("push", "origin", "main")
        self.write(self.worktree, "pending-repair.txt", b"preserve pending repair\n")
        integrated = self.adapter.integrate_main(self.worktree)
        self.assertEqual(integrated, main_head)
        self.assertEqual((self.worktree / "pending-repair.txt").read_bytes(), b"preserve pending repair\n")
        self.adapter.validate_worktree(TASK_ID, self.worktree, self.base)
        repaired = self.publisher.commit(self.worktree, TASK_ID, integrated)
        self.assertEqual(repaired.changed_files, ("pending-repair.txt",))
        self.assertEqual(self.publisher.push_task_branch(self.worktree, TASK_ID, repaired.commit_sha), repaired.commit_sha)

    def test_merge_conflicts_remain_repairable_with_full_diagnostics(self):
        self.write(self.worktree, "tracked.txt", b"TASK\n")
        first = self.publisher.commit(self.worktree, TASK_ID, self.base)
        self.write(self.source, "tracked.txt", b"MAIN\n")
        self.git("add", "-A")
        self.git("commit", "-m", "conflicting main")
        self.git("push", "origin", "main")
        with self.assertRaises(GitOperationError) as caught:
            self.adapter.integrate_main(self.worktree)
        self.assertIn("CONFLICT", caught.exception.stdout)
        self.assertIn("tracked.txt", str(caught.exception))
        self.assertEqual(self.head(), first.commit_sha)
        self.assertIn(b"<<<<<<<", (self.worktree / "tracked.txt").read_bytes())
        self.assertIn("tracked.txt", self.adapter.changed_files(self.worktree, self.base))
        self.adapter.validate_worktree(TASK_ID, self.worktree, self.base)
        # Resolving to our original content still needs a merge commit with two parents.
        self.write(self.worktree, "tracked.txt", b"TASK\n")
        repaired = self.publisher.commit(self.worktree, TASK_ID, self.base)
        self.assertEqual(len(self.git("rev-list", "--parents", "-n", "1", "HEAD", cwd=self.worktree).stdout.split()), 3)
        self.assertEqual(self.blob("tracked.txt"), b"TASK\n")
        self.assertEqual(self.publisher.push_task_branch(self.worktree, TASK_ID, repaired.commit_sha), repaired.commit_sha)

    def test_worktree_identity_stays_enforced_but_source_may_be_dirty(self):
        self.write(self.source, "source-pending.txt", b"independent work\n")
        self.adapter.require_source_repo()
        self.assertEqual(self.adapter.fetch_main(), self.base)
        self.adapter.validate_worktree(TASK_ID, self.worktree, self.base)
        self.git("checkout", "-b", "wrong-task-branch", cwd=self.worktree)
        with self.assertRaisesRegex(GitOperationError, "branch mismatch"):
            self.adapter.validate_worktree(TASK_ID, self.worktree, self.base)
        with self.assertRaisesRegex(PublishError, "branch mismatch"):
            self.publisher.commit(self.worktree, TASK_ID, self.base)
        foreign = self.directory / "foreign"
        foreign.mkdir()
        self.git("init", cwd=foreign)
        with self.assertRaisesRegex(GitOperationError, "different repository"):
            self.adapter.snapshot(foreign)
        nested = self.worktree / "subdirectory"
        nested.mkdir()
        with self.assertRaisesRegex(GitOperationError, "root mismatch"):
            self.adapter.snapshot(nested)

    def test_legacy_worktree_listing_keeps_real_worktree_integrity(self):
        calls = []

        def older_git(argv, **kwargs):
            calls.append(argv[1:])
            if argv[1:] == ["worktree", "list", "--porcelain", "-z"]:
                return SimpleNamespace(returncode=129, stdout="", stderr="error: unknown switch `z'\n")
            return subprocess.run(argv, **kwargs)

        adapter = GitAdapter(self.source, self.git_path, runner=older_git)
        task_id = UUID("00000000-0000-0000-0000-000000000002")
        worktree = adapter.add_worktree(task_id, self.directory / "legacy spaces \u65e5\u672c", self.base)
        snapshot = adapter.snapshot(worktree)
        self.assertEqual(snapshot.head, self.base)
        self.assertEqual(snapshot.branch, adapter.expected_branch(task_id))
        self.assertNotIn("\0", snapshot.worktrees)
        adapter.validate_worktree(task_id, worktree, self.base)
        self.assertIn(["worktree", "list", "--porcelain"], calls)
        self.git("checkout", "-b", "wrong-legacy-task", cwd=worktree)
        with self.assertRaisesRegex(GitOperationError, "branch mismatch"):
            adapter.validate_worktree(task_id, worktree, self.base)
        source_only = self.git("worktree", "list", "--porcelain").stdout.split("\n\n", 1)[0] + "\n\n"
        with patch.object(adapter, "_worktree_list", return_value=SimpleNamespace(stdout=source_only)):
            with self.assertRaisesRegex(GitOperationError, "snapshot validation failed"):
                adapter.snapshot(worktree)

    def test_symlinked_worktree_parent_is_accepted(self):
        alias = self.directory / "worktree-alias"
        try:
            alias.symlink_to(self.worktree.parent, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"host does not permit directory symlinks: {exc}")
        linked_worktree = alias / self.worktree.name
        self.adapter.validate_worktree(TASK_ID, linked_worktree, self.base)
        self.assertEqual(self.adapter.snapshot(linked_worktree).head, self.base)
        self.write(linked_worktree, "alias.txt", b"normal worktree alias\n")
        committed = self.publisher.commit(linked_worktree, TASK_ID, self.base)
        self.assertEqual(committed.commit_sha, self.head())
        self.adapter.validate_worktree(TASK_ID, linked_worktree, self.base)

    def test_git_errors_and_whitespace_feedback_include_real_output(self):
        self.write(self.worktree, "tracked.txt", b"bad whitespace   \n")
        with self.assertRaises(GitDiffCheckError) as caught:
            self.adapter.diff_check(self.worktree)
        self.assertIn("trailing whitespace", caught.exception.stdout)
        self.assertIn("tracked.txt", str(caught.exception))
        self.git("remote", "set-url", "origin", str(self.directory / "missing-remote"))
        with self.assertRaises(GitOperationError) as caught:
            self.adapter.fetch_main()
        self.assertIn("missing-remote", caught.exception.stderr)
        self.assertIn(caught.exception.stderr, str(caught.exception))

    def test_lease_loss_prevents_new_commands(self):
        stopped = threading.Event()
        stopped.set()
        self.write(self.worktree, "pending.txt", b"preserve\n")
        with self.assertRaisesRegex(PublishError, "lease loss"):
            self.publisher.commit(self.worktree, TASK_ID, self.base, stop_event=stopped)
        with self.assertRaisesRegex(PublishError, "lease loss"):
            self.publisher.push_task_branch(self.worktree, TASK_ID, self.base, stop_event=stopped)
        self.assertEqual(self.commands, [])
        self.assertEqual(self.adapter.staged_files(self.worktree), [])
        self.assertEqual((self.worktree / "pending.txt").read_bytes(), b"preserve\n")


class DiagnosticTests(unittest.TestCase):
    def test_worktree_z_fallback_is_specific_and_preserves_other_errors(self):
        for message in ("error: unknown switch `z'\n", "error: unknown switch 'z'\n",
                        'error: unknown option "-z"\n'):
            calls = []

            def legacy(argv, **kwargs):
                calls.append(argv[1:])
                return SimpleNamespace(returncode=129 if len(calls) == 1 else 0,
                                       stdout="" if len(calls) == 1 else "legacy output", stderr=message if len(calls) == 1 else "")

            with self.subTest(message=message):
                adapter = GitAdapter(ROOT, Path(sys.executable), runner=legacy)
                self.assertEqual(adapter._worktree_list().stdout, "legacy output")
                self.assertEqual(calls, [["worktree", "list", "--porcelain", "-z"],
                                         ["worktree", "list", "--porcelain"]])
        for code, message in ((128, "error: unknown switch 'z'\n"), (129, "fatal: permission denied\n"),
                              (129, "error: unknown switch 'x'\n"), (1, "fatal: not a git repository\n")):
            calls = []

            def failed(argv, **kwargs):
                calls.append(argv[1:])
                return SimpleNamespace(returncode=code, stdout="full stdout", stderr=message)

            with self.subTest(code=code, message=message):
                adapter = GitAdapter(ROOT, Path(sys.executable), runner=failed)
                with self.assertRaises(GitOperationError) as caught:
                    adapter._worktree_list()
                self.assertEqual(len(calls), 1)
                self.assertEqual(caught.exception.returncode, code)
                self.assertEqual(caught.exception.stdout, "full stdout")
                self.assertEqual(caught.exception.stderr, message)

    def test_legacy_worktree_listing_failure_is_not_hidden(self):
        calls = []

        def failed(argv, **kwargs):
            calls.append(argv[1:])
            if len(calls) == 1:
                return SimpleNamespace(returncode=129, stdout="", stderr="error: unknown switch 'z'\n")
            return SimpleNamespace(returncode=128, stdout="legacy stdout", stderr="fatal: legacy listing failed\n")

        adapter = GitAdapter(ROOT, Path(sys.executable), runner=failed)
        with self.assertRaises(GitOperationError) as caught:
            adapter._worktree_list()
        self.assertEqual(len(calls), 2)
        self.assertEqual(caught.exception.returncode, 128)
        self.assertEqual(caught.exception.stdout, "legacy stdout")
        self.assertEqual(caught.exception.stderr, "fatal: legacy listing failed\n")

    def test_legacy_worktree_paths_decode_git_quoting_and_utf8(self):
        encoded = [
            "/tmp/plain space", r'"/tmp/quote\"and\\slash"',
            r'"/tmp/line\nbreak\tand\rreturn"',
            r'"/tmp/\346\227\245\346\234\254"',
            '"/tmp/\u65e5\u672c"',
        ]
        decoded = ["/tmp/plain space", '/tmp/quote"and\\slash',
                   "/tmp/line\nbreak\tand\rreturn", "/tmp/\u65e5\u672c", "/tmp/\u65e5\u672c"]
        value = "".join(f"worktree {path}\nHEAD {'a' * 40}\nbranch refs/heads/task\n\n" for path in encoded)
        self.assertEqual(GitAdapter.parse_worktree_porcelain(value),
                         {GitAdapter._normalized_path(Path(path)) for path in decoded})
        self.assertEqual(GitAdapter.parse_worktree_porcelain(value.replace("\n", "\r\n")),
                         GitAdapter.parse_worktree_porcelain(value))

    def test_nul_worktree_paths_are_not_unquoted(self):
        path = '/tmp/literal\\n\nwith"quote'
        value = f"worktree {path}\0HEAD {'a' * 40}\0detached\0\0"
        self.assertEqual(GitAdapter.parse_worktree_porcelain(value),
                         {GitAdapter._normalized_path(Path(path))})

    def test_malformed_legacy_worktree_quoting_is_rejected(self):
        for path in ('"unterminated', r'"/tmp/\q"', r'"/tmp/\400"', r'"/tmp/\000"', '""', '"/tmp/a"junk'):
            with self.subTest(path=path), self.assertRaises(GitOperationError):
                GitAdapter.parse_worktree_porcelain(f"worktree {path}\nHEAD {'a' * 40}\n\n")

    def test_full_streams_no_argv_allowlist_and_timeout_diagnostics(self):
        stdout = "out\n" * 40000
        stderr = "err\n" * 40000
        runner = lambda *args, **kwargs: SimpleNamespace(returncode=19, stdout=stdout, stderr=stderr)
        adapter = GitAdapter(ROOT, Path(sys.executable), runner=runner)
        publisher = GitPublisher(Path(sys.executable), runner=runner)
        for invoke in (
            lambda: adapter._checked(("an-ordinary-git-operation",)),
            lambda: publisher._checked(("an-ordinary-git-operation",), cwd=ROOT),
        ):
            with self.assertRaises(GitOperationError) as caught:
                invoke()
            self.assertEqual(caught.exception.stdout, stdout)
            self.assertEqual(caught.exception.stderr, stderr)
            self.assertEqual(caught.exception.returncode, 19)
            self.assertIn(stdout, str(caught.exception))
            self.assertIn(stderr, str(caught.exception))

        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(args[0], kwargs["timeout"], output=b"partial stdout", stderr=b"partial stderr")

        adapter = GitAdapter(ROOT, Path(sys.executable), runner=timeout)
        publisher = GitPublisher(Path(sys.executable), runner=timeout)
        for invoke in (
            lambda: adapter._checked(("status",)),
            lambda: publisher._checked(("status",), cwd=ROOT),
        ):
            with self.assertRaises(GitOperationError) as caught:
                invoke()
            self.assertIn("timed out", str(caught.exception))
            self.assertEqual(caught.exception.stdout, "partial stdout")
            self.assertEqual(caught.exception.stderr, "partial stderr")

    def test_process_timeouts_stops_and_cleanup_errors_keep_output(self):
        for field, phrase in (("timed_out", "timed out"), ("stopped", "lease loss"),
                              ("stdin_cleanup_failed", "cleanup failed")):
            values = dict(returncode=-1, stdout="partial stdout", stderr="partial stderr",
                          timed_out=False, stopped=False, stdin_cleanup_failed=False)
            values[field] = True
            process = SimpleNamespace(stdout=io.BytesIO(), stderr=io.BytesIO())
            publisher = GitPublisher(Path(sys.executable), popen=lambda *args, **kwargs: process)
            with patch("ai_task_publish.communicate_bounded", return_value=SimpleNamespace(**values)):
                with self.assertRaises(PublishError) as caught:
                    publisher._run(("status",), cwd=ROOT, stop_event=threading.Event())
            self.assertIn(phrase, str(caught.exception))
            self.assertEqual(caught.exception.stdout, "partial stdout")
            self.assertEqual(caught.exception.stderr, "partial stderr")
            self.assertTrue(process.stdout.closed)
            self.assertTrue(process.stderr.closed)

    def test_child_environment_is_inherited_without_filtering(self):
        inherited = {key: "inherited-value" for key in (
            "AI_TASK_RUNNER_API_TOKEN", "DATABASE_URL", "DISCORD_TOKEN", "GH_TOKEN",
            "GITHUB_TOKEN", "SOME_API_KEY", "GIT_INDEX_FILE", "GIT_SSH_COMMAND", "HTTPS_PROXY",
        )}
        with patch.dict(os.environ, inherited):
            environment = GitPublisher._base_environment()
            for key, value in os.environ.items():
                self.assertEqual(environment[key], value)
        self.assertEqual(environment["GIT_TERMINAL_PROMPT"], os.environ.get("GIT_TERMINAL_PROMPT", "0"))
        self.assertEqual(environment["GCM_INTERACTIVE"], os.environ.get("GCM_INTERACTIVE", "Never"))

    def test_nul_status_parser_keeps_paths_and_both_rename_sides(self):
        value = 'R  new\nname.txt\0old\tname.txt\0?? -new.txt\0 D deleted.txt\0?? quote".txt\0'
        self.assertEqual(GitAdapter.parse_status_z(value), ["new\nname.txt", "old\tname.txt", "-new.txt", "deleted.txt", 'quote".txt'])
        with self.assertRaises(GitOperationError):
            GitAdapter.parse_status_z("R  missing-source\0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
