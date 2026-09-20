"""Phase 3C-1D Windows local AI task runner.

The default mode processes at most one task. This module never connects directly
to the database. After fixed offline validation it may create a deterministic
commit object, push only the UUID task branch, create or adopt an exact Draft PR,
wait for exact CI, run a read-only code review, and perform a guarded squash merge.
Real production deployment is not implemented in Phase 3C-1D.
The durable deploying state precedes any injected fixed-operation deployer.
"""

import argparse
import logging
import threading
import time
from enum import Enum
from pathlib import Path
from threading import Event

from ai_task_api_client import ClaimedTask, RunnerAPIClient, RunnerAPIError, SHA_PATTERN
from ai_task_auto_merge import AutoMergeAdapter, AutoMergeSafetyError
from ai_task_code_review import CodeReviewAdapter, CodeReviewSafetyError
from ai_task_codex import CodexAdapter, CodexResult, CodexSafetyError, build_prompt
from ai_task_git import GitAdapter, GitSafetyError
from ai_task_github import GitHubAdapter, GitHubSafetyError
from ai_task_process import ProcessTerminationError
from ai_task_publish import GitPublisher, PublishSafetyError
from ai_task_review_merge import ReviewMergeGate, ReviewMergeSafetyError
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
            and config.gcm_path is not None
        ):
            self.publisher = GitPublisher(
                config.git_path,
                gcm_path=config.gcm_path,
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
                self.client.mark_needs_human(task.task_id, task.claim_token, "Runner safety validation failed")
            except Exception:
                pass
            return RunOutcome.FAILED
        except Exception as exc:
            logger.error(
                "AI task runner internal failure: %s",
                type(exc).__name__,
            )
            try:
                self.client.mark_needs_human(
                    task.task_id,
                    task.claim_token,
                    "Runner internal failure",
                )
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
                self.client.mark_needs_human(
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
                self.client.mark_needs_human(
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
                self.client.mark_needs_human(
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
            heartbeat.stop()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="process one task and exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        config = RunnerConfig.from_environment()
        runner = LocalRunner(config)
        # --once remains the only supported execution mode in Phase 2D.
        outcome = runner.run_once()
        return outcome_exit_code(outcome)
    except (ValueError, OSError) as exc:
        logger.error("Runner configuration failed: %s", type(exc).__name__)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
