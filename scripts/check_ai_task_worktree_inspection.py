"""Exercise the inspection CLI against disposable real Git worktrees."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


CLI = Path(__file__).with_name("inspect_ai_task_worktree.py")


class InspectionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Inspection test")
        self.git("config", "user.email", "inspection@example.invalid")
        self.git("config", "core.autocrlf", "false")
        self.git("config", "commit.gpgsign", "false")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], stderr=subprocess.PIPE)

    def run_cli(self, repo=None, env=None, code=0):
        result = subprocess.run([sys.executable, str(CLI), "--repo", str(repo or self.repo)],
                                capture_output=True, text=True, encoding="utf-8", env=env, timeout=40)
        self.assertEqual(result.returncode, code, result.stderr)
        self.assertEqual(result.stderr if code == 0 else result.stdout, "")
        return json.loads(result.stdout if code == 0 else result.stderr)

    def commit_base(self):
        for name in ("modify.txt", "delete.txt", "old name.txt"):
            (self.repo / name).write_text(name + "\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-m", "fixture")

    def test_unborn_clean_and_detached(self):
        self.assertEqual(self.run_cli(), {"branch": "main", "head": None, "changes": []})
        self.commit_base()
        head = self.git("rev-parse", "HEAD").decode().strip()
        self.assertEqual(self.run_cli(), {"branch": "main", "head": head, "changes": []})
        self.git("checkout", "--detach")
        self.assertEqual(self.run_cli(), {"branch": None, "head": head, "changes": []})

    def test_changes_and_read_only(self):
        self.commit_base()
        (self.repo / "modify.txt").write_text("changed\n")
        (self.repo / "delete.txt").unlink()
        self.git("mv", "old name.txt", "new 日本語.txt")
        (self.repo / "added.txt").write_text("staged\n")
        self.git("add", "added.txt")
        (self.repo / "nested").mkdir()
        (self.repo / "nested/untracked 日本語.txt").write_text("new\n")
        if os.name != "nt":
            (self.repo / 'tab\tline\n".txt').write_text("unusual\n")
        before = {str(p.relative_to(self.repo)): (p.read_bytes(), p.stat().st_mtime_ns)
                  for p in self.repo.rglob("*") if p.is_file()}
        report = self.run_cli()
        after = {str(p.relative_to(self.repo)): (p.read_bytes(), p.stat().st_mtime_ns)
                 for p in self.repo.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        changes = {entry["path"]: entry for entry in report["changes"]}
        expected = {"modify.txt": " M", "delete.txt": " D", "added.txt": "A ",
                    "new 日本語.txt": "R ", "nested/untracked 日本語.txt": "??"}
        if os.name != "nt":
            expected['tab\tline\n".txt'] = "??"
        self.assertEqual({path: item["status"] for path, item in changes.items()}, expected)
        self.assertEqual(changes["new 日本語.txt"]["original_path"], "old name.txt")

    def test_linked_worktree(self):
        self.commit_base()
        linked = self.root / "task checkout"
        self.git("worktree", "add", "-b", "ai/task/test", str(linked))
        self.assertEqual(self.run_cli(linked), {
            "branch": "ai/task/test", "head": self.git("rev-parse", "HEAD").decode().strip(), "changes": []})

    def test_errors_and_redaction(self):
        self.assertIn("failed", self.run_cli(self.root, code=1)["error"])
        secret = "inspection-private-value"
        env = dict(os.environ, INSPECTION_TEST_SECRET=secret)
        error = self.run_cli(self.root / secret, env=env, code=1)["error"]
        self.assertNotIn(secret, error)
        self.assertIn("[redacted]", error)
        (self.repo / secret).write_text("untracked\n")
        self.assertEqual(self.run_cli(env=env)["changes"][0]["path"], "[redacted]")
        bare = self.root / "bare.git"
        self.git("init", "--bare", str(bare))
        self.assertIn("bare", self.run_cli(bare, code=1)["error"])


if __name__ == "__main__":
    unittest.main()
