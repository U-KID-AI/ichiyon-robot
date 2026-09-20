"""Phase 2B Windows local AI task runner.

The default mode processes at most one task. This module does not connect to a
database and does not create commits, pushes, or pull requests.
"""

import argparse
import logging
import threading
import time
from enum import Enum
from pathlib import Path
from threading import Event

from ai_task_api_client import ClaimedTask, RunnerAPIClient, RunnerAPIError
from ai_task_codex import CodexAdapter, CodexResult, CodexSafetyError, build_prompt
from ai_task_git import GitAdapter, GitSafetyError
from ai_task_process import ProcessTerminationError
from ai_task_runner_config import RunnerConfig
from ai_task_safety import (SafetyError, is_reparse_point, task_worktree_path,
                             validate_changed_paths, validate_claim_names, validate_project_codex_layer)
from ai_task_test_registry import TestRegistryError, run_tests


logger = logging.getLogger("ai_task_runner")


class RunOutcome(Enum):
    NO_TASK = "no_task"
    SUCCESS = "success"
    FAILED = "failed"
    CLAIM_FAILED = "claim_failed"


def outcome_exit_code(outcome: RunOutcome) -> int:
    return 0 if outcome in (RunOutcome.NO_TASK, RunOutcome.SUCCESS) else 1


def read_rules(worktree: Path) -> dict[str, str]:
    rules = {}
    for relative in ("AGENTS.md", "docs/AI_RULES.md", "docs/AI_CONTEXT.md"):
        candidate = worktree / relative
        if candidate.is_symlink() or is_reparse_point(candidate) or not candidate.is_file() or candidate.stat().st_size > 256 * 1024:
            raise SafetyError("repository rule file is unsafe")
        path = candidate.resolve()
        if worktree.resolve() not in path.parents or is_reparse_point(path):
            raise SafetyError("repository rule file escapes worktree")
        parent = candidate.parent
        while parent != worktree and worktree in parent.parents:
            if parent.is_symlink() or is_reparse_point(parent):
                raise SafetyError("repository rule parent is a reparse point")
            parent = parent.parent
        rules[relative] = path.read_text(encoding="utf-8")
    return rules


class LeaseHeartbeat:
    def __init__(self, client: RunnerAPIClient, task: ClaimedTask, *, interval: float = 30,
                 on_lost=None, sleep=time.sleep, wait=None) -> None:
        self.client = client
        self.task = task
        self.interval = interval
        self.on_lost = on_lost
        self.sleep = sleep
        self.stop_event = Event()
        self.wait = wait or self.stop_event.wait
        self.lost = Event()
        self.thread = threading.Thread(target=self._run, name="ai-task-heartbeat", daemon=True)

    def _run(self):
        failures = 0
        while not self.wait(self.interval):
            try:
                self.client.heartbeat(self.task.task_id, self.task.claim_token)
                failures = 0
            except Exception:
                failures += 1
                if failures >= 3:
                    self.lost.set()
                    if self.on_lost:
                        self.on_lost()
                    return

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=2)


class LocalRunner:
    def __init__(self, config: RunnerConfig, *, client=None, git=None, codex=None, test_runner=run_tests,
                 heartbeat_factory=LeaseHeartbeat):
        self.config = config
        self.client = client or RunnerAPIClient(config.api_base_url, config.api_token, config.runner_id,
                                               timeout=config.api_timeout_seconds,
                                               max_response_bytes=config.max_api_response_bytes)
        self.git = git or GitAdapter(config.repo_root, config.git_path)
        self.codex = codex or CodexAdapter(config.codex_path)
        self.test_runner = test_runner
        self.heartbeat_factory = heartbeat_factory

    def run_once(self) -> RunOutcome:
        try:
            task = self.client.claim()
        except RunnerAPIError:
            logger.error("AI task claim failed")
            return RunOutcome.CLAIM_FAILED
        if task is None:
            return RunOutcome.NO_TASK
        try:
            return RunOutcome.SUCCESS if self._process(task) else RunOutcome.FAILED
        except (RunnerAPIError, GitSafetyError, CodexSafetyError, ProcessTerminationError,
                SafetyError, TestRegistryError, OSError, ValueError, TimeoutError) as exc:
            logger.error("AI task failed safely: %s", type(exc).__name__)
            try:
                self.client.mark_needs_human(task.task_id, task.claim_token, "Runner safety validation failed")
            except Exception:
                pass
            return RunOutcome.FAILED

    def _process(self, task: ClaimedTask) -> bool:
        validate_claim_names(task.task_id, task.branch_name, task.worktree_name)
        self.git.require_source_repo()
        self.client.progress(task.task_id, task.claim_token, current_step="claimed", progress_summary="Task claimed by local runner")
        self.client.progress(task.task_id, task.claim_token, current_step="fetching_base", progress_summary="Fetching origin/main")
        base_sha = self.git.fetch_main()
        self.client.progress(task.task_id, task.claim_token, base_commit_sha=base_sha)
        worktree_path = task_worktree_path(self.config.worktree_root, task.task_id)
        self.client.progress(task.task_id, task.claim_token, current_step="creating_worktree", progress_summary="Creating isolated task worktree")
        worktree_path = self.git.add_worktree(task.task_id, self.config.worktree_root, base_sha)
        self.git.validate_worktree(task.task_id, worktree_path, base_sha)
        before = self.git.snapshot(worktree_path)
        validate_project_codex_layer(worktree_path)
        rules = read_rules(worktree_path)
        prompt = build_prompt(task.description, rules)
        heartbeat = self.heartbeat_factory(self.client, task, interval=self.config.heartbeat_seconds, on_lost=self.codex.stop)
        heartbeat.start()
        try:
            self.client.progress(task.task_id, task.claim_token, current_step="running_codex", progress_summary="Running non-interactive Codex")
            result: CodexResult = self.codex.run(worktree_path, self.config.worktree_root / ("." + task.worktree_name + ".codex-output.txt"), prompt,
                                                 timeout=self.config.codex_timeout_seconds,
                                                 codex_home=self.config.codex_home, stop_event=heartbeat.lost)
            if heartbeat.lost.is_set():
                self.client.mark_needs_human(task.task_id, task.claim_token, "Control Plane lease could not be maintained")
                return False
            if result.timed_out:
                self.client.mark_needs_human(task.task_id, task.claim_token, "Codex execution timed out")
                return False
            if getattr(result, "stopped", False):
                self.client.mark_needs_human(task.task_id, task.claim_token, "Codex process stopped unexpectedly")
                return False
            if getattr(result, "stdin_cleanup_failed", False):
                self.client.mark_needs_human(task.task_id, task.claim_token, "Codex input cleanup did not complete")
                return False
            if result.returncode != 0:
                self.client.mark_failed(task.task_id, task.claim_token, "Codex execution failed")
                return False
            after = self.git.snapshot(worktree_path)
            if after.head != before.head or after.branch != before.branch or after.worktrees != before.worktrees or after.origin != before.origin:
                self.client.mark_needs_human(task.task_id, task.claim_token, "Codex changed protected Git state")
                return False
            changed = self.git.changed_files(worktree_path)
            validate_changed_paths(worktree_path, changed)
            if self.git.staged_files(worktree_path):
                self.client.mark_needs_human(task.task_id, task.claim_token, "Codex changed the Git index")
                return False
            if not changed:
                self.client.mark_needs_human(task.task_id, task.claim_token, "No repository changes were produced")
                return False
            self.client.mark_testing(task.task_id, task.claim_token)
            self.client.progress(task.task_id, task.claim_token, current_step="testing", progress_summary="Running fixed offline test registry")
            results = self.test_runner(worktree_path, changed, stop_event=heartbeat.lost)
            if heartbeat.lost.is_set():
                self.client.mark_needs_human(task.task_id, task.claim_token, "Control Plane lease could not be maintained")
                return False
            after_tests = self.git.snapshot(worktree_path)
            changed_after_tests = self.git.changed_files(worktree_path)
            validate_changed_paths(worktree_path, changed_after_tests)
            if self.git.staged_files(worktree_path):
                self.client.mark_needs_human(task.task_id, task.claim_token, "Tests changed the Git index")
                return False
            if after_tests != before:
                self.client.mark_needs_human(task.task_id, task.claim_token, "Tests changed protected Git state")
                return False
            summary = "; ".join(f"{item.name}={item.returncode}" for item in results)[:8000]
            files_summary = ("\n".join(changed_after_tests) + "\n" + self.git.diff_stat(worktree_path))[:8000]
            if any(item.returncode != 0 or item.timed_out for item in results):
                self.client.progress(task.task_id, task.claim_token, test_summary=summary, changed_files_summary=files_summary)
                self.client.mark_failed(task.task_id, task.claim_token, "Fixed test registry failed")
                return False
            self.git.diff_check(worktree_path)
            self.client.progress(task.task_id, task.claim_token, current_step="phase2b_complete", progress_summary="Phase 2B complete; task remains testing", test_summary=summary, changed_files_summary=files_summary)
            return True
        finally:
            heartbeat.stop()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="process one task and exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        config = RunnerConfig.from_environment()
        runner = LocalRunner(config)
        # --once is the only supported execution mode in Phase 2B.
        outcome = runner.run_once()
        return outcome_exit_code(outcome)
    except (ValueError, OSError) as exc:
        logger.error("Runner configuration failed: %s", type(exc).__name__)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
