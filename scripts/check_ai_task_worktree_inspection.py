"""Offline CLI checks using temporary real Git repositories and linked worktrees."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from inspect_ai_task_worktree import InspectionError, git, parse_changes


CLI = Path(__file__).with_name("inspect_ai_task_worktree.py")


class InspectionChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo space 日本語"
        self.repo.mkdir()
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        self.env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_AUTHOR_NAME="Fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
                        GIT_COMMITTER_NAME="Fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid")
        self.run_git("init", "-b", "fixture")

    def run_git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], env=self.env,
                              capture_output=True, check=True).stdout.decode("utf-8").strip()

    def cli(self, repo=None, expected=0, extra_env=None, args=None):
        result = subprocess.run(
            [sys.executable, str(CLI), *(args if args is not None else ["--repo", str(repo or self.repo)])],
            env={**self.env, **(extra_env or {})}, capture_output=True, text=True, encoding="utf-8",
            timeout=30,
        )
        self.assertEqual(result.returncode, expected, result.stderr)
        if expected == 0:
            self.assertEqual(result.stderr, "")
            return json.loads(result.stdout)
        self.assertEqual(result.stdout, "")
        return result.stderr

    def commit_fixture(self):
        for name in ("modified.txt", "deleted.txt", "old name 日本語.txt", "staged-delete.txt"):
            (self.repo / name).write_text(name + "\n", encoding="utf-8")
        self.run_git("add", ".")
        self.run_git("commit", "-m", "fixture")
        return self.run_git("rev-parse", "HEAD")

    def test_unborn_clean_and_detached(self):
        self.assertEqual(self.cli(), {"branch": "fixture", "head": None, "changes": []})
        head = self.commit_fixture()
        self.assertEqual(self.cli(), {"branch": "fixture", "head": head, "changes": []})
        self.run_git("checkout", "--detach", "HEAD")
        self.assertEqual(self.cli(), {"branch": None, "head": head, "changes": []})

    def test_changes_and_read_only(self):
        head = self.commit_fixture()
        (self.repo / "modified.txt").write_text("staged\n", encoding="utf-8")
        self.run_git("add", "modified.txt")
        (self.repo / "modified.txt").write_text("unstaged\n", encoding="utf-8")
        (self.repo / "deleted.txt").unlink()
        self.run_git("rm", "staged-delete.txt")
        self.run_git("mv", "old name 日本語.txt", "new name 日本語.txt")
        (self.repo / "added.txt").write_text("new", encoding="utf-8")
        self.run_git("add", "added.txt")
        (self.repo / "nested").mkdir()
        unusual = "nested/untracked 日本語.txt" if os.name == "nt" else 'nested/tab\tline\nquote".txt'
        (self.repo / unusual).write_text("untracked", encoding="utf-8")
        def snapshot():
            return {str(p.relative_to(self.repo)): (p.read_bytes(), p.stat().st_mtime_ns)
                    for p in self.repo.rglob("*") if p.is_file()}
        before = snapshot()
        report = self.cli(self.repo / "nested")
        self.assertEqual(snapshot(), before)
        self.assertEqual(report["branch"], "fixture")
        self.assertEqual(report["head"], head)
        changes = {item["path"]: item for item in report["changes"]}
        self.assertEqual({path: item["status"] for path, item in changes.items()}, {
            "modified.txt": "MM", "deleted.txt": " D", "staged-delete.txt": "D ",
            "new name 日本語.txt": "R ", "added.txt": "A ", unusual: "??",
        })
        self.assertEqual(changes["new name 日本語.txt"]["original_path"], "old name 日本語.txt")

    def test_linked_worktree(self):
        head = self.commit_fixture()
        target = self.root / "linked checkout"
        self.run_git("worktree", "add", "-b", "ai/task/fixture", str(target))
        self.assertTrue((target / ".git").is_file())
        self.assertEqual(self.cli(target), {"branch": "ai/task/fixture", "head": head, "changes": []})

    def test_errors_and_redaction(self):
        secret = 'synthetic-inspection-credential'
        missing = self.root / secret
        error = self.cli(missing, expected=1, extra_env={"INSPECTION_API_TOKEN": secret})
        self.assertNotIn(secret, error)
        self.assertIn("[redacted]", json.loads(error)["error"])
        self.assertIn("not a git repository", json.loads(self.cli(self.root, expected=1))["error"].lower())
        bare = self.root / "bare.git"
        self.run_git("init", "--bare", str(bare))
        self.assertIn("bare repository", json.loads(self.cli(bare, expected=1))["error"])
        self.assertIn("--repo", self.cli(expected=2, args=[]))
        (self.repo / (secret + '.txt')).touch()
        report = self.cli(extra_env={"INSPECTION_API_TOKEN": secret})
        self.assertEqual(report["changes"], [{"status": "??", "path": "[redacted].txt"}])

    def test_process_errors_and_malformed_status(self):
        for error, message in ((FileNotFoundError("git missing"), "Could not run git"),
                               (subprocess.TimeoutExpired("git", 30), "timed out")):
            with self.subTest(error=error), patch("inspect_ai_task_worktree.subprocess.run", side_effect=error):
                with self.assertRaisesRegex(InspectionError, message):
                    git(self.repo, "status")
        for raw in (b"?? missing-terminator", b"x\0", b"R  target\0"):
            with self.subTest(raw=raw), self.assertRaises(InspectionError):
                parse_changes(raw)


if __name__ == "__main__":
    unittest.main()
