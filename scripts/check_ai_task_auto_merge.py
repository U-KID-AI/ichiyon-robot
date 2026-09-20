"""Offline checks for Phase 2D privileged GitHub mutations."""

import json
import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from ai_task_auto_merge import (
    AutoMergeAdapter,
    AutoMergeSafetyError,
)
from ai_task_review_merge import ReviewGateResult


TASK_ID = UUID(
    "00000000-0000-0000-0000-000000000001"
)
HEAD_SHA = "a" * 40
BASE_SHA = "b" * 40
MERGE_SHA = "c" * 40
PR_NUMBER = 123
PR_URL = (
    "https://github.com/"
    "U-KID-AI/ichiyon-robot/pull/123"
)
BRANCH = f"ai/task/{TASK_ID}"
FILES = (
    "bot/example.py",
    "docs/example.md",
)


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print(f"PASS {name}")


def rejects(call):
    try:
        call()
    except (
        ValueError,
        RuntimeError,
        AutoMergeSafetyError,
    ):
        return True
    return False


def gate_result():
    return ReviewGateResult(
        pr_number=PR_NUMBER,
        pr_url=PR_URL,
        head_sha=HEAD_SHA,
        base_sha=BASE_SHA,
        workflow_run_id=900,
        changed_files=tuple(sorted(FILES)),
    )


class FakeGate:
    def __init__(
        self,
        *,
        mismatch_after=False,
        stop_after_ready=None,
    ):
        self.calls = []
        self.mismatch_after = mismatch_after
        self.stop_after_ready = stop_after_ready

    def inspect_candidate(
        self,
        cwd,
        task_id,
        commit_sha,
        pr_number,
        pr_url,
        expected_files,
        *,
        expected_draft,
        stop_event=None,
    ):
        self.calls.append(expected_draft)

        result = gate_result()

        if (
            expected_draft is False
            and self.mismatch_after
        ):
            result = ReviewGateResult(
                pr_number=result.pr_number,
                pr_url=result.pr_url,
                head_sha=result.head_sha,
                base_sha=result.base_sha,
                workflow_run_id=901,
                changed_files=result.changed_files,
            )

        return result


def backend(
    *,
    ready_returncode=0,
    merge_returncode=0,
    final_head_sha=HEAD_SHA,
    final_merge_sha=MERGE_SHA,
    stop_after_ready=None,
):
    calls = []
    state = {
        "ready": False,
        "merged": False,
    }

    def pr_payload():
        if state["merged"]:
            return {
                "number": PR_NUMBER,
                "html_url": PR_URL,
                "state": "closed",
                "draft": False,
                "merged_at":
                "2026-09-20T00:00:00Z",
                "merge_commit_sha":
                final_merge_sha,
                "base": {
                    "ref": "main",
                    "sha": BASE_SHA,
                },
                "head": {
                    "ref": BRANCH,
                    "sha": final_head_sha,
                },
                "node_id": "PR_test123",
            }

        return {
            "number": PR_NUMBER,
            "html_url": PR_URL,
            "state": "open",
            "draft": not state["ready"],
            "merged_at": None,
            "merge_commit_sha": None,
            "base": {
                "ref": "main",
                "sha": BASE_SHA,
            },
            "head": {
                "ref": BRANCH,
                "sha": HEAD_SHA,
            },
            "node_id": "PR_test123",
        }

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        args = argv[1:]

        if (
            args[:3]
            == ["api", "--method", "GET"]
        ):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(pr_payload()),
                stderr="",
            )

        if args[:2] == ["api", "graphql"]:
            state["ready"] = True

            if stop_after_ready is not None:
                stop_after_ready.set()

            return SimpleNamespace(
                returncode=ready_returncode,
                stdout=json.dumps(
                    {
                        "data": {
                            "markPullRequestReadyForReview": {
                                "pullRequest": {
                                    "number": PR_NUMBER,
                                    "isDraft": False,
                                }
                            }
                        }
                    }
                ),
                stderr=(
                    ""
                    if ready_returncode == 0
                    else "ambiguous"
                ),
            )

        if (
            args[:3]
            == ["api", "--method", "PUT"]
        ):
            state["merged"] = True

            return SimpleNamespace(
                returncode=merge_returncode,
                stdout=json.dumps(
                    {
                        "sha": final_merge_sha,
                        "merged": True,
                        "message":
                        "Pull Request successfully merged",
                    }
                ),
                stderr=(
                    ""
                    if merge_returncode == 0
                    else "ambiguous"
                ),
            )

        raise AssertionError(args)

    return runner, calls


def run_success(
    *,
    gate=None,
    ready_returncode=0,
    merge_returncode=0,
    final_head_sha=HEAD_SHA,
    final_merge_sha=MERGE_SHA,
    stop_after_ready=None,
):
    runner, calls = backend(
        ready_returncode=ready_returncode,
        merge_returncode=merge_returncode,
        final_head_sha=final_head_sha,
        final_merge_sha=final_merge_sha,
        stop_after_ready=stop_after_ready,
    )

    actual_gate = gate or FakeGate()

    adapter = AutoMergeAdapter(
        Path(sys.executable).resolve(),
        gate=actual_gate,
        runner=runner,
    )

    result = adapter.ready_and_squash_merge(
        ROOT,
        TASK_ID,
        HEAD_SHA,
        PR_NUMBER,
        PR_URL,
        FILES,
        stop_event=stop_after_ready,
    )

    return result, calls, actual_gate


def main():
    old_values = {
        key: os.environ.get(key)
        for key in (
            "AI_TASK_RUNNER_API_TOKEN",
            "DATABASE_URL",
            "GH_TOKEN",
            "GITHUB_TOKEN",
        )
    }

    os.environ["AI_TASK_RUNNER_API_TOKEN"] = "secret"
    os.environ["DATABASE_URL"] = "secret"
    os.environ["GH_TOKEN"] = "secret"
    os.environ["GITHUB_TOKEN"] = "secret"

    try:
        result, calls, gate = run_success()
    finally:
        for key, value in old_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    check(
        "ready then exact squash merge succeeds",
        result.pr_number == PR_NUMBER
        and result.pr_url == PR_URL
        and result.head_sha == HEAD_SHA
        and result.base_sha == BASE_SHA
        and result.workflow_run_id == 900
        and result.merge_sha == MERGE_SHA,
    )

    check(
        "gate runs before ready and again after ready",
        gate.calls == [True, False],
    )

    ready_calls = [
        call
        for call in calls
        if call[0][1:3]
        == ["api", "graphql"]
    ]

    merge_calls = [
        call
        for call in calls
        if call[0][1:4]
        == ["api", "--method", "PUT"]
    ]

    check(
        "exactly one ready mutation occurs",
        len(ready_calls) == 1,
    )

    check(
        "exactly one merge mutation occurs",
        len(merge_calls) == 1,
    )

    merge_argv = merge_calls[0][0]

    check(
        "merge is fixed squash with expected head SHA",
        "merge_method=squash" in merge_argv
        and f"sha={HEAD_SHA}" in merge_argv
        and (
            "repos/U-KID-AI/ichiyon-robot/"
            "pulls/123/merge"
        )
        in merge_argv,
    )

    check(
        "GitHub mutations use shell false",
        all(
            kwargs["shell"] is False
            for _argv, kwargs in calls
        ),
    )

    first_env = calls[0][1]["env"]

    check(
        "mutation child excludes secrets",
        all(
            key not in first_env
            for key in (
                "AI_TASK_RUNNER_API_TOKEN",
                "DATABASE_URL",
                "GH_TOKEN",
                "GITHUB_TOKEN",
            )
        ),
    )

    mismatch_gate = FakeGate(
        mismatch_after=True,
    )

    mismatch_runner, mismatch_calls = backend()

    mismatch_adapter = AutoMergeAdapter(
        Path(sys.executable).resolve(),
        gate=mismatch_gate,
        runner=mismatch_runner,
    )

    check(
        "gate mismatch after ready blocks merge",
        rejects(
            lambda: mismatch_adapter.ready_and_squash_merge(
                ROOT,
                TASK_ID,
                HEAD_SHA,
                PR_NUMBER,
                PR_URL,
                FILES,
            )
        )
        and not any(
            call[0][1:4]
            == ["api", "--method", "PUT"]
            for call in mismatch_calls
        ),
    )

    check(
        "ambiguous ready response is adopted after exact recheck",
        run_success(
            ready_returncode=1,
        )[0].merge_sha
        == MERGE_SHA,
    )

    check(
        "ambiguous merge response is adopted after exact final state",
        run_success(
            merge_returncode=1,
        )[0].merge_sha
        == MERGE_SHA,
    )

    check(
        "wrong final head SHA is rejected",
        rejects(
            lambda: run_success(
                final_head_sha="d" * 40,
            )
        ),
    )

    check(
        "invalid final merge SHA is rejected",
        rejects(
            lambda: run_success(
                final_merge_sha="bad",
            )
        ),
    )

    stopped = threading.Event()
    stopped.set()

    no_start_runner, no_start_calls = backend()

    no_start_adapter = AutoMergeAdapter(
        Path(sys.executable).resolve(),
        gate=FakeGate(),
        runner=no_start_runner,
    )

    check(
        "lease loss before ready blocks all mutations",
        rejects(
            lambda: no_start_adapter.ready_and_squash_merge(
                ROOT,
                TASK_ID,
                HEAD_SHA,
                PR_NUMBER,
                PR_URL,
                FILES,
                stop_event=stopped,
            )
        )
        and not no_start_calls,
    )

    lost_after_ready = threading.Event()

    lost_runner, lost_calls = backend(
        stop_after_ready=lost_after_ready,
    )

    lost_adapter = AutoMergeAdapter(
        Path(sys.executable).resolve(),
        gate=FakeGate(),
        runner=lost_runner,
    )

    check(
        "lease loss after ready blocks merge",
        rejects(
            lambda: lost_adapter.ready_and_squash_merge(
                ROOT,
                TASK_ID,
                HEAD_SHA,
                PR_NUMBER,
                PR_URL,
                FILES,
                stop_event=lost_after_ready,
            )
        )
        and not any(
            call[0][1:4]
            == ["api", "--method", "PUT"]
            for call in lost_calls
        ),
    )

    adapter = AutoMergeAdapter(
        Path(sys.executable).resolve(),
        gate=FakeGate(),
        runner=lambda *_args, **_kwargs: None,
    )

    check(
        "arbitrary GitHub write is rejected",
        rejects(
            lambda: adapter._run(
                (
                    "api",
                    "--method",
                    "DELETE",
                    "repos/U-KID-AI/ichiyon-robot",
                ),
                cwd=ROOT,
            )
        ),
    )

    print(
        "AI task Phase 2D auto-merge checks passed"
    )


if __name__ == "__main__":
    main()