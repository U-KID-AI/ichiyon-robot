"""Offline checks for the Phase 2D read-only review gate."""

import json
import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from ai_task_review_merge import (
    ReviewCIFailedError,
    ReviewGateResult,
    ReviewMergeGate,
    ReviewMergeSafetyError,
    ReviewPendingError,
)
from ai_task_safety import is_protected_path


TASK_ID = UUID(
    "00000000-0000-0000-0000-000000000001"
)
COMMIT_SHA = "a" * 40
BASE_SHA = "c" * 40
BRANCH = f"ai/task/{TASK_ID}"
PR_NUMBER = 123
PR_URL = (
    "https://github.com/"
    "U-KID-AI/ichiyon-robot/pull/123"
)
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
        ReviewMergeSafetyError,
    ):
        return True
    return False


def pr_item():
    return {
        "number": PR_NUMBER,
        "html_url": PR_URL,
        "state": "open",
        "draft": True,
        "merged_at": None,
        "mergeable": True,
        "base": {
            "ref": "main",
            "sha": BASE_SHA,
            "repo": {
                "full_name":
                "U-KID-AI/ichiyon-robot",
            },
        },
        "head": {
            "ref": BRANCH,
            "sha": COMMIT_SHA,
            "repo": {
                "full_name":
                "U-KID-AI/ichiyon-robot",
            },
        },
    }


def run_item(
    *,
    run_id=900,
    status="completed",
    conclusion="success",
    sha=COMMIT_SHA,
    base_sha=BASE_SHA,
    pr_number=PR_NUMBER,
):
    return {
        "id": run_id,
        "name": "checks",
        "path": ".github/workflows/checks.yml",
        "event": "pull_request",
        "head_sha": sha,
        "head_branch": BRANCH,
        "status": status,
        "conclusion": conclusion,
        "pull_requests": [
            {
                "number": pr_number,
                "base": {
                    "ref": "main",
                    "sha": base_sha,
                },
                "head": {
                    "ref": BRANCH,
                    "sha": sha,
                },
            },
        ],
    }


def fake_backend(
    *,
    pr=None,
    files=None,
    page2=None,
    runs=None,
    main_sha=BASE_SHA,
):
    calls = []

    pr = pr if pr is not None else pr_item()
    files = (
        files
        if files is not None
        else [{"filename": value} for value in FILES]
    )
    page2 = page2 if page2 is not None else []
    runs = (
        runs
        if runs is not None
        else [run_item()]
    )

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        args = argv[1:]

        if (
            args[:3]
            == ["api", "--method", "GET"]
            and args[3]
            == (
                "repos/U-KID-AI/"
                "ichiyon-robot/git/ref/heads/main"
            )
        ):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "ref": "refs/heads/main",
                        "object": {
                            "type": "commit",
                            "sha": main_sha,
                        },
                    }
                ),
                stderr="",
            )

        if (
            args[:3]
            == ["api", "--method", "GET"]
            and args[3]
            == (
                "repos/U-KID-AI/"
                "ichiyon-robot/pulls/123"
            )
        ):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(pr),
                stderr="",
            )

        if (
            args[:3]
            == ["api", "--method", "GET"]
            and args[3]
            == (
                "repos/U-KID-AI/"
                "ichiyon-robot/pulls/123/files"
            )
        ):
            page = args[-1]

            value = (
                files
                if page == "page=1"
                else page2
            )

            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(value),
                stderr="",
            )

        if (
            args[:3]
            == ["api", "--method", "GET"]
            and args[3]
            == (
                "repos/U-KID-AI/"
                "ichiyon-robot/actions/runs"
            )
        ):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "workflow_runs": runs,
                    }
                ),
                stderr="",
            )

        raise AssertionError(args)

    return runner, calls


def inspect_with(
    *,
    pr=None,
    files=None,
    page2=None,
    runs=None,
    main_sha=BASE_SHA,
    expected_files=FILES,
):
    runner, calls = fake_backend(
        pr=pr,
        files=files,
        page2=page2,
        runs=runs,
        main_sha=main_sha,
    )

    adapter = ReviewMergeGate(
        Path(sys.executable).resolve(),
        runner=runner,
    )

    result = adapter.inspect_draft_candidate(
        ROOT,
        TASK_ID,
        COMMIT_SHA,
        PR_NUMBER,
        PR_URL,
        expected_files,
    )

    return result, calls


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

    os.environ["AI_TASK_RUNNER_API_TOKEN"] = (
        "runner-secret"
    )
    os.environ["DATABASE_URL"] = "db-secret"
    os.environ["GH_TOKEN"] = "gh-secret"
    os.environ["GITHUB_TOKEN"] = "github-secret"

    try:
        result, calls = inspect_with()
    finally:
        for key, value in old_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    check(
        "exact Draft PR and successful CI pass gate",
        result.pr_number == PR_NUMBER
        and result.pr_url == PR_URL
        and result.head_sha == COMMIT_SHA
        and result.base_sha == BASE_SHA
        and result.workflow_run_id == 900
        and result.changed_files == tuple(sorted(FILES)),
    )

    ready_pr = pr_item()
    ready_pr["draft"] = False

    ready_runner, _ready_calls = fake_backend(
        pr=ready_pr,
    )

    ready_result = ReviewMergeGate(
        Path(sys.executable).resolve(),
        runner=ready_runner,
    ).inspect_candidate(
        ROOT,
        TASK_ID,
        COMMIT_SHA,
        PR_NUMBER,
        PR_URL,
        FILES,
        expected_draft=False,
    )

    check(
        "same gate can verify ready PR state",
        ready_result.head_sha == COMMIT_SHA
        and ready_result.base_sha == BASE_SHA,
    )

    check(
        "wrong expected draft state is rejected",
        rejects(
            lambda: ReviewMergeGate(
                Path(sys.executable).resolve(),
                runner=ready_runner,
            ).inspect_candidate(
                ROOT,
                TASK_ID,
                COMMIT_SHA,
                PR_NUMBER,
                PR_URL,
                FILES,
                expected_draft=True,
            )
        ),
    )

    check(
        "review gate re-reads PR metadata",
        sum(
            1
            for argv, _kwargs in calls
            if argv[1:4]
            == ["api", "--method", "GET"]
            and argv[4]
            == (
                "repos/U-KID-AI/"
                "ichiyon-robot/pulls/123"
            )
        )
        == 2,
    )

    check(
        "GitHub API uses shell false",
        all(
            kwargs["shell"] is False
            for _argv, kwargs in calls
        ),
    )

    first_env = calls[0][1]["env"]

    check(
        "review gate child excludes secrets",
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

    check(
        "review gate disables prompting",
        first_env.get("GH_PROMPT_DISABLED") == "1"
        and first_env.get("GIT_TERMINAL_PROMPT")
        == "0",
    )

    check(
        "Phase 2D control paths are protected",
        all(
            is_protected_path(path)
            for path in (
                "scripts/ai_task_review_merge.py",
                "scripts/ai_task_auto_merge.py",
                "scripts/check_ai_task_review_merge.py",
                "scripts/check_ai_task_auto_merge.py",
            )
        ),
    )

    wrong_base = pr_item()
    wrong_base["base"] = {"ref": "other"}

    check(
        "wrong PR base is rejected",
        rejects(
            lambda: inspect_with(
                pr=wrong_base,
            )
        ),
    )

    wrong_head = pr_item()
    wrong_head["head"] = dict(
        wrong_head["head"]
    )
    wrong_head["head"]["sha"] = "b" * 40

    check(
        "wrong PR head SHA is rejected",
        rejects(
            lambda: inspect_with(
                pr=wrong_head,
            )
        ),
    )

    not_mergeable = pr_item()
    not_mergeable["mergeable"] = False

    check(
        "non-mergeable PR is rejected",
        rejects(
            lambda: inspect_with(
                pr=not_mergeable,
            )
        ),
    )

    check(
        "PR base must still equal current main",
        rejects(
            lambda: inspect_with(
                main_sha="d" * 40,
            )
        ),
    )

    check(
        "CI for stale base SHA is rejected",
        rejects(
            lambda: inspect_with(
                runs=[
                    run_item(
                        base_sha="d" * 40,
                    )
                ],
            )
        ),
    )

    check(
        "wrong PR URL is rejected",
        rejects(
            lambda: ReviewMergeGate(
                Path(sys.executable).resolve(),
                runner=lambda *_args, **_kwargs: (
                    None
                ),
            ).inspect_draft_candidate(
                ROOT,
                TASK_ID,
                COMMIT_SHA,
                PR_NUMBER,
                (
                    "https://github.com/"
                    "U-KID-AI/ichiyon-robot/"
                    "pull/124"
                ),
                FILES,
            )
        ),
    )

    check(
        "changed-file mismatch is rejected",
        rejects(
            lambda: inspect_with(
                files=[
                    {
                        "filename":
                        "bot/unexpected.py",
                    },
                ],
            )
        ),
    )

    check(
        "more than 100 PR files is rejected",
        rejects(
            lambda: inspect_with(
                page2=[
                    {
                        "filename":
                        "bot/too_many.py",
                    },
                ],
            )
        ),
    )

    check(
        "pending CI is rejected",
        rejects(
            lambda: inspect_with(
                runs=[
                    run_item(
                        status="in_progress",
                        conclusion=None,
                    )
                ],
            )
        ),
    )

    check(
        "failed CI is rejected",
        rejects(
            lambda: inspect_with(
                runs=[
                    run_item(
                        status="completed",
                        conclusion="failure",
                    )
                ],
            )
        ),
    )

    try:
        inspect_with(runs=[])
        missing_ci_pending = False
    except ReviewPendingError:
        missing_ci_pending = True

    check(
        "missing exact CI is transient pending",
        missing_ci_pending,
    )

    try:
        inspect_with(
            runs=[
                run_item(
                    status="completed",
                    conclusion="failure",
                )
            ],
        )
        failed_ci_typed = False
    except ReviewCIFailedError:
        failed_ci_typed = True

    check(
        "completed failed CI is not retried as pending",
        failed_ci_typed,
    )

    wait_adapter = ReviewMergeGate(
        Path(sys.executable).resolve(),
        runner=lambda *_args, **_kwargs: None,
    )

    wait_calls = []

    def sequenced_inspection(
        *args,
        **kwargs,
    ):
        wait_calls.append(True)

        if len(wait_calls) == 1:
            raise ReviewPendingError(
                "pending"
            )

        return ReviewGateResult(
            pr_number=PR_NUMBER,
            pr_url=PR_URL,
            head_sha=COMMIT_SHA,
            base_sha=BASE_SHA,
            workflow_run_id=900,
            changed_files=tuple(
                sorted(FILES)
            ),
        )

    wait_adapter.inspect_draft_candidate = (
        sequenced_inspection
    )

    waited = wait_adapter.wait_for_draft_candidate(
        ROOT,
        TASK_ID,
        COMMIT_SHA,
        PR_NUMBER,
        PR_URL,
        FILES,
        timeout=1,
        interval=0.01,
    )

    check(
        "bounded CI wait retries only transient pending state",
        len(wait_calls) == 2
        and waited.workflow_run_id == 900,
    )

    check(
        "CI for wrong SHA is rejected",
        rejects(
            lambda: inspect_with(
                runs=[
                    run_item(
                        sha="b" * 40,
                    )
                ],
            )
        ),
    )

    check(
        "CI for wrong PR is rejected",
        rejects(
            lambda: inspect_with(
                runs=[
                    run_item(
                        pr_number=999,
                    )
                ],
            )
        ),
    )

    check(
        "newer failed exact CI beats older success",
        rejects(
            lambda: inspect_with(
                runs=[
                    run_item(
                        run_id=800,
                    ),
                    run_item(
                        run_id=900,
                        conclusion="failure",
                    ),
                ],
            )
        ),
    )

    adapter = ReviewMergeGate(
        Path(sys.executable).resolve(),
        runner=lambda *_args, **_kwargs: None,
    )

    check(
        "merge write operation is not allowlisted",
        rejects(
            lambda: adapter._run(
                (
                    "api",
                    "--method",
                    "PUT",
                    (
                        "repos/U-KID-AI/"
                        "ichiyon-robot/pulls/"
                        "123/merge"
                    ),
                ),
                cwd=ROOT,
            )
        ),
    )

    check(
        "ready mutation is not allowlisted",
        rejects(
            lambda: adapter._run(
                (
                    "pr",
                    "ready",
                    "123",
                ),
                cwd=ROOT,
            )
        ),
    )

    stopped = threading.Event()
    stopped.set()

    stopped_adapter = ReviewMergeGate(
        Path(sys.executable).resolve(),
        runner=lambda *_args, **_kwargs: (
            (_ for _ in ()).throw(
                AssertionError(
                    "runner must not start"
                )
            )
        ),
    )

    check(
        "review gate refuses after lease loss",
        rejects(
            lambda: stopped_adapter.inspect_draft_candidate(
                ROOT,
                TASK_ID,
                COMMIT_SHA,
                PR_NUMBER,
                PR_URL,
                FILES,
                stop_event=stopped,
            )
        ),
    )

    print(
        "AI task Phase 2D review gate checks passed"
    )


if __name__ == "__main__":
    main()