"""Offline checks for Phase 2C safe Git publishing."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PureWindowsPath
from unittest.mock import patch
from types import SimpleNamespace
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from ai_task_git import EXPECTED_ORIGIN
from ai_task_publish import GitPublisher, PublishSafetyError
from ai_task_safety import is_protected_path


TASK_ID = UUID("00000000-0000-0000-0000-000000000001")


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print(f"PASS {name}")


def run(argv, cwd):
    return subprocess.run(
        argv,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def rejects(call):
    try:
        call()
    except (ValueError, RuntimeError, PublishSafetyError):
        return True
    return False


@patch("ai_task_publish.sys", SimpleNamespace(platform="win32"))
def main():
    git_value = shutil.which("git")
    if not git_value:
        raise RuntimeError("git is required")

    git_path = Path(git_value).resolve()

    secret_keys = (
        "AI_TASK_RUNNER_API_TOKEN",
        "DATABASE_URL",
        "DISCORD_TOKEN",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "SOME_API_KEY",
    )
    old_secret_values = {
        key: os.environ.get(key)
        for key in secret_keys
    }

    for key in secret_keys:
        os.environ[key] = "secret"

    try:
        publish_environment = GitPublisher._base_environment()
    finally:
        for key, value in old_secret_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    check(
        "publisher child environment excludes secrets",
        all(
            key not in publish_environment
            for key in secret_keys
        ),
    )

    check(
        "Phase 2C sensitive paths are protected",
        all(
            is_protected_path(value)
            for value in (
                ".gitattributes",
                "scripts/ai_task_publish.py",
                "scripts/ai_task_github.py",
            )
        ),
    )

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "repo"
        root.mkdir()

        check(
            "temporary git init",
            run(
                [str(git_path), "init", "-q"],
                root,
            ).returncode
            == 0,
        )

        check(
            "temporary main branch",
            run([str(git_path), "symbolic-ref", "HEAD", "refs/heads/main"], root).returncode == 0,
        )
        run(
            [str(git_path), "config", "user.name", "Phase2C Check"],
            root,
        )
        run(
            [
                str(git_path),
                "config",
                "user.email",
                "phase2c-check@invalid.local",
            ],
            root,
        )

        (root / "tracked.txt").write_text("BASE\n", encoding="utf-8")
        (root / "deleted.txt").write_text("DELETE\n", encoding="utf-8")

        run(
            [
                str(git_path),
                "add",
                "--",
                "tracked.txt",
                "deleted.txt",
            ],
            root,
        )

        baseline = run(
            [str(git_path), "commit", "-q", "-m", "baseline"],
            root,
        )
        check("temporary baseline commit", baseline.returncode == 0)

        base_sha = run(
            [str(git_path), "rev-parse", "HEAD"],
            root,
        ).stdout.strip()

        # Use explicit CRLF bytes so this regression
        # is meaningful on Windows and non-Windows hosts.
        (root / "tracked.txt").write_bytes(
            b"CHANGED\r\n"
        )
        (root / "new.txt").write_bytes(
            b"NEW\r\n"
        )
        (root / "deleted.txt").unlink()

        check(
            "deleted changed path passes safety validation",
            not rejects(
                lambda: __import__(
                    "ai_task_safety"
                ).validate_changed_paths(
                    root,
                    ["deleted.txt"],
                )
            ),
        )

        before_index = run(
            [str(git_path), "diff", "--cached", "--name-only"],
            root,
        ).stdout

        publisher = GitPublisher(git_path)

        opaque_payload = (
            b"\x01\xff\r\n\x02\xfe"
        )

        check(
            "unknown binary payload is byte-for-byte preserved",
            publisher._canonical_payload(
                "assets/blob.bin",
                opaque_payload,
            )
            == opaque_payload,
        )

        check(
            "known text invalid UTF-8 is rejected",
            rejects(
                lambda: publisher._canonical_payload(
                    "bad.txt",
                    b"\xff\xfe",
                )
            ),
        )

        check(
            "known text NUL is rejected",
            rejects(
                lambda: publisher._canonical_payload(
                    "bad.py",
                    b"pass\0\n",
                )
            ),
        )

        result = publisher.safe_commit_object(
            root,
            TASK_ID,
            base_sha,
            ["tracked.txt", "new.txt", "deleted.txt"],
        )

        repeated_result = publisher.safe_commit_object(
            root,
            TASK_ID,
            base_sha,
            ["tracked.txt", "new.txt", "deleted.txt"],
        )

        check(
            "safe commit returns SHA",
            len(result.commit_sha) == 40,
        )

        check(
            "same task tree produces deterministic commit SHA",
            repeated_result.commit_sha
            == result.commit_sha
            and repeated_result.tree_sha
            == result.tree_sha,
        )

        current_head = run(
            [str(git_path), "rev-parse", "HEAD"],
            root,
        ).stdout.strip()

        check(
            "safe commit does not move local HEAD",
            current_head == base_sha,
        )

        after_index = run(
            [str(git_path), "diff", "--cached", "--name-only"],
            root,
        ).stdout

        check(
            "safe commit does not modify real index",
            before_index == after_index == "",
        )

        tracked = run(
            [
                str(git_path),
                "cat-file",
                "blob",
                f"{result.commit_sha}:tracked.txt",
            ],
            root,
        )
        new_file = run(
            [
                str(git_path),
                "cat-file",
                "blob",
                f"{result.commit_sha}:new.txt",
            ],
            root,
        )
        deleted = run(
            [
                str(git_path),
                "cat-file",
                "-e",
                f"{result.commit_sha}:deleted.txt",
            ],
            root,
        )

        raw_tracked = subprocess.run(
            [
                str(git_path),
                "cat-file",
                "blob",
                (
                    f"{result.commit_sha}:"
                    "tracked.txt"
                ),
            ],
            cwd=str(root),
            shell=False,
            capture_output=True,
            text=False,
            check=False,
        )

        raw_new = subprocess.run(
            [
                str(git_path),
                "cat-file",
                "blob",
                f"{result.commit_sha}:new.txt",
            ],
            cwd=str(root),
            shell=False,
            capture_output=True,
            text=False,
            check=False,
        )

        check(
            "safe commit contains tracked change",
            tracked.returncode == 0
            and tracked.stdout == "CHANGED\n",
        )
        check(
            "safe text hashing canonicalizes CRLF as raw bytes",
            raw_tracked.returncode == 0
            and raw_tracked.stdout
            == b"CHANGED\n"
            and raw_new.returncode == 0
            and raw_new.stdout
            == b"NEW\n",
        )
        check(
            "safe commit contains new file",
            new_file.returncode == 0
            and new_file.stdout == "NEW\n",
        )
        check(
            "safe commit contains deletion",
            deleted.returncode != 0,
        )

    calls = []
    commit_sha = "a" * 40
    task_ref = (
        "refs/heads/ai/task/"
        "00000000-0000-0000-0000-000000000001"
    )

    def fake_git(argv, **kwargs):
        calls.append((argv, kwargs))
        args = argv[1:]

        if args == [
            "config",
            "--local",
            "--no-includes",
            "--name-only",
            "--list",
        ]:
            return SimpleNamespace(
                returncode=0,
                stdout="",
                stderr="",
            )

        if args[:2] == ["ls-remote", "--heads"]:
            previous_push = any(
                call[0][1]
                == "push"
                for call in calls[:-1]
                if len(call[0]) > 1
            )
            value = (
                f"{commit_sha}\t{task_ref}\n"
                if previous_push
                else ""
            )
            return SimpleNamespace(
                returncode=0,
                stdout=value,
                stderr="",
            )

        if args and args[0] == "push":
            return SimpleNamespace(
                returncode=0,
                stdout="ok",
                stderr="",
            )

        raise AssertionError(args)

    fake_path = Path(sys.executable).resolve()

    # The constructor still validates a real local file.
    # Explicitly exercise the preserved Windows helper on Linux CI.
    fake_windows_gcm = PureWindowsPath(
        "C:/Program Files/Git/mingw64/bin/"
        "git-credential-manager.exe"
    )

    fake_publisher = GitPublisher(
        fake_path,
        gcm_path=fake_path,
        runner=fake_git,
    )
    fake_publisher.gcm_path = fake_windows_gcm

    pushed = fake_publisher.push_task_branch(
        ROOT,
        TASK_ID,
        commit_sha,
    )

    check(
        "push verifies exact remote SHA",
        pushed == commit_sha,
    )

    push_calls = [
        call
        for call in calls
        if len(call[0]) > 1 and call[0][1] == "push"
    ]

    check(
        "push uses fixed repository URL",
        len(push_calls) == 1
        and EXPECTED_ORIGIN in push_calls[0][0],
    )

    push_environment = push_calls[0][1]["env"]

    check(
        "push disables system and global Git config",
        push_environment.get("GIT_CONFIG_NOSYSTEM") == "1"
        and push_environment.get("GIT_CONFIG_GLOBAL"),
    )

    config_count = int(
        push_environment["GIT_CONFIG_COUNT"]
    )

    config_pairs = [
        (
            push_environment[
                f"GIT_CONFIG_KEY_{index}"
            ],
            push_environment[
                f"GIT_CONFIG_VALUE_{index}"
            ],
        )
        for index in range(config_count)
    ]

    check(
        "push resets helpers and pins one credential helper",
        ("credential.helper", "") in config_pairs
        and (
            "credential.https://github.com.helper",
            "",
        ) in config_pairs
        and sum(
            1
            for key, value in config_pairs
            if (
                key
                == "credential.https://github.com.helper"
                and value
            )
        )
        == 1,
    )
    check(
        "push bypasses hooks without force",
        "--no-verify" in push_calls[0][0]
        and not any(
            value.startswith("--force")
            for value in push_calls[0][0]
        ),
    )
    check(
        "push disables tag signing and submodule recursion",
        "--no-follow-tags" in push_calls[0][0]
        and "--no-signed" in push_calls[0][0]
        and "--recurse-submodules=no" in push_calls[0][0],
    )

    mismatch_publisher = GitPublisher(
        fake_path,
        gcm_path=fake_path,
        runner=lambda argv, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=(
                ("b" * 40)
                + "\t"
                + task_ref
                + "\n"
            ),
            stderr="",
        ),
    )
    mismatch_publisher.gcm_path = fake_windows_gcm

    check(
        "existing remote mismatch is rejected",
        rejects(
            lambda: mismatch_publisher.push_task_branch(
                ROOT,
                TASK_ID,
                commit_sha,
            )
        ),
    )

    import threading

    stopped = threading.Event()
    stopped.set()

    check(
        "push refuses to start after lease loss",
        rejects(
            lambda: fake_publisher.push_task_branch(
                ROOT,
                TASK_ID,
                commit_sha,
                stop_event=stopped,
            )
        ),
    )

    print("AI task Phase 2C publish checks passed")


if __name__ == "__main__":
    main()
