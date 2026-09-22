"""Safe Phase 2C Git publishing primitives.

This module deliberately avoids git add and git commit. Repository content is
hashed without filters into a temporary index, a commit object is created
without moving a local ref, and only the UUID task ref may be pushed.
"""

import os
import sys
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, Sequence
from uuid import UUID

from ai_task_git import EXPECTED_ORIGIN, GitResult, SHA_PATTERN
from ai_task_process import communicate_bounded, managed_process_options
from ai_task_safety import (
    expected_branch,
    is_reparse_point,
    validate_changed_paths,
    validate_sha,
)


MAX_PUBLISH_FILES = 2000
MAX_PUBLISH_FILE_BYTES = 50 * 1024 * 1024
MAX_PUBLISH_TOTAL_BYTES = 500 * 1024 * 1024
MAX_GIT_OUTPUT_BYTES = 128 * 1024


TEXT_EXTENSIONS = frozenset(
    {
        ".bat",
        ".cfg",
        ".cmd",
        ".conf",
        ".css",
        ".csv",
        ".html",
        ".ini",
        ".js",
        ".json",
        ".jsx",
        ".md",
        ".ps1",
        ".py",
        ".pyi",
        ".sh",
        ".sql",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".xml",
        ".yaml",
        ".yml",
    }
)

TEXT_BASENAMES = frozenset(
    {
        ".dockerignore",
        ".editorconfig",
        ".gitignore",
        "Dockerfile",
        "Makefile",
        "Procfile",
    }
)

MODE_PATTERN = re.compile(r"^100(?:644|755)$")
TASK_MESSAGE_PATTERN = re.compile(
    r"^chore\(ai\): task [0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}$"
)


class PublishSafetyError(RuntimeError):
    pass


class PublishDiffCheckError(PublishSafetyError):
    def __init__(self, stdout: str, stderr: str) -> None:
        diagnostics = "\n".join(
            part for part in (stdout.strip(), stderr.strip()) if part
        )
        if not diagnostics:
            diagnostics = "git diff --cached --check failed without diagnostics"
        super().__init__("publish diff check failed: " + diagnostics)
        self.stdout = stdout
        self.stderr = stderr
        self.diagnostics = diagnostics


@dataclass(frozen=True)
class PublishResult:
    commit_sha: str
    tree_sha: str
    changed_files: tuple[str, ...]


class GitPublisher:
    def __init__(
        self,
        git_path: Path,
        *,
        gcm_path: Path | None = None,
        gh_path: Path | None = None,
        runner: Callable[..., object] | None = None,
        popen: Callable[..., object] | None = None,
    ) -> None:
        resolved = git_path.resolve()
        if (
            not resolved.is_absolute()
            or not resolved.is_file()
            or resolved.is_symlink()
            or is_reparse_point(resolved)
        ):
            raise PublishSafetyError("Git executable is unsafe")
        self.git_path = resolved
        self.gcm_path = None

        if gcm_path is not None:
            gcm_resolved = gcm_path.resolve()
            if (
                not gcm_resolved.is_absolute()
                or not gcm_resolved.is_file()
                or gcm_resolved.is_symlink()
                or is_reparse_point(gcm_resolved)
            ):
                raise PublishSafetyError(
                    "Git Credential Manager executable is unsafe"
                )
            self.gcm_path = gcm_resolved

        self.gh_path = None
        if gh_path is not None:
            if (not gh_path.is_absolute() or not gh_path.is_file()
                    or gh_path.is_symlink() or is_reparse_point(gh_path)):
                raise PublishSafetyError("GitHub CLI executable is unsafe")
            self.gh_path = gh_path.resolve()

        self._runner = runner or subprocess.run
        self._popen = popen or subprocess.Popen

    @staticmethod
    def _safe_relative(value: str) -> bool:
        if not isinstance(value, str) or not value or "\0" in value:
            return False
        if "\\" in value or value.startswith("-"):
            return False
        path = Path(value)
        return not path.is_absolute() and ".." not in path.parts

    @classmethod
    def _is_allowed_argv(cls, args: tuple[str, ...]) -> bool:
        if args == (
            "config",
            "--local",
            "--no-includes",
            "--name-only",
            "--list",
        ):
            return True

        if len(args) == 2 and args[0] == "read-tree":
            return SHA_PATTERN.fullmatch(args[1]) is not None

        if (
            len(args) == 3
            and args[:2] == ("cat-file", "commit")
        ):
            return SHA_PATTERN.fullmatch(args[2]) is not None

        if (
            len(args) == 5
            and args[:4] == ("ls-files", "-s", "-z", "--")
        ):
            return cls._safe_relative(args[4])

        if args == (
            "hash-object",
            "-w",
            "--stdin",
            "--no-filters",
        ):
            return True

        if (
            len(args) == 5
            and args[0] == "update-index"
            and args[1] == "--cacheinfo"
        ):
            return (
                MODE_PATTERN.fullmatch(args[2]) is not None
                and SHA_PATTERN.fullmatch(args[3]) is not None
                and cls._safe_relative(args[4])
            )

        if (
            len(args) == 6
            and args[:3] == ("update-index", "--add", "--cacheinfo")
        ):
            return (
                args[3] == "100644"
                and SHA_PATTERN.fullmatch(args[4]) is not None
                and cls._safe_relative(args[5])
            )

        if (
            len(args) == 4
            and args[:3] == ("update-index", "--force-remove", "--")
        ):
            return cls._safe_relative(args[3])

        if args == ("diff", "--cached", "--check", "--no-ext-diff"):
            return True

        if args == ("write-tree",):
            return True

        if (
            len(args) == 6
            and args[0] == "commit-tree"
            and args[2] == "-p"
            and args[4] == "-m"
        ):
            return (
                SHA_PATTERN.fullmatch(args[1]) is not None
                and SHA_PATTERN.fullmatch(args[3]) is not None
                and TASK_MESSAGE_PATTERN.fullmatch(args[5]) is not None
            )

        if (
            len(args) == 7
            and args[:5]
            == (
                "diff-tree",
                "--no-commit-id",
                "--name-only",
                "-r",
                "-z",
            )
        ):
            return (
                SHA_PATTERN.fullmatch(args[5]) is not None
                and SHA_PATTERN.fullmatch(args[6]) is not None
            )

        if (
            len(args) == 4
            and args[:2] == ("ls-remote", "--heads")
            and args[2] == EXPECTED_ORIGIN
        ):
            prefix = "refs/heads/ai/task/"
            return (
                args[3].startswith(prefix)
                and cls._valid_task_branch(args[3][len("refs/heads/"):])
            )

        if (
            len(args) == 8
            and args[:6]
            == (
                "push",
                "--no-verify",
                "--porcelain",
                "--no-follow-tags",
                "--no-signed",
                "--recurse-submodules=no",
            )
            and args[6] == EXPECTED_ORIGIN
        ):
            return cls._valid_push_refspec(args[7])

        return False

    @staticmethod
    def _valid_task_branch(branch: str) -> bool:
        prefix = "ai/task/"
        if not branch.startswith(prefix):
            return False
        try:
            value = UUID(branch[len(prefix):])
        except (ValueError, TypeError):
            return False
        return branch == expected_branch(value)

    @classmethod
    def _valid_push_refspec(cls, value: str) -> bool:
        if ":" not in value:
            return False
        sha, remote_ref = value.split(":", 1)
        prefix = "refs/heads/"
        return (
            SHA_PATTERN.fullmatch(sha) is not None
            and remote_ref.startswith(prefix)
            and cls._valid_task_branch(remote_ref[len(prefix):])
        )

    @staticmethod
    def _base_environment() -> dict[str, str]:
        allowed = {
            "APPDATA",
            "COMSPEC",
            "HOME",
            "HOMEDRIVE",
            "HOMEPATH",
            "LOCALAPPDATA",
            "PATH",
            "PATHEXT",
            "SYSTEMDRIVE",
            "SYSTEMROOT",
            "TEMP",
            "TMP",
            "USERDOMAIN",
            "USERNAME",
            "USERPROFILE",
            "WINDIR",
        }

        environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in allowed
        }

        environment["GIT_TERMINAL_PROMPT"] = "0"
        environment["GCM_INTERACTIVE"] = "Never"
        return environment

    def _audit_local_network_config(
        self,
        cwd: Path,
        *,
        stop_event=None,
    ) -> dict[str, str]:
        environment = self._base_environment()
        if sys.platform == "linux":
            # Git 2.25 ignores GIT_CONFIG_GLOBAL. Hide both global config
            # locations, while gh retains only its trusted default auth location.
            home = environment.get("HOME", "")
            if not home or not Path(home).is_absolute():
                raise PublishSafetyError("trusted HOME is required for GitHub authentication")
            environment["GH_CONFIG_DIR"] = str(Path(home) / ".config" / "gh")
            environment["HOME"] = os.devnull
            environment["XDG_CONFIG_HOME"] = os.devnull
        environment["GIT_CONFIG_NOSYSTEM"] = "1"
        environment["GIT_CONFIG_SYSTEM"] = os.devnull
        environment["GIT_CONFIG_GLOBAL"] = os.devnull

        result = self._run(
            (
                "config",
                "--local",
                "--no-includes",
                "--name-only",
                "--list",
            ),
            cwd=cwd,
            environment=environment,
            timeout=10,
            stop_event=stop_event,
        )

        if result.returncode != 0:
            raise PublishSafetyError(
                "local Git configuration inspection failed"
            )

        forbidden_prefixes = (
            "include.",
            "includeif.",
            "url.",
            "http.",
            "credential.",
        )
        forbidden_exact = {
            "core.askpass",
            "core.gitproxy",
            "extensions.worktreeconfig",
        }

        for raw_key in result.stdout.splitlines():
            key = raw_key.strip().lower()
            if (
                key.startswith(forbidden_prefixes)
                or key in forbidden_exact
                or (key.startswith("remote.")
                    and key not in {"remote.origin.url", "remote.origin.fetch"})
            ):
                raise PublishSafetyError(
                    "local Git network configuration is unsafe"
                )

        return environment

    def _network_environment(
        self,
        cwd: Path,
        *,
        stop_event=None,
    ) -> dict[str, str]:
        environment = self._audit_local_network_config(
            cwd,
            stop_event=stop_event,
        )

        config = self._network_config()

        environment["GIT_CONFIG_COUNT"] = str(
            len(config)
        )

        for index, (key, value) in enumerate(config):
            environment[
                f"GIT_CONFIG_KEY_{index}"
            ] = key
            environment[
                f"GIT_CONFIG_VALUE_{index}"
            ] = value

        return environment

    def _network_config(self) -> tuple[tuple[str, str], ...]:
        if sys.platform == "linux":
            if self.gh_path is None:
                raise PublishSafetyError("GitHub CLI path is required for network publishing")
            helper = str(self.gh_path)
            # Git interprets helper values through its own shell. Accept only a
            # conservative absolute path, then escape spaces; no task input.
            if re.fullmatch(r"/[A-Za-z0-9._/ -]+", helper) is None:
                raise PublishSafetyError("GitHub CLI path cannot be safely quoted")
            helper = helper.replace(" ", r"\ ") + " auth git-credential"
        elif sys.platform == "win32":
            if self.gcm_path is None:
                raise PublishSafetyError("Git Credential Manager path is required for network publishing")
            helper = str(self.gcm_path).replace("\\", "/")
            if re.fullmatch(r"[A-Za-z]:/[A-Za-z0-9._/ -]+", helper) is None:
                raise PublishSafetyError("Git Credential Manager path cannot be safely quoted")
            helper = helper.replace(" ", r"\ ")
        else:
            raise PublishSafetyError("unsupported publishing platform")

        return (
            ("credential.helper", ""),
            (
                "credential.https://github.com.helper",
                "",
            ),
            (
                "credential.https://github.com.helper",
                helper,
            ),
            ("http.extraheader", ""),
            ("http.sslverify", "true"),
            ("http.followredirects", "initial"),
        )

    def _run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        environment: Mapping[str, str] | None = None,
        timeout: float = 60,
        stop_event=None,
    ) -> GitResult:
        values = tuple(args)

        if not self._is_allowed_argv(values):
            raise PublishSafetyError(
                "Git publish operation is not allowlisted"
            )

        env = (
            dict(environment)
            if environment is not None
            else self._base_environment()
        )

        config_args = []
        if sys.platform == "linux" and values[0] in ("push", "ls-remote"):
            # Command-line configuration works on Ubuntu 20.04's Git 2.25.
            # Values come only from reviewed code and the trusted CLI path.
            for key, value in self._network_config():
                config_args.extend(("-c", key + "=" + value))
        argv = [str(self.git_path), *config_args, *values]

        if stop_event is None:
            result = self._runner(
                argv,
                cwd=str(cwd.resolve()),
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                env=env,
            )

            stdout = result.stdout or ""
            stderr = result.stderr or ""

            if (
                len(stdout.encode("utf-8", errors="replace"))
                + len(stderr.encode("utf-8", errors="replace"))
                > MAX_GIT_OUTPUT_BYTES
            ):
                raise PublishSafetyError(
                    "Git publish output is too large"
                )

            return GitResult(
                result.returncode,
                stdout,
                stderr,
            )

        if stop_event.is_set():
            raise PublishSafetyError(
                "Git publish operation refused after lease loss"
            )

        process = self._popen(
            argv,
            **managed_process_options(),
            cwd=str(cwd.resolve()),
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )

        result = communicate_bounded(
            process,
            input_text=None,
            timeout=timeout,
            max_output_bytes=MAX_GIT_OUTPUT_BYTES,
            stop_event=stop_event,
        )

        if result.stopped:
            raise PublishSafetyError(
                "Git publish operation stopped after lease loss"
            )

        if result.timed_out:
            raise PublishSafetyError(
                "Git publish operation timed out"
            )

        if result.stdin_cleanup_failed:
            raise PublishSafetyError(
                "Git publish process cleanup failed"
            )

        return GitResult(
            result.returncode,
            result.stdout,
            result.stderr,
        )

    def _hash_payload(
        self,
        cwd: Path,
        payload: bytes,
    ) -> str:
        args = (
            "hash-object",
            "-w",
            "--stdin",
            "--no-filters",
        )

        if not self._is_allowed_argv(args):
            raise PublishSafetyError(
                "raw hash operation is not allowlisted"
            )

        result = self._runner(
            [str(self.git_path), *args],
            cwd=str(cwd.resolve()),
            shell=False,
            input=payload,
            capture_output=True,
            text=False,
            timeout=60,
            check=False,
            env=self._base_environment(),
        )

        stdout = result.stdout or b""

        if not isinstance(stdout, (bytes, bytearray)):
            raise PublishSafetyError(
                "raw hash returned invalid output"
            )

        try:
            sha = bytes(stdout).decode("ascii").strip()
        except UnicodeDecodeError as exc:
            raise PublishSafetyError(
                "raw hash returned non-ASCII output"
            ) from exc

        if (
            result.returncode != 0
            or SHA_PATTERN.fullmatch(sha) is None
        ):
            raise PublishSafetyError(
                "raw object hashing failed"
            )

        return sha

    @staticmethod
    def _canonical_payload(
        relative: str,
        payload: bytes,
    ) -> bytes:
        path = PurePosixPath(relative)
        suffix = path.suffix.lower()

        is_text = (
            suffix in TEXT_EXTENSIONS
            or path.name in TEXT_BASENAMES
        )

        # Unknown formats are opaque. Never guess that
        # "no NUL" means text; binary formats are not
        # required to contain a NUL byte.
        if not is_text:
            return payload

        if b"\0" in payload:
            raise PublishSafetyError(
                "text publish payload contains NUL"
            )

        try:
            payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PublishSafetyError(
                "text publish payload is not UTF-8"
            ) from exc

        return payload.replace(
            b"\r\n",
            b"\n",
        )

    def _deterministic_commit_time(
        self,
        cwd: Path,
        base_sha: str,
    ) -> str:
        result = self._run(
            ("cat-file", "commit", base_sha),
            cwd=cwd,
            timeout=30,
        )

        if result.returncode != 0:
            raise PublishSafetyError(
                "base commit inspection failed"
            )

        committer_lines = [
            line
            for line in result.stdout.splitlines()
            if line.startswith("committer ")
        ]

        if len(committer_lines) != 1:
            raise PublishSafetyError(
                "base commit metadata is malformed"
            )

        fields = committer_lines[0].rsplit(" ", 2)

        if len(fields) != 3:
            raise PublishSafetyError(
                "base commit timestamp is malformed"
            )

        try:
            epoch = int(fields[1])
        except ValueError as exc:
            raise PublishSafetyError(
                "base commit timestamp is invalid"
            ) from exc

        if epoch < 0 or epoch > 4_102_444_800:
            raise PublishSafetyError(
                "base commit timestamp is out of range"
            )

        commit_time = (
            datetime.fromtimestamp(
                epoch,
                tz=timezone.utc,
            )
            + timedelta(seconds=1)
        )

        return commit_time.isoformat()

    def _tracked_mode(
        self,
        cwd: Path,
        relative: str,
        environment: Mapping[str, str],
    ) -> str | None:
        result = self._run(
            ("ls-files", "-s", "-z", "--", relative),
            cwd=cwd,
            environment=environment,
        )
        if result.returncode != 0:
            raise PublishSafetyError("tracked file inspection failed")

        if not result.stdout:
            return None

        records = [item for item in result.stdout.split("\0") if item]
        if len(records) != 1 or "\t" not in records[0]:
            raise PublishSafetyError("tracked file entry is malformed")

        metadata, reported_path = records[0].split("\t", 1)
        fields = metadata.split()

        if (
            len(fields) != 3
            or fields[2] != "0"
            or reported_path != relative
            or MODE_PATTERN.fullmatch(fields[0]) is None
            or SHA_PATTERN.fullmatch(fields[1]) is None
        ):
            raise PublishSafetyError("tracked file entry is unsafe")

        return fields[0]

    def _build_candidate_index(
        self,
        cwd: Path,
        base_sha: str,
        changed_files: list[str],
    ) -> tuple[Path, dict[str, str], tuple[str, ...]]:
        validate_sha(base_sha)

        if (
            not changed_files
            or len(changed_files) > MAX_PUBLISH_FILES
            or len(set(changed_files)) != len(changed_files)
        ):
            raise PublishSafetyError("changed file set is unsafe")

        validate_changed_paths(cwd, changed_files)

        paths = tuple(sorted(changed_files))
        total_bytes = 0
        validated_sizes: dict[str, int] = {}

        for relative in paths:
            if not self._safe_relative(relative):
                raise PublishSafetyError("changed path is unsafe")

            candidate = cwd / relative

            if candidate.exists():
                if (
                    candidate.is_symlink()
                    or is_reparse_point(candidate)
                    or not candidate.is_file()
                ):
                    raise PublishSafetyError("publish path is not a regular file")

                size = candidate.stat().st_size

                if size > MAX_PUBLISH_FILE_BYTES:
                    raise PublishSafetyError("publish file is too large")

                total_bytes += size

                if total_bytes > MAX_PUBLISH_TOTAL_BYTES:
                    raise PublishSafetyError("publish file set is too large")

                validated_sizes[relative] = size

        fd, index_name = tempfile.mkstemp(
            prefix=".ichiyon-ai-index-",
            suffix=".tmp",
            dir=str(cwd.parent),
        )
        os.close(fd)
        os.unlink(index_name)

        index_path = Path(index_name)
        environment = self._base_environment()
        environment["GIT_INDEX_FILE"] = str(index_path)

        try:
            read_tree = self._run(
                ("read-tree", base_sha),
                cwd=cwd,
                environment=environment,
            )
            if read_tree.returncode != 0:
                raise PublishSafetyError("temporary index initialization failed")

            for relative in paths:
                candidate = cwd / relative
                tracked_mode = self._tracked_mode(
                    cwd,
                    relative,
                    environment,
                )

                if not candidate.exists():
                    if tracked_mode is None:
                        raise PublishSafetyError("untracked deletion is invalid")

                    removed = self._run(
                        ("update-index", "--force-remove", "--", relative),
                        cwd=cwd,
                        environment=environment,
                    )
                    if removed.returncode != 0:
                        raise PublishSafetyError("temporary index deletion failed")
                    continue

                expected_size = validated_sizes.get(relative)

                if expected_size is None:
                    raise PublishSafetyError(
                        "publish file was not prevalidated"
                    )

                try:
                    if (
                        candidate.is_symlink()
                        or is_reparse_point(candidate)
                        or not candidate.is_file()
                    ):
                        raise PublishSafetyError(
                            "publish path changed after validation"
                        )

                    before_read_size = candidate.stat().st_size
                    raw_payload = candidate.read_bytes()
                    after_read_size = candidate.stat().st_size
                except PublishSafetyError:
                    raise
                except OSError as exc:
                    raise PublishSafetyError(
                        "publish file could not be read"
                    ) from exc

                if (
                    before_read_size != expected_size
                    or len(raw_payload) != expected_size
                    or after_read_size != expected_size
                ):
                    raise PublishSafetyError(
                        "publish file changed during validation"
                    )

                payload = self._canonical_payload(
                relative,
                raw_payload,
            )
                blob_sha = self._hash_payload(
                    cwd,
                    payload,
                )

                if tracked_mode is None:
                    indexed = self._run(
                        (
                            "update-index",
                            "--add",
                            "--cacheinfo",
                            "100644",
                            blob_sha,
                            relative,
                        ),
                        cwd=cwd,
                        environment=environment,
                    )
                else:
                    indexed = self._run(
                        (
                            "update-index",
                            "--cacheinfo",
                            tracked_mode,
                            blob_sha,
                            relative,
                        ),
                        cwd=cwd,
                        environment=environment,
                    )

                if indexed.returncode != 0:
                    raise PublishSafetyError("temporary index update failed")

            return index_path, environment, paths
        except Exception:
            try:
                index_path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise PublishSafetyError(
                    "temporary index cleanup failed"
                ) from exc
            raise

    def validate_candidate_diff_check(
        self,
        cwd: Path,
        base_sha: str,
        changed_files: list[str],
    ) -> None:
        index_path, environment, _paths = self._build_candidate_index(
            cwd,
            base_sha,
            changed_files,
        )
        try:
            checked = self._run(
                ("diff", "--cached", "--check", "--no-ext-diff"),
                cwd=cwd,
                environment=environment,
            )
            if checked.returncode != 0:
                raise PublishDiffCheckError(
                    checked.stdout,
                    checked.stderr,
                )
        finally:
            try:
                index_path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise PublishSafetyError(
                    "temporary index cleanup failed"
                ) from exc

    def safe_commit_object(
        self,
        cwd: Path,
        task_id: UUID,
        base_sha: str,
        changed_files: list[str],
    ) -> PublishResult:
        validate_sha(base_sha)

        if not isinstance(task_id, UUID):
            raise PublishSafetyError("task ID must be UUID")

        index_path, environment, paths = self._build_candidate_index(
            cwd,
            base_sha,
            changed_files,
        )

        try:
            checked = self._run(
                ("diff", "--cached", "--check", "--no-ext-diff"),
                cwd=cwd,
                environment=environment,
            )
            if checked.returncode != 0:
                raise PublishDiffCheckError(
                    checked.stdout,
                    checked.stderr,
                )

            tree_result = self._run(
                ("write-tree",),
                cwd=cwd,
                environment=environment,
            )
            tree_sha = tree_result.stdout.strip()

            if (
                tree_result.returncode != 0
                or SHA_PATTERN.fullmatch(tree_sha) is None
            ):
                raise PublishSafetyError("tree creation failed")

            commit_environment = dict(environment)
            commit_time = self._deterministic_commit_time(
                cwd,
                base_sha,
            )
            commit_environment.update(
                {
                    "GIT_AUTHOR_NAME": "ichiyon-ai-runner",
                    "GIT_AUTHOR_EMAIL": "ai-runner@invalid.local",
                    "GIT_AUTHOR_DATE": commit_time,
                    "GIT_COMMITTER_NAME": "ichiyon-ai-runner",
                    "GIT_COMMITTER_EMAIL": "ai-runner@invalid.local",
                    "GIT_COMMITTER_DATE": commit_time,
                }
            )

            message = f"chore(ai): task {task_id}"

            commit_result = self._run(
                (
                    "commit-tree",
                    tree_sha,
                    "-p",
                    base_sha,
                    "-m",
                    message,
                ),
                cwd=cwd,
                environment=commit_environment,
            )
            commit_sha = commit_result.stdout.strip()

            if (
                commit_result.returncode != 0
                or SHA_PATTERN.fullmatch(commit_sha) is None
            ):
                raise PublishSafetyError("commit object creation failed")

            diff_result = self._run(
                (
                    "diff-tree",
                    "--no-commit-id",
                    "--name-only",
                    "-r",
                    "-z",
                    base_sha,
                    commit_sha,
                ),
                cwd=cwd,
            )

            if diff_result.returncode != 0:
                raise PublishSafetyError("commit tree verification failed")

            published = tuple(
                sorted(item for item in diff_result.stdout.split("\0") if item)
            )

            if published != paths:
                raise PublishSafetyError("commit contains unexpected paths")

            return PublishResult(commit_sha, tree_sha, paths)
        finally:
            try:
                index_path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise PublishSafetyError(
                    "temporary index cleanup failed"
                ) from exc

    def push_task_branch(
        self,
        cwd: Path,
        task_id: UUID,
        commit_sha: str,
        *,
        stop_event=None,
    ) -> str:
        validate_sha(commit_sha)

        if not isinstance(task_id, UUID):
            raise PublishSafetyError("task ID must be UUID")

        branch = expected_branch(task_id)
        remote_ref = "refs/heads/" + branch

        network_environment = self._network_environment(
            cwd,
            stop_event=stop_event,
        )

        before = self._run(
            ("ls-remote", "--heads", EXPECTED_ORIGIN, remote_ref),
            cwd=cwd,
            environment=network_environment,
            timeout=120,
            stop_event=stop_event,
        )

        if before.returncode != 0:
            raise PublishSafetyError("remote branch inspection failed")

        existing = self._parse_ls_remote(before.stdout, remote_ref)

        if existing is not None:
            if existing != commit_sha:
                raise PublishSafetyError(
                    "remote task branch already points elsewhere"
                )
            return existing

        refspec = f"{commit_sha}:{remote_ref}"

        pushed = self._run(
            (
                "push",
                "--no-verify",
                "--porcelain",
                "--no-follow-tags",
                "--no-signed",
                "--recurse-submodules=no",
                EXPECTED_ORIGIN,
                refspec,
            ),
            cwd=cwd,
            environment=network_environment,
            timeout=180,
            stop_event=stop_event,
        )

        if pushed.returncode != 0:
            raise PublishSafetyError("task branch push failed")

        after = self._run(
            ("ls-remote", "--heads", EXPECTED_ORIGIN, remote_ref),
            cwd=cwd,
            environment=network_environment,
            timeout=120,
            stop_event=stop_event,
        )

        if after.returncode != 0:
            raise PublishSafetyError("remote branch verification failed")

        verified = self._parse_ls_remote(after.stdout, remote_ref)

        if verified != commit_sha:
            raise PublishSafetyError("remote task branch SHA mismatch")

        return verified

    @staticmethod
    def _parse_ls_remote(value: str, expected_ref: str) -> str | None:
        if not value.strip():
            return None

        lines = [line for line in value.splitlines() if line]

        if len(lines) != 1 or "\t" not in lines[0]:
            raise PublishSafetyError("remote branch response is malformed")

        sha, ref = lines[0].split("\t", 1)

        if SHA_PATTERN.fullmatch(sha) is None or ref != expected_ref:
            raise PublishSafetyError("remote branch response is invalid")

        return sha
