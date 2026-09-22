"""Read-only JSON inspection of a local Git checkout; no runner configuration needed."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from ai_task_diagnostics import redact_secrets


class InspectionError(RuntimeError):
    pass


def git(repo: Path, *args: str, allowed=(0,)) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(repo), *args],
            capture_output=True, timeout=30, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise InspectionError(f"git {' '.join(args)} timed out after 30s") from exc
    except OSError as exc:
        raise InspectionError(f"Could not run git: {exc}") from exc
    if result.returncode not in allowed:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise InspectionError(f"git {' '.join(args)} failed (exit {result.returncode}): {detail}")
    return result


def parse_changes(raw: bytes) -> list[dict]:
    """Porcelain v1 -z puts the destination first, then the rename/copy source."""
    records = iter(raw.decode("utf-8", errors="surrogateescape").split("\0")[:-1])
    if raw and not raw.endswith(b"\0"):
        raise InspectionError("Git status output is missing its NUL terminator")
    changes = []
    for record in records:
        if len(record) < 4 or record[2] != " ":
            raise InspectionError("Invalid Git porcelain status record")
        change = {"status": record[:2], "path": record[3:]}
        if "R" in record[:2] or "C" in record[:2]:
            source = next(records, None)
            if not source:
                raise InspectionError("Git rename/copy record is missing its source")
            change["original_path"] = source
        changes.append(change)
    return changes


def inspect(repo: Path) -> dict:
    inside = git(repo, "rev-parse", "--is-inside-work-tree").stdout.strip()
    if inside != b"true":
        raise InspectionError("--repo must identify a Git working tree, not a bare repository")
    branch = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", allowed=(0, 1))
    head = git(repo, "rev-parse", "--verify", "--quiet", "HEAD", allowed=(0, 1))
    status = git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--renames")
    return {
        "branch": branch.stdout.decode("utf-8", errors="surrogateescape").strip() if branch.returncode == 0 else None,
        "head": head.stdout.decode("ascii").strip() if head.returncode == 0 else None,
        "changes": parse_changes(status.stdout),
    }


def redacted(value):
    # Redact strings before JSON encoding so quotes/backslashes cannot break JSON.
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, list):
        return [redacted(item) for item in value]
    if isinstance(value, dict):
        return {key: redacted(item) for key, item in value.items()}
    return value


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True, help="Git checkout directory (or a directory within it)")
    args = parser.parse_args(argv)
    try:
        report = inspect(args.repo)
    except InspectionError as exc:
        print(json.dumps({"error": redact_secrets(exc)}, ensure_ascii=True), file=sys.stderr)
        return 1
    print(json.dumps(redacted(report), ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
