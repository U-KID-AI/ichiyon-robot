"""Offline checks for Phase 2C GitHub Draft PR handling."""

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from ai_task_github import GitHubAdapter, GitHubSafetyError


TASK_ID = UUID("00000000-0000-0000-0000-000000000001")
COMMIT_SHA = "a" * 40
BRANCH = f"ai/task/{TASK_ID}"


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print(f"PASS {name}")


def rejects(call):
    try:
        call()
    except (ValueError, RuntimeError, GitHubSafetyError):
        return True
    return False


def main():
    calls = []
    created = {"value": False}

    item = {
        "number": 123,
        "url": "https://github.com/U-KID-AI/ichiyon-robot/pull/123",
        "isDraft": True,
        "baseRefName": "main",
        "headRefName": BRANCH,
        "headRefOid": COMMIT_SHA,
        "state": "OPEN",
    }

    def fake_runner(argv, **kwargs):
        calls.append((argv, kwargs))
        args = argv[1:]

        if args[:2] == ["pr", "list"]:
            value = [item] if created["value"] else []
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(value),
                stderr="",
            )

        if args[:2] == ["pr", "create"]:
            created["value"] = True
            return SimpleNamespace(
                returncode=0,
                stdout=item["url"] + "\n",
                stderr="",
            )

        raise AssertionError(args)

    fake_path = Path(sys.executable).resolve()

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
        adapter = GitHubAdapter(fake_path, runner=fake_runner)
        pr = adapter.ensure_draft_pr(ROOT, TASK_ID, COMMIT_SHA)
    finally:
        for key, value in old_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    check(
        "Draft PR metadata verified",
        pr.number == 123
        and pr.url == item["url"]
        and pr.head_sha == COMMIT_SHA,
    )

    create_calls = [
        call
        for call in calls
        if call[0][1:3] == ["pr", "create"]
    ]

    check(
        "exactly one Draft PR creation attempted",
        len(create_calls) == 1,
    )

    argv, kwargs = create_calls[0]

    check(
        "Draft PR repository and base are fixed",
        "U-KID-AI/ichiyon-robot" in argv
        and "--base" in argv
        and "main" in argv,
    )

    check(
        "Draft flag is mandatory",
        "--draft" in argv,
    )

    check(
        "task head is UUID-derived",
        BRANCH in argv,
    )

    check(
        "GitHub CLI uses shell false",
        kwargs["shell"] is False,
    )

    environment = kwargs["env"]

    check(
        "runner and GitHub tokens are not passed in environment",
        all(
            key not in environment
            for key in (
                "AI_TASK_RUNNER_API_TOKEN",
                "DATABASE_URL",
                "GH_TOKEN",
                "GITHUB_TOKEN",
            )
        ),
    )

    check(
        "GitHub prompting is disabled",
        environment.get("GH_PROMPT_DISABLED") == "1"
        and environment.get("GIT_TERMINAL_PROMPT") == "0",
    )

    existing_calls = []

    def existing_runner(argv, **kwargs):
        existing_calls.append(argv)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps([item]),
            stderr="",
        )

    existing = GitHubAdapter(
        fake_path,
        runner=existing_runner,
    ).ensure_draft_pr(ROOT, TASK_ID, COMMIT_SHA)

    check(
        "existing exact Draft PR is idempotent",
        existing.number == 123
        and not any(
            argv[1:3] == ["pr", "create"]
            for argv in existing_calls
        ),
    )

    wrong = dict(item)
    wrong["baseRefName"] = "other"

    bad_adapter = GitHubAdapter(
        fake_path,
        runner=lambda argv, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps([wrong]),
            stderr="",
        ),
    )

    check(
        "mismatched existing PR is rejected",
        rejects(
            lambda: bad_adapter.ensure_draft_pr(
                ROOT,
                TASK_ID,
                COMMIT_SHA,
            )
        ),
    )

    adapter = GitHubAdapter(
        fake_path,
        runner=lambda *_args, **_kwargs: None,
    )

    check(
        "merge operation is not allowlisted",
        rejects(
            lambda: adapter._run(
                (
                    "pr",
                    "merge",
                    "123",
                ),
                cwd=ROOT,
            )
        ),
    )

    check(
        "ready operation is not allowlisted",
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

    import threading

    stopped = threading.Event()
    stopped.set()

    stopped_adapter = GitHubAdapter(
        fake_path,
        runner=lambda *_args, **_kwargs: (
            _ for _ in ()
        ).throw(
            AssertionError(
                "runner must not start after lease loss"
            )
        ),
    )

    check(
        "Draft PR refuses to start after lease loss",
        rejects(
            lambda: stopped_adapter.ensure_draft_pr(
                ROOT,
                TASK_ID,
                COMMIT_SHA,
                stop_event=stopped,
            )
        ),
    )

    print("AI task Phase 2C GitHub checks passed")


if __name__ == "__main__":
    main()
