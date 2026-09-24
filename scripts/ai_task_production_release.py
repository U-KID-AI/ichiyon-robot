"""Operator entry point: existing exact-SHA app deploy, then managed Minecraft.

Reads only the installed runner's process environment; never loads a dotenv file.
This command performs production changes. Offline tests do not invoke main().
"""

import argparse
import os
from pathlib import Path
from uuid import UUID

from ai_task_api_client import RunnerAPIClient
from ai_task_deploy import ProductionDeployAdapter
from ai_task_deploy_config import DeployConfig, exception_detail
from ai_task_diagnostics import redact_secrets
from ai_task_minecraft_deploy import DeploymentResult, validate_sha, verify_deployment
from ai_task_minecraft_deploy_managed import ManagedMinecraftDeployAdapter
from ai_task_runner_config import validate_api_base_url, validate_runner_id


def release(sha, apps, minecraft, *, stop_event=None):
    validate_sha(sha)
    app = apps.deploy(sha, stop_event=stop_event)
    verify_deployment(app, sha, frozenset({"apps"}))
    result = minecraft.deploy(sha, stop_event=stop_event)
    verify_deployment(result, sha, frozenset({"minecraft"}))
    return DeploymentResult(sha, app.summary + " " + result.summary,
                            frozenset({"apps", "minecraft"}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--merge-sha", required=True)
    parser.add_argument("--attempt", type=UUID, help="new UUID to retry a terminal failed operation")
    args = parser.parse_args()
    try:
        validate_sha(args.merge_sha)
        base = validate_api_base_url(os.environ["AI_TASK_RUNNER_API_BASE_URL"])
        token = os.environ["AI_TASK_RUNNER_API_TOKEN"]
        runner_id = validate_runner_id(os.environ["AI_TASK_RUNNER_ID"])
        if not token:
            raise ValueError("runner API token missing")
        config = DeployConfig.from_environment(
            repo_root=Path(os.environ["AI_TASK_RUNNER_REPO_ROOT"]),
            worktree_root=Path(os.environ["AI_TASK_RUNNER_WORKTREE_ROOT"]),
        )
        client = RunnerAPIClient(base, token, runner_id, timeout=180)
        managed = ManagedMinecraftDeployAdapter(client, attempt=str(args.attempt) if args.attempt else None)
        result = release(args.merge_sha, ProductionDeployAdapter(config), managed)
        print(result.summary)
        return 0
    except Exception as exc:
        print(redact_secrets(exception_detail(exc), secrets=(os.environ.get("AI_TASK_RUNNER_API_TOKEN", ""),)))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
