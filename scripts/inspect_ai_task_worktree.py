"""Read-only JSON inspection of a Git checkout or linked worktree."""

import argparse
import json
from pathlib import Path
import subprocess
import sys

try:
    from .ai_task_diagnostics import redact_secrets
except ImportError:
    from ai_task_diagnostics import redact_secrets


def git(repo: Path, *args: str, allowed=(0,)) -> str:
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(repo), *args],
            capture_output=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Cannot run git {args[0]}: {exc}") from None
    if result.returncode not in allowed:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git {' '.join(args)} failed ({result.returncode}): {detail}")
    return result.stdout.decode("utf-8", errors="surrogateescape")


def inspect(repo: Path) -> dict:
    if git(repo, "rev-parse", "--is-inside-work-tree").strip() != "true":
        raise RuntimeError("--repo must identify a Git working tree, not a bare repository")
    branch = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", allowed=(0, 1)).strip() or None
    head = git(repo, "rev-parse", "--verify", "--quiet", "HEAD", allowed=(0, 1)).strip() or None
    records = git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--renames").split("\0")
    changes = []
    index = 0
    while index < len(records) - 1:
        record = records[index]
        index += 1
        if len(record) < 4 or record[2] != " ":
            raise RuntimeError("Invalid Git porcelain status record")
        change = {"status": record[:2], "path": record[3:]}
        if "R" in record[:2] or "C" in record[:2]:
            if index >= len(records) - 1 or not records[index]:
                raise RuntimeError("Missing Git porcelain rename/copy source")
            change["original_path"] = records[index]
            index += 1
        changes.append(change)
    return {"branch": branch, "head": head, "changes": changes}


def redact_report(value):
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, list):
        return [redact_report(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_report(item) for key, item in value.items()}
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path, help="Git checkout or linked worktree directory")
    args = parser.parse_args()
    try:
        report = inspect(args.repo)
    except RuntimeError as exc:
        print(json.dumps({"error": redact_secrets(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(redact_report(report), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
