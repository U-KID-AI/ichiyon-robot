"""Trusted exact-commit Minecraft/BDS deployment transport."""

import base64
import os
import re
import shlex
import subprocess
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath

from ai_task_deploy_config import (
    DeploymentError,
    exception_detail,
    normal_file,
    process_failure,
    proof_error,
    proof_fields,
    validate_host,
)
from ai_task_git import EXPECTED_ORIGIN
from ai_task_minecraft_deploy import (
    DeploymentResult,
    prepare_pack_archive,
    validate_sha,
)
from ai_task_process import communicate_bounded, managed_process_options


TEMPLATE = Path(__file__).absolute().with_name("ai_task_minecraft_deploy_remote.py")
PLACEHOLDER = "__ICHYON_ARCHIVE_BASE64__"
SUMMARY = "Minecraft BDS deployment verified."


@dataclass(frozen=True, repr=False)
class MinecraftDeployConfig:
    ssh_path: Path
    ssh_host: str
    ssh_user: str
    ssh_key_path: Path
    known_hosts_path: Path
    data_root: str
    world_name: str
    container: str
    excluded_roots: tuple[Path, ...] = field(repr=False)
    timeout: float = 1800
    max_output_bytes: int = 65536

    def validate(self) -> None:
        validate_host(self.ssh_host)

        if self.ssh_user != "ubuntu":
            raise DeploymentError(
                "Minecraft deployment configuration rejected"
            )

        roots = (
            *self.excluded_roots,
            Path(__file__).resolve().parent.parent,
        )

        for path in (
            self.ssh_path,
            self.ssh_key_path,
            self.known_hosts_path,
        ):
            normal_file(path, roots)

        if (
            not isinstance(self.data_root, str)
            or not PurePosixPath(self.data_root).is_absolute()
            or "\0" in self.data_root
            or ".." in self.data_root.split("/")
        ):
            raise DeploymentError(
                "Minecraft data root rejected"
            )

        if (not isinstance(self.world_name, str) or self.world_name in ("", ".", "..")
                or "/" in self.world_name or "\0" in self.world_name):
            raise DeploymentError("Minecraft world name must be one path component")
        if (not isinstance(self.container, str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", self.container) is None):
            raise DeploymentError("Minecraft container name rejected")

        if (
            not isinstance(self.timeout, (int, float))
            or not 0 < float(self.timeout) <= 7200
        ):
            raise DeploymentError(
                "Minecraft deployment timeout rejected"
            )

        if (
            type(self.max_output_bytes) is not int
            or not 8192
            <= self.max_output_bytes
            <= 1048576
        ):
            raise DeploymentError(
                "Minecraft deployment output limit rejected"
            )

    @classmethod
    def from_environment(
        cls,
        *,
        repo_root: Path,
        worktree_root: Path,
    ) -> "MinecraftDeployConfig":
        try:
            prefix = "AI_TASK_RUNNER_BDS_"

            config = cls(
                ssh_path=Path(
                    os.environ[prefix + "SSH_PATH"]
                ),
                ssh_host=os.environ[
                    prefix + "SSH_HOST"
                ],
                ssh_user=os.environ[
                    prefix + "SSH_USER"
                ],
                ssh_key_path=Path(
                    os.environ[prefix + "SSH_KEY_PATH"]
                ),
                known_hosts_path=Path(
                    os.environ[
                        prefix + "KNOWN_HOSTS_PATH"
                    ]
                ),
                data_root=os.environ[
                    prefix + "DATA_ROOT"
                ],
                world_name=os.environ[
                    prefix + "WORLD_NAME"
                ],
                container=os.environ[
                    prefix + "CONTAINER"
                ],
                excluded_roots=(
                    repo_root,
                    worktree_root,
                    Path(__file__)
                    .resolve()
                    .parent
                    .parent,
                ),
                timeout=float(
                    os.environ.get(
                        prefix + "TIMEOUT_SECONDS",
                        "1800",
                    )
                ),
                max_output_bytes=int(
                    os.environ.get(
                        prefix + "MAX_OUTPUT_BYTES",
                        "65536",
                    )
                ),
            )

            config.validate()
            return config

        except (
            KeyError,
            ValueError,
            TypeError,
            OSError,
        ) as exc:
            raise DeploymentError(
                "Minecraft deployment configuration invalid: " + exception_detail(exc)
            ) from None


class ExactMergeSource:
    """Read exact commits without using or modifying working-tree edits."""

    def __init__(
        self,
        repo_root: Path,
        git_path: Path,
    ):
        self.repo_root = repo_root.resolve()
        self.git_path = git_path.resolve()

    def _run(
        self,
        args,
        *,
        binary=False,
        timeout=120,
    ):
        kwargs = {
            "cwd": str(self.repo_root),
            "shell": False,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "timeout": timeout,
            "check": False,
        }

        if not binary:
            kwargs.update(
                {
                    "text": True,
                    "encoding": "utf-8",
                    "errors": "replace",
                }
            )

        try:
            result = subprocess.run([str(self.git_path), *args], **kwargs)
        except (OSError, subprocess.SubprocessError) as exc:
            raise DeploymentError("Minecraft source git " + args[0] + ": " + exception_detail(exc)) from None
        if result.returncode != 0:
            raise DeploymentError(process_failure("Minecraft source git " + args[0], result))
        return result

    def _require_repo(self) -> None:
        if (
            not self.repo_root.is_dir()
            or not (
                self.repo_root / ".git"
            ).exists()
        ):
            raise DeploymentError(
                "Minecraft source repository rejected"
            )

        git_dir = self._run(
            ("rev-parse", "--git-dir")
        )
        root = self._run(
            ("rev-parse", "--show-toplevel")
        )
        remote = self._run(
            ("remote", "get-url", "origin")
        )
        if (
            git_dir.returncode != 0
            or not git_dir.stdout.strip()
            or root.returncode != 0
            or Path(
                root.stdout.strip()
            ).resolve()
            != self.repo_root
            or remote.returncode != 0
            or remote.stdout.strip().rstrip("/")
            != EXPECTED_ORIGIN.rstrip("/")
        ):
            raise DeploymentError(
                "Minecraft source repository rejected"
            )

    def _fetch_main(self) -> str:
        self._require_repo()

        result = self._run(
            (
                "fetch",
                "--quiet",
                "--no-tags",
                "origin",
                "main",
            )
        )

        if result.returncode != 0:
            raise DeploymentError(
                process_failure("Minecraft source fetch failed", result)
            )

        result = self._run(
            ("rev-parse", "origin/main")
        )

        sha = result.stdout.strip()

        try:
            validate_sha(sha)
        except Exception:
            raise DeploymentError(
                "Minecraft source main SHA rejected"
            ) from None

        if result.returncode != 0:
            raise DeploymentError(
                "Minecraft source main SHA rejected"
            )

        return sha

    def current_main_sha(self) -> str:
        # systemd ExecStartPre has already synchronized the trusted source.
        # Catch-up must not add a network dependency in front of ordinary tasks.
        self._require_repo()

        result = self._run(
            ("rev-parse", "origin/main")
        )

        sha = result.stdout.strip()

        try:
            validate_sha(sha)
        except Exception:
            raise DeploymentError(
                "Minecraft source main SHA rejected"
            ) from None

        if result.returncode != 0:
            raise DeploymentError(
                "Minecraft source main SHA rejected"
            )

        return sha

    def _require_commit(
        self,
        sha: str,
        *,
        refresh_source: bool = True,
    ) -> None:
        validate_sha(sha)

        if type(refresh_source) is not bool:
            raise DeploymentError(
                "Minecraft source refresh mode rejected"
            )

        main_sha = (
            self._fetch_main()
            if refresh_source
            else self.current_main_sha()
        )

        # Idle catch-up is permitted to skip the network fetch only for the
        # exact origin/main tip already synchronized by ExecStartPre.
        if not refresh_source and sha != main_sha:
            raise DeploymentError(
                "Minecraft local main SHA mismatch"
            )

        commit_type = self._run(
            ("cat-file", "-t", sha)
        )
        ancestor = self._run(
            (
                "merge-base",
                "--is-ancestor",
                sha,
                main_sha,
            )
        )
        parents = self._run(
            (
                "rev-list",
                "--parents",
                "-n",
                "1",
                sha,
            )
        )

        tokens = parents.stdout.strip().split()

        if (
            commit_type.returncode != 0
            or commit_type.stdout.strip()
            != "commit"
            or ancestor.returncode != 0
            or parents.returncode != 0
            or len(tokens) < 2
            or tokens[0] != sha
            or any(
                re.fullmatch(
                    r"[0-9a-f]{40}",
                    token,
                )
                is None
                for token in tokens
            )
        ):
            raise DeploymentError(
                "Minecraft source commit rejected"
            )

    def changed_files(self, sha: str):
        self._require_commit(sha)

        result = self._run(
            (
                "diff",
                "--name-only",
                "-z",
                sha + "^1",
                sha,
                "--",
            )
        )

        if result.returncode != 0:
            raise DeploymentError(
                process_failure("Minecraft merge diff failed", result)
            )

        records = result.stdout.split("\0")

        if records and records[-1] == "":
            records.pop()

        for path in records:
            if (
                not path
                or path.startswith("/")
                or any(
                    part in ("", ".", "..")
                    for part in path.split("/")
                )
            ):
                raise DeploymentError(
                    "Minecraft merge path rejected"
                )

        return records

    def pack_archive(
        self,
        sha: str,
        *,
        refresh_source: bool = True,
    ) -> bytes:
        self._require_commit(
            sha,
            refresh_source=refresh_source,
        )

        result = self._run(
            (
                "archive",
                "--format=tar",
                sha,
                "--",
                "minecraft/behavior_packs",
                "minecraft/resource_packs",
            ),
            binary=True,
        )

        if (
            result.returncode != 0
            or not isinstance(
                result.stdout,
                bytes,
            )
            or not result.stdout
        ):
            raise DeploymentError(
                process_failure("Minecraft source archive failed", result)
            )

        prepare_pack_archive(result.stdout)

        return result.stdout


def parse_proof(
    stdout: str,
    sha: str,
    tree_hash: str,
    *,
    stderr="",
) -> DeploymentResult:
    fields = proof_fields(stdout, {"MINECRAFT_DEPLOY_RESULT", "DEPLOYED_COMMIT_SHA",
                                   "MINECRAFT_TREE_SHA256"}, stderr=stderr)
    expected = {"MINECRAFT_DEPLOY_RESULT": "SUCCESS", "DEPLOYED_COMMIT_SHA": sha,
                "MINECRAFT_TREE_SHA256": tree_hash}
    for key, value in expected.items():
        if fields.get(key) != value:
            proof_error(f"Minecraft deployment {key} missing or mismatched", stdout, stderr)

    return DeploymentResult(
        sha,
        SUMMARY,
        frozenset({"minecraft"}),
    )


class ProductionMinecraftDeployAdapter:
    def __init__(
        self,
        config: MinecraftDeployConfig,
        source: ExactMergeSource,
    ):
        config.validate()
        self.config = config
        self.source = source

    def deploy(
        self,
        merge_sha: str,
        *,
        stop_event=None,
        refresh_source: bool = True,
    ) -> DeploymentResult:
        validate_sha(merge_sha)

        try:
            self.config.validate()

            if type(refresh_source) is not bool:
                raise DeploymentError(
                    "Minecraft deployment source mode rejected"
                )

            if (
                stop_event is not None
                and stop_event.is_set()
            ):
                raise DeploymentError(
                    "Minecraft deployment stopped"
                )

            archive = self.source.pack_archive(
                merge_sha, refresh_source=refresh_source,
            )

            _, tree_hash = prepare_pack_archive(
                archive
            )

            template_path = normal_file(
                TEMPLATE,
                (),
            )

            template = template_path.read_text(
                encoding="utf-8"
            )

            if template.count(PLACEHOLDER) != 1:
                raise DeploymentError(
                    "Minecraft deployment template rejected"
                )

            rendered = template.replace(
                PLACEHOLDER,
                base64.b64encode(
                    archive
                ).decode("ascii"),
            )

            c = self.config

            argv = [
                str(c.ssh_path),
                "-F",
                "none",
                "-T",
                "-o",
                "BatchMode=yes",
                "-o",
                "IdentitiesOnly=yes",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                'UserKnownHostsFile="'
                + c.known_hosts_path.as_posix()
                + '"',
                "-o",
                "ConnectTimeout=15",
                "-o",
                "ServerAliveInterval=15",
                "-o",
                "ServerAliveCountMax=3",
                "-o",
                "ForwardAgent=no",
                "-o",
                "ClearAllForwardings=yes",
                "-o",
                "PermitLocalCommand=no",
                "-i",
                str(c.ssh_key_path),
                c.ssh_user
                + "@"
                + c.ssh_host,
                shlex.join(["/usr/bin/python3", "-", merge_sha,
                            c.data_root, c.world_name, c.container]),
            ]

            # Keep complete logs independently of process timeout handling.
            with tempfile.TemporaryFile() as diagnostics, tempfile.TemporaryFile() as output:
                try:
                    process = subprocess.Popen(
                        argv, **managed_process_options(), shell=False,
                        stdin=subprocess.PIPE, stdout=output, stderr=diagnostics,
                    )
                    result = communicate_bounded(
                        process, input_text=rendered, timeout=c.timeout,
                        max_output_bytes=c.max_output_bytes, stop_event=None,
                    )
                except Exception as exc:
                    diagnostics.seek(0)
                    output.seek(0)
                    raise DeploymentError(exception_detail(exc) + "\n" +
                                          diagnostics.read().decode("utf-8", errors="replace") + "\nstdout:\n" +
                                          output.read().decode("utf-8", errors="replace")) from None
                diagnostics.seek(0)
                output.seek(0)
                result = replace(result, stderr=diagnostics.read().decode("utf-8", errors="replace") + result.stderr,
                                 stdout=output.read().decode("utf-8", errors="replace") + result.stdout)

            if (
                result.timed_out
                or result.stopped
                or result.stdin_cleanup_failed
                or result.returncode != 0
                or (
                    stop_event is not None
                    and stop_event.is_set()
                )
            ):
                detail = process_failure("Minecraft deployment transport failed", result)
                if stop_event is not None and stop_event.is_set():
                    detail += "; deployment lease lost"
                raise DeploymentError(detail)

            return parse_proof(result.stdout, merge_sha, tree_hash, stderr=result.stderr)

        except Exception as exc:
            raise DeploymentError(exception_detail(exc)) from None
