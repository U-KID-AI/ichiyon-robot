"""Run Discord development tasks through Codex, ordinary Git, CI and deployment."""

import argparse
import logging
import threading
import time
import traceback
from enum import Enum
from pathlib import Path
from threading import Event

from ai_task_api_client import ClaimedTask, RunnerAPIClient, RunnerAPIError
from ai_task_auto_merge import AutoMergeAdapter, MergeOutcomeUnknownError
from ai_task_codex import CodexAdapter, CodexResult, build_prompt
from ai_task_diagnostics import redact_secrets
from ai_task_git import GitAdapter
from ai_task_github import GitHubAdapter
from ai_task_process import ProcessTerminationError
from ai_task_publish import GitPublisher
from ai_task_review_merge import ReviewMergeGate
from ai_task_deploy import ProductionDeployAdapter
from ai_task_deploy_config import DeployConfig
from ai_task_minecraft_deploy import TargetDeployAdapter
from ai_task_minecraft_runtime import (
    ExactMergeSource, MinecraftDeployConfig, ProductionMinecraftDeployAdapter,
)
from ai_task_runner_config import RunnerConfig
from ai_task_runtime import validate_claim_names
from ai_task_test_registry import run_tests


logger = logging.getLogger("ai_task_runner")


class MetadataReportError(RunnerAPIError):
    """An idempotent control-plane update exhausted its transport retries."""


class CompletionReportError(MetadataReportError):
    """Deployment succeeded, but the control plane did not accept its report."""


def failure_reason(exc):
    summary = f"{type(exc).__name__}: {exc}"
    return redact_secrets(summary + "\n" + "".join(traceback.format_exception(exc)))


def test_feedback(item, changed=()):
    state = "timeout" if item.timed_out else "stopped" if getattr(item, "stopped", False) else str(item.returncode)
    return redact_secrets(f"{item.name}: exit={state}\n{item.output}")


CODEX_USAGE_LIMIT_MARKERS = (
    "usage limit", "usage_limit_reached", "insufficient_quota",
    "quota exceeded", "exceeded your current quota",
)


def codex_usage_limit_reached(result: CodexResult) -> bool:
    output = f"{result.stdout}\n{result.stderr}".lower()
    return any(marker in output for marker in CODEX_USAGE_LIMIT_MARKERS)


class RunOutcome(Enum):
    NO_TASK = "no_task"
    SUCCESS = "success"
    FAILED = "failed"
    CLAIM_FAILED = "claim_failed"


def outcome_exit_code(outcome: RunOutcome) -> int:
    return 0 if outcome in (RunOutcome.NO_TASK, RunOutcome.SUCCESS) else 1


def run_idle_maintenance(deployer, outcome: RunOutcome) -> None:
    if outcome is RunOutcome.NO_TASK:
        deployer.catch_up()


def read_rules(worktree: Path) -> dict[str, str]:
    return {relative: (worktree / relative).read_text(encoding="utf-8")
            for relative in ("AGENTS.md", "docs/AI_RULES.md", "docs/AI_CONTEXT.md")
            if (worktree / relative).is_file()}


class LeaseHeartbeat:
    def __init__(self, client, task, *, interval=30, on_lost=None,
                 sleep=time.sleep, wait=None):
        self.client, self.task, self.interval = client, task, interval
        self.on_lost, self.sleep = on_lost, sleep
        self.stop_event, self.lost = Event(), Event()
        self.wait = wait or self.stop_event.wait
        self.thread = threading.Thread(target=self._run, name="ai-task-heartbeat", daemon=True)

    def _run(self):
        failures = 0
        while not self.wait(self.interval):
            try:
                self.client.heartbeat(self.task.task_id, self.task.claim_token)
                failures = 0
            except Exception as exc:
                logger.warning("Heartbeat: %s", failure_reason(exc))
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
    def __init__(self, config, *, client=None, git=None, codex=None,
                 publisher=None, github=None, review_gate=None, auto_merger=None,
                 deployer=None, test_runner=run_tests, heartbeat_factory=LeaseHeartbeat):
        self.config, self.deployer = config, deployer
        self.client = client or RunnerAPIClient(config.api_base_url, config.api_token,
            config.runner_id, timeout=config.api_timeout_seconds,
            max_response_bytes=config.max_api_response_bytes)
        self.git = git or GitAdapter(config.repo_root, config.git_path)
        self.codex = codex or CodexAdapter(config.codex_path)
        self.publisher = publisher or GitPublisher(config.git_path,
            gcm_path=config.gcm_path, gh_path=config.gh_path)
        self.github = github or GitHubAdapter(config.gh_path)
        self.review_gate = review_gate or ReviewMergeGate(config.gh_path)
        self.auto_merger = auto_merger or AutoMergeAdapter(config.gh_path, gate=self.review_gate)
        self.test_runner, self.heartbeat_factory = test_runner, heartbeat_factory

    def run_once(self) -> RunOutcome:
        try:
            task = self.client.claim()
        except Exception as exc:
            logger.error("Task claim: %s", failure_reason(exc))
            return RunOutcome.CLAIM_FAILED
        if task is None:
            return RunOutcome.NO_TASK
        try:
            return RunOutcome.SUCCESS if self._process(task) else RunOutcome.FAILED
        except Exception as exc:
            self._report_failure(task, failure_reason(exc))
            return RunOutcome.FAILED

    def _report_failure(self, task, reason):
        reason = redact_secrets(reason, (self.config.api_token,))
        logger.error("Task %s: %s", task.task_id, reason)
        try:
            self.client.mark_failed(task.task_id, task.claim_token, reason[:4000])
        except Exception as exc:
            logger.error("Reporting task failure: %s", failure_reason(exc))

    def _progress(self, task, **fields):
        limits = {"current_step": 500, "progress_summary": 4000,
                  "test_summary": 8000, "changed_files_summary": 8000, "base_commit_sha": 40}
        self._control_call("progress", task.task_id, task.claim_token,
            **{key: redact_secrets(value, (self.config.api_token,))[:limits[key]]
               for key, value in fields.items()})

    def _control_call(self, operation, *args, **kwargs):
        for attempt in range(self.config.max_attempts):
            try:
                return getattr(self.client, operation)(*args, **kwargs)
            except Exception as exc:
                reason = failure_reason(exc)
                logger.warning("Control Plane %s: %s", operation, reason)
                if attempt + 1 == self.config.max_attempts:
                    raise MetadataReportError(f"Control Plane {operation} failed: {reason}") from exc
                time.sleep(self.config.poll_seconds)

    def _complete(self, task, deployment, heartbeat):
        # A lost completion response must never trigger another implementation,
        # PR or deployment. Repeat only the idempotent completion operation.
        for attempt in range(self.config.max_attempts):
            self._require_lease(heartbeat)
            try:
                self.client.mark_completed(task.task_id, task.claim_token,
                    deployed_commit_sha=deployment.deployed_commit_sha,
                    deployment_summary=redact_secrets(deployment.summary)[:4000])
                return
            except Exception as exc:
                reason = failure_reason(exc)
                logger.warning("Task completion report: %s", reason)
                if attempt + 1 == self.config.max_attempts:
                    raise CompletionReportError(
                        f"Deployment of {deployment.deployed_commit_sha} succeeded; "
                        f"completion reporting failed: {reason}") from exc
                heartbeat.lost.wait(self.config.poll_seconds)

    @staticmethod
    def _require_lease(heartbeat):
        if heartbeat.lost.is_set():
            raise RunnerAPIError("Control Plane heartbeat failed; task lease was lost")

    def _process(self, task: ClaimedTask) -> bool:
        validate_claim_names(task.task_id, task.branch_name, task.worktree_name)
        heartbeat = self.heartbeat_factory(self.client, task,
            interval=self.config.heartbeat_seconds, on_lost=self.codex.stop)
        heartbeat.start()
        try:
            self.git.require_source_repo()
            self._progress(task, current_step="fetching_base", progress_summary="Fetching origin/main")
            base_sha = self.git.fetch_main()
            self._progress(task, base_commit_sha=base_sha, current_step="creating_worktree",
                           progress_summary="Creating task worktree")
            worktree = self.git.add_worktree(task.task_id, self.config.worktree_root, base_sha)
            self.git.validate_worktree(task.task_id, worktree, base_sha)
            artifacts = self.config.worktree_root / ("." + task.worktree_name + "-logs")
            artifacts.mkdir(parents=True, exist_ok=True)
            prompt = build_prompt(task.description, read_rules(worktree))
            feedback, testing, merged = "", False, False
            for attempt in range(1, self.config.max_attempts + 1):
                self._require_lease(heartbeat)
                self.git.validate_worktree(task.task_id, worktree, base_sha)
                step = "running_codex"
                try:
                    # A failed deployment may need another commit and PR. Merge
                    # main into the task branch without discarding task edits.
                    if merged:
                        try:
                            base_sha = self.git.integrate_main(worktree)
                            self._progress(task, base_commit_sha=base_sha)
                        except Exception as exc:
                            feedback += "\nIntegrating current main:\n" + failure_reason(exc)
                        merged = False
                    self._progress(task, current_step=step,
                        progress_summary=f"Running Codex attempt {attempt}/{self.config.max_attempts}")
                    attempt_prompt = prompt
                    if feedback:
                        attempt_prompt += ("\n<retry_feedback>\n" + feedback
                            + "\nRepair the reported operation; preserve existing edits. "
                              "Commit/push/PR/CI/merge/deploy are managed by the runner.\n</retry_feedback>")
                    result = self.codex.run(worktree, artifacts / f"attempt-{attempt}-codex.txt",
                        attempt_prompt, timeout=self.config.codex_timeout_seconds,
                        codex_home=self.config.codex_home, stop_event=heartbeat.lost)
                    self._require_lease(heartbeat)
                    if getattr(result, "stdin_cleanup_failed", False):
                        raise ProcessTerminationError("Codex process input cleanup did not complete")
                    if result.returncode or result.timed_out or getattr(result, "stopped", False):
                        details = redact_secrets(f"exit={result.returncode}, timeout={result.timed_out}\n"
                                                  f"{result.stdout}\n{result.stderr}")
                        raise RuntimeError("Codex execution failed:\n" + details)
                    self.git.validate_worktree(task.task_id, worktree, base_sha)
                    changed = self.git.changed_files(worktree, base_sha)
                    if not changed:
                        raise RuntimeError("No changes to publish; complete the requested implementation")
                    if not testing:
                        self._control_call("mark_testing", task.task_id, task.claim_token)
                        testing = True
                    step = "testing"
                    self._progress(task, current_step=step, progress_summary="Running project tests")
                    results = self.test_runner(worktree, changed, stop_event=heartbeat.lost)
                    self._require_lease(heartbeat)
                    summary = "\n\n".join(test_feedback(item, changed) for item in results)
                    (artifacts / f"attempt-{attempt}-tests.txt").write_text(summary, encoding="utf-8")
                    files_summary = "\n".join(changed) + "\n" + self.git.diff_stat(worktree, base_sha)
                    self._progress(task, test_summary=summary, changed_files_summary=files_summary)
                    failures = [item for item in results if item.returncode or item.timed_out or getattr(item, "stopped", False)]
                    if failures:
                        raise RuntimeError("Tests failed:\n" + "\n\n".join(test_feedback(item) for item in failures))
                    step = "committing"
                    self._progress(task, current_step=step, progress_summary="Staging and committing task changes")
                    commit = self.publisher.commit(worktree, task.task_id, base_sha, changed,
                                                   stop_event=heartbeat.lost)
                    (artifacts / f"attempt-{attempt}-diff.patch").write_text(
                        redact_secrets(self.git.diff(worktree, base_sha)), encoding="utf-8")
                    self._require_lease(heartbeat)
                    step = "pushing_branch"
                    self._progress(task, current_step=step, progress_summary="Pushing task branch")
                    self.publisher.push_task_branch(worktree, task.task_id, commit.commit_sha,
                                                    stop_event=heartbeat.lost)
                    step = "creating_draft_pr"
                    self._progress(task, current_step=step, progress_summary="Creating or updating task PR")
                    pr = self.github.ensure_draft_pr(worktree, task.task_id, commit.commit_sha,
                                                     stop_event=heartbeat.lost)
                    step = "waiting_for_ci"
                    self._progress(task, current_step=step, progress_summary=f"Waiting for CI: {pr.url}")
                    ci = self.review_gate.wait_for_draft_candidate(worktree, task.task_id,
                        commit.commit_sha, pr.number, pr.url, changed,
                        timeout=self.config.codex_timeout_seconds, interval=self.config.poll_seconds,
                        stop_event=heartbeat.lost)
                    self._require_lease(heartbeat)
                    step = "merging"
                    self._progress(task, current_step=step, progress_summary=f"Merging {pr.url}")
                    merge = self.auto_merger.ready_and_squash_merge(worktree, task.task_id,
                        commit.commit_sha, pr.number, pr.url, changed, stop_event=heartbeat.lost)
                    merged = True
                    step = "deploying"
                    self._control_call("mark_deploying", task.task_id, task.claim_token,
                        commit_sha=commit.commit_sha, pr_number=pr.number, pr_url=pr.url,
                        test_summary=redact_secrets(summary)[:8000],
                        changed_files_summary=redact_secrets(files_summary)[:8000],
                        ci_workflow_run_id=merge.workflow_run_id or getattr(ci, "workflow_run_id", None) or None,
                        review_summary="Automated code review disabled; GitHub CI passed.",
                        merge_commit_sha=merge.merge_sha)
                    self._require_lease(heartbeat)
                    if self.deployer is None:
                        raise RuntimeError("Production deployment adapter is not configured")
                    # Retry the same release once before asking Codex for a fix.
                    for deployment_attempt in range(2):
                        try:
                            deployment = self.deployer.deploy(merge.merge_sha, stop_event=heartbeat.lost)
                            if deployment.deployed_commit_sha != merge.merge_sha:
                                raise RuntimeError(f"Deployed SHA {deployment.deployed_commit_sha} differs from requested {merge.merge_sha}")
                            break
                        except Exception as exc:
                            self._require_lease(heartbeat)
                            if deployment_attempt:
                                raise
                            self._progress(task, current_step=step,
                                progress_summary="Retrying deployment:\n" + failure_reason(exc))
                    self._require_lease(heartbeat)
                    self._complete(task, deployment, heartbeat)
                    return True
                except (ProcessTerminationError, MetadataReportError, MergeOutcomeUnknownError):
                    raise
                except Exception as exc:
                    self._require_lease(heartbeat)
                    feedback = redact_secrets(f"{step} failed on attempt {attempt}:\n{failure_reason(exc)}",
                                              (self.config.api_token,))
                    (artifacts / f"attempt-{attempt}-error.txt").write_text(feedback, encoding="utf-8")
                    logger.warning("Task %s %s", task.task_id, feedback)
                    self._progress(task, current_step="retrying", progress_summary=feedback)
                    if attempt < self.config.max_attempts:
                        self._control_call("retry", task.task_id, task.claim_token, feedback[:4000])
                        testing = True
            self._report_failure(task, f"Attempts exhausted ({self.config.max_attempts}):\n{feedback}")
            return False
        finally:
            try:
                self.codex.stop()
            finally:
                heartbeat.stop()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="process one task and exit")
    parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        config = RunnerConfig.from_environment()
        deploy_config = DeployConfig.from_environment(repo_root=config.repo_root, worktree_root=config.worktree_root)
        app_deployer = ProductionDeployAdapter(deploy_config)
        minecraft_source = ExactMergeSource(config.repo_root, config.git_path)

        def minecraft_factory():
            minecraft_config = MinecraftDeployConfig.from_environment(
                repo_root=config.repo_root, worktree_root=config.worktree_root)
            return ProductionMinecraftDeployAdapter(minecraft_config, minecraft_source)

        deployer = TargetDeployAdapter(app_deployer, minecraft_source, minecraft_factory)
        runner = LocalRunner(config, deployer=deployer)
        outcome = runner.run_once()
        run_idle_maintenance(deployer, outcome)
        return outcome_exit_code(outcome)
    except Exception as exc:
        logger.error("Runner startup: %s", failure_reason(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
