"""Offline checks for the Phase 2D read-only Codex reviewer."""

import io
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from ai_task_code_review import (
    CodeReviewAdapter,
    CodeReviewSafetyError,
)


TASK_ID = UUID(
    "00000000-0000-0000-0000-000000000001"
)
BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40
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
        CodeReviewSafetyError,
    ):
        return True
    return False


class FakeStdin:
    def __init__(self):
        self.data = bytearray()
        self.closed = False

    def write(self, value):
        self.data.extend(value)
        return len(value)

    def close(self):
        self.closed = True


class FakeProcess:
    _next_pid = 50000

    def __init__(self, returncode=0):
        self.stdin = FakeStdin()
        self.stdout = io.BytesIO(b"")
        self.stderr = io.BytesIO(b"")
        self.returncode = returncode
        self.pid = FakeProcess._next_pid
        FakeProcess._next_pid += 1

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


def approve_payload(
    *,
    base_sha=BASE_SHA,
    head_sha=HEAD_SHA,
    findings=None,
):
    return {
        "version": 1,
        "decision": "approve",
        "reviewed_base_sha": base_sha,
        "reviewed_head_sha": head_sha,
        "summary": "No blocking review findings.",
        "findings": (
            []
            if findings is None
            else findings
        ),
    }


def reject_payload():
    return {
        "version": 1,
        "decision": "reject",
        "reviewed_base_sha": BASE_SHA,
        "reviewed_head_sha": HEAD_SHA,
        "summary": "A regression was found.",
        "findings": [
            {
                "severity": "major",
                "path": "bot/example.py",
                "line": 10,
                "message": "Example regression.",
            }
        ],
    }


def make_popen(payload, *, returncode=0):
    calls = []

    def popen(argv, **kwargs):
        output_index = argv.index("-o") + 1
        output_path = Path(argv[output_index])

        if isinstance(payload, bytes):
            output_path.write_bytes(payload)
        else:
            output_path.write_text(
                payload,
                encoding="utf-8",
            )

        process = FakeProcess(
            returncode=returncode
        )

        calls.append(
            {
                "argv": argv,
                "kwargs": kwargs,
                "process": process,
                "output_path": output_path,
            }
        )

        return process

    return popen, calls


def run_with(
    payload,
    *,
    returncode=0,
    stop_event=None,
    files=FILES,
):
    with tempfile.TemporaryDirectory() as temp:
        popen, calls = make_popen(
            payload,
            returncode=returncode,
        )

        adapter = CodeReviewAdapter(
            Path(sys.executable).resolve(),
            popen=popen,
            temp_root=Path(temp),
        )

        worktree = Path(temp) / "worktree"
        worktree.mkdir()
        result = adapter.run(
            worktree,
            task_id=TASK_ID,
            task_description="Add the requested feature.",
            base_sha=BASE_SHA,
            head_sha=HEAD_SHA,
            changed_files=files,
            timeout=30,
            stop_event=stop_event,
        )

        output_path = (
            calls[0]["output_path"]
            if calls
            else None
        )

        return result, calls, output_path


def main():
    old_values = {
        key: os.environ.get(key)
        for key in (
            "AI_TASK_RUNNER_API_TOKEN",
            "DATABASE_URL",
            "DISCORD_TOKEN",
            "GH_TOKEN",
            "GITHUB_TOKEN",
            "OPENAI_API_KEY",
        )
    }

    for key in old_values:
        os.environ[key] = "secret"

    try:
        payload = json.dumps(
            approve_payload()
        )

        result, calls, output_path = run_with(
            payload
        )
    finally:
        for key, value in old_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    check(
        "exact approve JSON passes review gate",
        result.approved is True
        and result.decision == "approve"
        and result.base_sha == BASE_SHA
        and result.head_sha == HEAD_SHA
        and result.findings == (),
    )

    check(
        "Codex reviewer uses read-only sandbox",
        "--sandbox" in calls[0]["argv"]
        and (
            calls[0]["argv"][
                calls[0]["argv"].index(
                    "--sandbox"
                )
                + 1
            ]
            == "read-only"
        )
        and "workspace-write"
        not in calls[0]["argv"],
    )

    check(
        "Codex reviewer uses approval never",
        'approval_policy="never"'
        in calls[0]["argv"],
    )

    check(
        "Codex reviewer disables network",
        (
            "sandbox_workspace_write."
            "network_access=false"
        )
        in calls[0]["argv"],
    )

    check(
        "Codex reviewer disables project instructions",
        "project_doc_max_bytes=0"
        in calls[0]["argv"]
        and "project_doc_fallback_filenames=[]"
        in calls[0]["argv"],
    )

    check(
        "Codex reviewer uses shell false",
        calls[0]["kwargs"]["shell"] is False,
    )

    environment = calls[0]["kwargs"]["env"]

    check(
        "reviewer child excludes secrets",
        all(
            key not in environment
            for key in old_values
        ),
    )

    prompt = bytes(
        calls[0]["process"].stdin.data
    ).decode(
        "utf-8",
        errors="strict",
    )

    check(
        "review prompt binds exact SHAs",
        BASE_SHA in prompt
        and HEAD_SHA in prompt,
    )

    check(
        "review prompt treats task as untrusted",
        "task_description_untrusted_json"
        in prompt
        and "untrusted data" in prompt,
    )

    check(
        "review prompt respects pre-merge phase boundary",
        "This review runs before merge and production deployment."
        in prompt
        and "Do not reject solely because later pipeline stages"
        in prompt
        and "deterministic automated tests"
        in prompt
        and "safe fixed later-stage verifier"
        in prompt
        and "does not implement a safe verification mechanism"
        in prompt,
    )

    check(
        "temporary review output is cleaned",
        output_path is not None
        and not output_path.exists(),
    )

    rejected, _, _ = run_with(
        json.dumps(
            reject_payload()
        )
    )

    check(
        "valid reject JSON blocks approval",
        rejected.approved is False
        and rejected.decision == "reject"
        and len(rejected.findings) == 1,
    )

    check(
        "malformed JSON is rejected",
        rejects(
            lambda: run_with(
                "not-json"
            )
        ),
    )

    check(
        "mismatched reviewed head SHA is rejected",
        rejects(
            lambda: run_with(
                json.dumps(
                    approve_payload(
                        head_sha="c" * 40,
                    )
                )
            )
        ),
    )

    check(
        "mismatched reviewed base SHA is rejected",
        rejects(
            lambda: run_with(
                json.dumps(
                    approve_payload(
                        base_sha="c" * 40,
                    )
                )
            )
        ),
    )

    bad_finding = {
        "severity": "major",
        "path": "bot/example.py",
        "line": 1,
        "message": "Must not accompany approve.",
    }

    check(
        "approve with findings is rejected",
        rejects(
            lambda: run_with(
                json.dumps(
                    approve_payload(
                        findings=[bad_finding],
                    )
                )
            )
        ),
    )

    check(
        "finding outside changed files is rejected",
        rejects(
            lambda: run_with(
                json.dumps(
                    {
                        **reject_payload(),
                        "findings": [
                            {
                                "severity": "major",
                                "path":
                                "bot/unrelated.py",
                                "line": 1,
                                "message":
                                "Wrong path.",
                            }
                        ],
                    }
                )
            )
        ),
    )

    result_for_control_path, _calls_for_control_path, _output_for_control_path = run_with(
        json.dumps(
            approve_payload()
        ),
        files=(
            "scripts/ai_task_runner.py",
        ),
    )
    check(
        "review allows control-plane repository paths",
        result_for_control_path.approved is True,
    )

    check(
        "nonzero Codex exit fails closed",
        rejects(
            lambda: run_with(
                json.dumps(
                    approve_payload()
                ),
                returncode=1,
            )
        ),
    )

    huge = "x" * (70 * 1024)

    check(
        "oversized review output is rejected",
        rejects(
            lambda: run_with(
                huge
            )
        ),
    )

    stopped = threading.Event()
    stopped.set()

    popen_calls = []

    def must_not_start(*args, **kwargs):
        popen_calls.append((args, kwargs))
        raise AssertionError(
            "Codex must not start"
        )

    with tempfile.TemporaryDirectory() as temp:
        adapter = CodeReviewAdapter(
            Path(sys.executable).resolve(),
            popen=must_not_start,
            temp_root=Path(temp),
        )

        stopped_rejected = rejects(
            lambda: adapter.run(
                ROOT,
                task_id=TASK_ID,
                task_description="Review.",
                base_sha=BASE_SHA,
                head_sha=HEAD_SHA,
                changed_files=FILES,
                timeout=30,
                stop_event=stopped,
            )
        )

    check(
        "lease loss before review blocks Codex start",
        stopped_rejected
        and not popen_calls,
    )

    print(
        "AI task Phase 2D code-review checks passed"
    )


if __name__ == "__main__":
    main()
