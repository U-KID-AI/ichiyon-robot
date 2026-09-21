"""Phase 3C-1D Windows/Linux local AI task runner.

The default mode processes at most one task. This module never connects directly
to the database. After fixed offline validation it may create a deterministic
commit object, push only the UUID task branch, create or adopt an exact Draft PR,
wait for exact CI, run a read-only code review, and perform a guarded squash merge.
Production deployment is available only through the reviewed fixed-operation deployer.
The durable deploying state precedes the fixed-operation deployer.
"""

import argparse
import logging
import re
import sys
import threading
import time
from enum import Enum
from pathlib import Path
from threading import Event

from ai_task_api_client import ClaimedTask, RunnerAPIClient, RunnerAPIError, SHA_PATTERN
from ai_task_auto_merge import AutoMergeAdapter, AutoMergeSafetyError
from ai_task_code_review import CodeReviewAdapter, CodeReviewSafetyError
from ai_task_codex import CodexAdapter, CodexResult, CodexSafetyError, build_prompt
from ai_task_git import GitAdapter, GitSafetyError, GitDiffCheckError, repairable_paths
from ai_task_github import GitHubAdapter, GitHubSafetyError
from ai_task_process import ProcessTerminationError
from ai_task_publish import GitPublisher, PublishSafetyError
from ai_task_review_merge import ReviewMergeGate, ReviewMergeSafetyError
from ai_task_deploy import ProductionDeployAdapter
from ai_task_deploy_config import DeployConfig, DeploymentSafetyError
from ai_task_minecraft_deploy import TargetDeployAdapter
from ai_task_minecraft_runtime import (
    ExactMergeSource,
    MinecraftDeployConfig,
    ProductionMinecraftDeployAdapter,
)
from ai_task_runner_config import RunnerConfig
from ai_task_safety import (SafetyError, is_reparse_point, task_worktree_path,
                             validate_changed_paths, validate_claim_names, validate_project_codex_layer)
from ai_task_test_registry import TestRegistryError, run_tests


logger = logging.getLogger("ai_task_runner")


def failure_reason(exc):
    # Never serialize arbitrary exception text (transport errors may contain credentials).
    safe_messages = {
        "task Git topology changed", "origin repository mismatch", "source repository is not clean",
        "worktree root mismatch", "worktree base mismatch", "worktree branch mismatch",
        "worktree name mismatch", "invalid worktree input", "Git snapshot validation failed",
        "unsafe or credential changed path", "changed path is a symlink",
        "changed path is a reparse point", "changed path escapes repository",
        "automatic unstage failed", "protected path restoration failed",
        "protected base path is not a regular file", "origin fetch failed",
        "Phase 2C publishing is not configured", "invalid task ID",
        "claim names do not match task ID", "Codex input cleanup did not complete",
    }
    if type(exc) in (GitSafetyError, SafetyError, ProcessTerminationError) and str(exc) in safe_messages:
        return str(exc)
    reasons = {
        GitSafetyError: "Git integrity or recovery operation failed; ownership, origin, base or worktree could not be verified",
        DeploymentSafetyError: "Production deployment verification failed; completion withheld",
        SafetyError: "Repository filesystem or policy boundary validation failed",
        CodexSafetyError: "Codex adapter safety or process handling failed",
        ProcessTerminationError: "Process cleanup could not be verified; human inspection required",
        RunnerAPIError: "Control Plane operation failed; task transition could not be confirmed",
        TestRegistryError: "Fixed offline test registry could not inspect an implementation file",
        PublishSafetyError: "Deterministic task publication validation failed",
        GitHubSafetyError: "Draft PR identity or GitHub operation validation failed",
        OSError: "Runner filesystem or process operation failed",
    }
    return next((message for kind, message in reasons.items() if isinstance(exc, kind)),
                "Runner validation failed in a fixed operation; inspect runner diagnostics")


def test_feedback(item, changed=()):
    # The fixed registry is python-syntax. Project-derived syntax messages can
    # contain source literals, so export only known diagnostic phrases and lines.
    name = "python-syntax" if item.name == "python-syntax" else "offline-test"
    result = "timeout" if item.timed_out else "stopped" if getattr(item, "stopped", False) else str(int(item.returncode))
    if result == "0":
        return f"{name}=0"
    diagnostics = ("invalid syntax", "expected ':'", "was never closed", "unexpected indent",
                   "unindent does not match", "file is too large", "cannot read UTF-8 source",
                   "too many Python files")
    output = str(item.output)[:4000]
    detail = next((phrase for phrase in diagnostics if phrase in output), "diagnostic output omitted")
    line = re.search(r":([0-9]{1,7}):", output)
    filename = next((path for path in changed if output.startswith(path + ":")), "")
    return (f"{name}={result}: {detail}" + (f" at line {line[1]}" if line else "")
            + (f" ({filename[:300]})" if filename else ""))


CODEX_USAGE_LIMIT_MARKERS = (
    "usage limit",
    "usage_limit_reached",
    "insufficient_quota",
    "quota exceeded",
    "exceeded your current quota",
)


def codex_usage_limit_reached(result: CodexResult) -> bool:
    stdout = getattr(result, "stdout", "")
    stderr = getattr(result, "stderr", "")
    output = f"{stdout}\n{stderr}".lower()
    return any(marker in output for marker in CODEX_USAGE_LIMIT_MARKERS)


class RunOutcome(Enum):
    NO_TASK = "no_task"
    SUCCESS = "success"
    FAILED = "failed"
    CLAIM_FAILED = "claim_failed"


def outcome_exit_code(outcome: RunOutcome) -> int:
    return 0 if outcome in (RunOutcome.NO_TASK, RunOutcome.SUCCESS) else 1


def run_idle_maintenance(deployer, outcome: RunOutcome) -> None:
    """Run BDS catch-up only when this invocation claimed no task."""
    if outcome is RunOutcome.NO_TASK:
        deployer.catch_up()


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
    def __init__(
        self,
        config: RunnerConfig,
        *,
        client=None,
        git=None,
        codex=None,
        publisher=None,
        github=None,
        reviewer=None,
        review_gate=None,
        auto_merger=None,
        deployer=None,
        test_runner=run_tests,
        heartbeat_factory=LeaseHeartbeat,
    ):
        self.deployer = deployer
        self.config = config
        self.client = client or RunnerAPIClient(config.api_base_url, config.api_token, config.runner_id,
                                               timeout=config.api_timeout_seconds,
                                               max_response_bytes=config.max_api_response_bytes)
        self.git = git or GitAdapter(
            config.repo_root,
            config.git_path,
        )
        self.codex = codex or CodexAdapter(
            config.codex_path,
        )

        self.publisher = publisher
        if (
            self.publisher is None
            and (config.gcm_path is not None
                 or (sys.platform == "linux" and config.gh_path is not None))
        ):
            self.publisher = GitPublisher(
                config.git_path,
                gcm_path=config.gcm_path,
                gh_path=config.gh_path,
            )

        self.github = github
        if (
            self.github is None
            and config.gh_path is not None
        ):
            self.github = GitHubAdapter(
                config.gh_path,
            )

        self.reviewer = reviewer
        self.review_gate = review_gate
        self.auto_merger = auto_merger

        self.test_runner = test_runner
        self.heartbeat_factory = heartbeat_factory

    def run_once(self) -> RunOutcome:
        self._deployment_started = False
        try:
            task = self.client.claim()
        except RunnerAPIError:
            logger.error("AI task claim failed")
            return RunOutcome.CLAIM_FAILED
        if task is None:
            return RunOutcome.NO_TASK
        try:
            return RunOutcome.SUCCESS if self._process(task) else RunOutcome.FAILED
        except (
            RunnerAPIError,
            GitSafetyError,
            GitHubSafetyError,
            PublishSafetyError,
            CodexSafetyError,
            CodeReviewSafetyError,
            ReviewMergeSafetyError,
            AutoMergeSafetyError,
            ProcessTerminationError,
            SafetyError,
            TestRegistryError,
            OSError,
            ValueError,
            TimeoutError,
        ) as exc:
            logger.error("AI task failed safely: %s", type(exc).__name__)
            try:
                cleanup_uncertain = (isinstance(exc, ProcessTerminationError)
                                     or isinstance(exc, CodexSafetyError) and str(exc) == "Codex process handling and cleanup failed")
                report = self.client.mark_needs_human if self._deployment_started or cleanup_uncertain else self.client.mark_failed
                report(task.task_id, task.claim_token, failure_reason(exc))
            except Exception:
                pass
            return RunOutcome.FAILED
        except Exception as exc:
            logger.error(
                "AI task runner internal failure: %s",
                type(exc).__name__,
            )
            try:
                report = self.client.mark_needs_human if self._deployment_started else self.client.mark_failed
                report(
                    task.task_id,
                    task.claim_token,
                    "Runner internal operation failed; inspect runner implementation",
                )
            except Exception:
                pass
            return RunOutcome.FAILED

    def _repair_changes(self, task, worktree, base_sha, before):
        self.git.validate_worktree(task.task_id, worktree, base_sha)
        if self.git.snapshot(worktree) != before:
            raise GitSafetyError("task Git topology changed")
        changed = self.git.changed_files(worktree)
        protected = repairable_paths(worktree, changed)
        if self.git.staged_files(worktree):
            self.git.unstage(worktree, task.task_id, base_sha)
        if protected:
            self.git.restore_protected(worktree, task.task_id, base_sha, protected)
        changed = self.git.changed_files(worktree)
        validate_changed_paths(worktree, changed)
        validate_project_codex_layer(worktree)
        if self.git.snapshot(worktree) != before or self.git.staged_files(worktree):
            raise GitSafetyError("Git integrity changed during automatic recovery")
        return protected, changed

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
            testing = False
            feedback = ""
            for attempt in range(1, self.config.max_attempts + 1):
                if heartbeat.lost.is_set():
                    self.client.mark_needs_human(task.task_id, task.claim_token, "Control Plane lease could not be maintained")
                    return False
                self.git.validate_worktree(task.task_id, worktree_path, base_sha)
                if self.git.snapshot(worktree_path) != before:
                    raise GitSafetyError("task Git topology changed")
                self.client.progress(task.task_id, task.claim_token, current_step="running_codex",
                                     progress_summary=f"Running Codex attempt {attempt}/{self.config.max_attempts}")
                attempt_prompt = prompt
                if feedback:
                    attempt_prompt += "\n<retry_feedback>\n" + feedback[:2000] + "\nRepair the implementation and preserve allowed edits.\n</retry_feedback>"
                output = self.config.worktree_root / f".{task.worktree_name}.codex-output-attempt-{attempt}.txt"
                if (output.exists() or output.is_symlink() or output.parent.is_symlink()
                        or is_reparse_point(output.parent)):
                    raise CodexSafetyError("Codex output path is unsafe")
                try:
                    result: CodexResult = self.codex.run(
                        worktree_path, output, attempt_prompt, timeout=self.config.codex_timeout_seconds,
                        codex_home=self.config.codex_home, stop_event=heartbeat.lost)
                except TimeoutError:
                    self.codex.stop()
                    result = CodexResult(-1, "", "", timed_out=True)
                if heartbeat.lost.is_set():
                    self.client.mark_needs_human(task.task_id, task.claim_token, "Control Plane lease could not be maintained")
                    return False
                if getattr(result, "stdin_cleanup_failed", False):
                    self.codex.stop()
                    raise ProcessTerminationError("Codex input cleanup did not complete")
                if result.returncode != 0 and codex_usage_limit_reached(result):
                    self.codex.stop()
                    protected, changed = self._repair_changes(
                        task, worktree_path, base_sha, before
                    )
                    changed_summary = (
                        ("\n".join(changed) + "\n" + self.git.diff_stat(worktree_path))[:8000]
                        if changed
                        else "No repository changes before handoff"
                    )
                    self.client.progress(
                        task.task_id,
                        task.claim_token,
                        current_step="needs_human",
                        progress_summary="Codex usage limit reached; manual handoff required",
                        changed_files_summary=changed_summary,
                    )
                    self.client.mark_needs_human(
                        task.task_id,
                        task.claim_token,
                        "Codex usage limit reached; manual continuation required",
                    )
                    return False
                feedback = ""
                if result.timed_out or result.returncode != 0 or getattr(result, "stopped", False):
                    self.codex.stop()
                    feedback = ("Codex timed out" if result.timed_out else
                                "Codex process stopped unexpectedly" if getattr(result, "stopped", False) else
                                "Codex nonzero exit")
                protected, changed = self._repair_changes(task, worktree_path, base_sha, before)
                if protected:
                    feedback = (feedback + "; " if feedback else "") + "Protected paths were reverted: " + ", ".join(protected)[:1400]
                if feedback:
                    continue
                if not changed:
                    feedback = "No repository changes; implementation changes are required"
                    continue
                if not testing:
                    self.client.mark_testing(task.task_id, task.claim_token)
                    testing = True
                self.client.progress(task.task_id, task.claim_token, current_step="testing", progress_summary="Running fixed offline test registry")
                results = self.test_runner(worktree_path, changed, stop_event=heartbeat.lost)
                if heartbeat.lost.is_set():
                    self.client.mark_needs_human(task.task_id, task.claim_token, "Control Plane lease could not be maintained")
                    return False
                protected, changed_after_tests = self._repair_changes(task, worktree_path, base_sha, before)
                if protected:
                    feedback = "Protected paths were reverted after tests: " + ", ".join(protected)[:1400]
                    continue
                if not changed_after_tests:
                    feedback = "No repository changes after tests; implementation changes are required"
                    continue
                summary = "; ".join(test_feedback(item, changed_after_tests) for item in results)[:2000]
                files_summary = ("\n".join(changed_after_tests) + "\n" + self.git.diff_stat(worktree_path))[:8000]
                failures = [item for item in results if item.returncode != 0 or item.timed_out or getattr(item, "stopped", False)]
                if failures:
                    feedback = "Fixed test registry failed: " + "; ".join(test_feedback(item, changed_after_tests) for item in failures)[:1800]
                    self.client.progress(task.task_id, task.claim_token, test_summary=summary, changed_files_summary=files_summary)
                    continue
                try:
                    self.git.diff_check(worktree_path)
                except GitDiffCheckError:
                    feedback = "git diff --check failed; repair whitespace errors"
                    continue
                break
            else:
                self.client.mark_failed(task.task_id, task.claim_token,
                                        f"Attempts exhausted ({self.config.max_attempts}): {feedback}"[:2400])
                return False

            if heartbeat.lost.is_set():
                self.client.mark_needs_human(
                    task.task_id,
                    task.claim_token,
                    "Control Plane lease could not be maintained",
                )
                return False

            if (
                self.publisher is None
                or self.github is None
            ):
                raise SafetyError(
                    "Phase 2C publishing is not configured"
                )

            self.client.progress(
                task.task_id,
                task.claim_token,
                current_step="creating_commit",
                progress_summary=(
                    "Creating fixed Phase 2C commit object"
                ),
                test_summary=summary,
                changed_files_summary=files_summary,
            )

            commit_result = (
                self.publisher.safe_commit_object(
                    worktree_path,
                    task.task_id,
                    base_sha,
                    changed_after_tests,
                )
            )

            if heartbeat.lost.is_set():
                self.client.mark_needs_human(
                    task.task_id,
                    task.claim_token,
                    "Control Plane lease could not be maintained",
                )
                return False

            after_commit = self.git.snapshot(
                worktree_path
            )
            changed_before_push = (
                self.git.changed_files(
                    worktree_path
                )
            )

            validate_changed_paths(
                worktree_path,
                changed_before_push,
            )

            if self.git.staged_files(
                worktree_path
            ):
                self.client.mark_failed(
                    task.task_id,
                    task.claim_token,
                    (
                        "Git index changed before "
                        "publishing"
                    ),
                )
                return False

            if (
                after_commit != before
                or changed_before_push
                != changed_after_tests
            ):
                self.client.mark_failed(
                    task.task_id,
                    task.claim_token,
                    (
                        "Repository changed after "
                        "commit preparation"
                    ),
                )
                return False

            # Read the candidate tree again immediately
            # before the first remote side effect.
            # Deterministic commit construction means
            # the exact SHA must be reproduced.
            verified_commit = (
                self.publisher.safe_commit_object(
                    worktree_path,
                    task.task_id,
                    base_sha,
                    changed_before_push,
                )
            )

            if (
                verified_commit.commit_sha
                != commit_result.commit_sha
                or verified_commit.tree_sha
                != commit_result.tree_sha
            ):
                self.client.mark_failed(
                    task.task_id,
                    task.claim_token,
                    (
                        "Candidate commit changed "
                        "before publishing"
                    ),
                )
                return False

            if heartbeat.lost.is_set():
                self.client.mark_needs_human(
                    task.task_id,
                    task.claim_token,
                    "Control Plane lease could not be maintained",
                )
                return False

            self.client.progress(
                task.task_id,
                task.claim_token,
                current_step="pushing_branch",
                progress_summary=(
                    "Pushing fixed UUID task branch"
                ),
            )

            self.publisher.push_task_branch(
                worktree_path,
                task.task_id,
                commit_result.commit_sha,
                stop_event=heartbeat.lost,
            )

            if heartbeat.lost.is_set():
                self.client.mark_needs_human(
                    task.task_id,
                    task.claim_token,
                    (
                        "Control Plane lease was lost "
                        "after branch publication"
                    ),
                )
                return False

            # A push must not modify the local task
            # worktree, index, branch, or changed-file
            # set.
            after_push = self.git.snapshot(
                worktree_path
            )
            changed_after_push = (
                self.git.changed_files(
                    worktree_path
                )
            )

            validate_changed_paths(
                worktree_path,
                changed_after_push,
            )

            if (
                after_push != before
                or changed_after_push
                != changed_after_tests
                or self.git.staged_files(
                    worktree_path
                )
            ):
                self.client.mark_needs_human(
                    task.task_id,
                    task.claim_token,
                    (
                        "Repository changed during "
                        "branch publication"
                    ),
                )
                return False

            self.client.progress(
                task.task_id,
                task.claim_token,
                current_step="creating_draft_pr",
                progress_summary=(
                    "Creating or verifying Draft PR"
                ),
            )

            pull_request = (
                self.github.ensure_draft_pr(
                    worktree_path,
                    task.task_id,
                    commit_result.commit_sha,
                    stop_event=heartbeat.lost,
                )
            )

            if heartbeat.lost.is_set():
                self.client.mark_needs_human(
                    task.task_id,
                    task.claim_token,
                    (
                        "Control Plane lease was lost "
                        "after Draft PR creation"
                    ),
                )
                return False

            # Final local state check before the
            # terminal Control Plane transition.
            final_snapshot = self.git.snapshot(
                worktree_path
            )
            final_changed = (
                self.git.changed_files(
                    worktree_path
                )
            )

            validate_changed_paths(
                worktree_path,
                final_changed,
            )

            if (
                final_snapshot != before
                or final_changed
                != changed_after_tests
                or self.git.staged_files(
                    worktree_path
                )
            ):
                self.client.mark_needs_human(
                    task.task_id,
                    task.claim_token,
                    (
                        "Repository changed before "
                        "ready-for-review"
                    ),
                )
                return False

            if self.reviewer is None:
                self.reviewer = CodeReviewAdapter(
                    self.config.codex_path,
                    temp_root=self.config.worktree_root,
                )

            if self.review_gate is None:
                if self.config.gh_path is None:
                    raise SafetyError(
                        "Phase 2D GitHub review gate is not configured"
                    )

                self.review_gate = ReviewMergeGate(
                    self.config.gh_path,
                )

            if self.auto_merger is None:
                if self.config.gh_path is None:
                    raise SafetyError(
                        "Phase 2D auto merge is not configured"
                    )

                self.auto_merger = AutoMergeAdapter(
                    self.config.gh_path,
                    gate=self.review_gate,
                )

            self.client.progress(
                task.task_id,
                task.claim_token,
                current_step="waiting_for_ci",
                progress_summary=(
                    "Waiting for exact GitHub CI on the Draft PR"
                ),
            )

            gate_result = (
                self.review_gate.wait_for_draft_candidate(
                    worktree_path,
                    task.task_id,
                    commit_result.commit_sha,
                    pull_request.number,
                    pull_request.url,
                    changed_after_tests,
                    timeout=self.config.codex_timeout_seconds,
                    interval=self.config.poll_seconds,
                    stop_event=heartbeat.lost,
                )
            )

            if heartbeat.lost.is_set():
                self.client.mark_needs_human(
                    task.task_id,
                    task.claim_token,
                    "Control Plane lease was lost while waiting for CI",
                )
                return False

            expected_review_files = tuple(
                sorted(changed_after_tests)
            )

            if (
                gate_result.head_sha
                != commit_result.commit_sha
                or gate_result.base_sha != base_sha
                or gate_result.pr_number
                != pull_request.number
                or gate_result.pr_url
                != pull_request.url
                or gate_result.changed_files
                != expected_review_files
            ):
                raise SafetyError(
                    "CI gate result does not match the tested candidate"
                )

            self.client.progress(
                task.task_id,
                task.claim_token,
                current_step="code_review",
                progress_summary=(
                    "Running read-only automated code review"
                ),
            )

            review_result = self.reviewer.run(
                worktree_path,
                task_id=task.task_id,
                task_description=task.description,
                base_sha=gate_result.base_sha,
                head_sha=gate_result.head_sha,
                changed_files=changed_after_tests,
                timeout=self.config.codex_timeout_seconds,
                codex_home=self.config.codex_home,
                stop_event=heartbeat.lost,
            )

            if heartbeat.lost.is_set():
                self.client.mark_needs_human(
                    task.task_id,
                    task.claim_token,
                    "Control Plane lease was lost during code review",
                )
                return False

            after_review = self.git.snapshot(
                worktree_path
            )
            changed_after_review = (
                self.git.changed_files(
                    worktree_path
                )
            )

            validate_changed_paths(
                worktree_path,
                changed_after_review,
            )

            if (
                after_review != before
                or changed_after_review
                != changed_after_tests
                or self.git.staged_files(
                    worktree_path
                )
            ):
                self.client.mark_needs_human(
                    task.task_id,
                    task.claim_token,
                    (
                        "Repository changed during "
                        "read-only code review"
                    ),
                )
                return False

            if not review_result.approved:
                reason = (
                    "Automated code review rejected "
                    "the candidate. "
                    + review_result.summary
                )[:4000]

                self.client.mark_needs_human(
                    task.task_id,
                    task.claim_token,
                    reason,
                )
                return False

            if (
                review_result.base_sha
                != gate_result.base_sha
                or review_result.head_sha
                != gate_result.head_sha
                or review_result.findings
            ):
                raise SafetyError(
                    "code review approval binding is invalid"
                )

            self.client.progress(
                task.task_id,
                task.claim_token,
                current_step="merging",
                progress_summary=(
                    "Revalidating PR and performing guarded squash merge"
                ),
            )

            merge_result = (
                self.auto_merger.ready_and_squash_merge(
                    worktree_path,
                    task.task_id,
                    commit_result.commit_sha,
                    pull_request.number,
                    pull_request.url,
                    changed_after_tests,
                    stop_event=heartbeat.lost,
                )
            )

            if heartbeat.lost.is_set():
                raise SafetyError(
                    "lease was lost during merge finalization"
                )

            if (
                not isinstance(merge_result.merge_sha, str)
                or SHA_PATTERN.fullmatch(merge_result.merge_sha) is None
                or merge_result.head_sha
                != review_result.head_sha
                or merge_result.base_sha
                != review_result.base_sha
                or merge_result.pr_number
                != pull_request.number
                or merge_result.pr_url
                != pull_request.url
            ):
                raise SafetyError(
                    "merged candidate differs from reviewed candidate"
                )

            self._deployment_started = True
            self.client.mark_deploying(
                task.task_id,
                task.claim_token,
                commit_sha=commit_result.commit_sha,
                pr_number=pull_request.number,
                pr_url=pull_request.url,
                test_summary=summary,
                changed_files_summary=files_summary,
                ci_workflow_run_id=(
                    merge_result.workflow_run_id
                ),
                review_summary=(
                    review_result.summary
                ),
                merge_commit_sha=(
                    merge_result.merge_sha
                ),
            )

            if heartbeat.lost.is_set():
                raise SafetyError("lease was lost before deployment")
            if self.deployer is None:
                raise SafetyError("Phase 3C-1D production deployment is not implemented")
            merge_sha = merge_result.merge_sha
            deployment = self.deployer.deploy(merge_sha, stop_event=heartbeat.lost)
            if heartbeat.lost.is_set():
                raise SafetyError("lease was lost during deployment")
            deployed_sha = getattr(deployment, "deployed_commit_sha", None)
            deployment_summary = getattr(deployment, "summary", None)
            if (not isinstance(deployed_sha, str) or deployed_sha != merge_sha
                    or not isinstance(deployment_summary, str)
                    or not 1 <= len(deployment_summary) <= 4000):
                raise SafetyError("deployment proof does not match the reviewed merge")
            if heartbeat.lost.is_set():
                raise SafetyError("lease was lost before completion")
            self.client.mark_completed(
                task.task_id, task.claim_token,
                deployed_commit_sha=deployed_sha,
                deployment_summary=deployment_summary,
            )

            return True
        finally:
            try:
                self.codex.stop()
            finally:
                heartbeat.stop()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="process one task and exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        config = RunnerConfig.from_environment()
        deploy_config = DeployConfig.from_environment(
            repo_root=config.repo_root,
            worktree_root=config.worktree_root,
        )

        app_deployer = ProductionDeployAdapter(deploy_config)
        minecraft_source = ExactMergeSource(
            config.repo_root,
            config.git_path,
        )

        def minecraft_factory():
            minecraft_config = MinecraftDeployConfig.from_environment(
                repo_root=config.repo_root,
                worktree_root=config.worktree_root,
            )
            return ProductionMinecraftDeployAdapter(
                minecraft_config,
                minecraft_source,
            )

        deployer = TargetDeployAdapter(
            app_deployer,
            minecraft_source,
            minecraft_factory,
        )

        runner = LocalRunner(
            config,
            deployer=deployer,
        )

        # --once remains the only supported execution mode.
        # Minecraft catch-up runs only on an idle invocation. A run that claims
        # any ordinary task must never contact BDS unless that task's reviewed
        # deployment itself requires the Minecraft target.
        outcome = runner.run_once()

        run_idle_maintenance(deployer, outcome)

        return outcome_exit_code(outcome)
    except (ValueError, OSError, SafetyError) as exc:
        logger.error("Runner configuration failed: %s", type(exc).__name__)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
