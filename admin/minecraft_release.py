"""SHA-bound release entry point for the existing DB-managed pack pipeline."""

from pathlib import Path
import re
from typing import Optional
from uuid import UUID, NAMESPACE_URL, uuid5

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from admin.ai_tasks_internal import require_runner_token
from admin.minecraft_cosmetics import ROOT, records
from bot.db import get_connection
from bot.repositories.minecraft_cosmetics import MinecraftCosmeticsRepository
from bot.services.minecraft_cosmetics_pack import catalog_digest, pack_zip
from bot.services.minecraft_control import (
    MinecraftControlError, cosmetics_control, fetch_control_status,
)

router = APIRouter(prefix="/internal/minecraft-release", tags=["internal-minecraft-release"])
REVISION = Path(__file__).resolve().parent.parent / "REVISION"


class ReleaseRequest(BaseModel):
    # Omit for idempotent reconciliation. A new UUID explicitly retries a failed job.
    attempt: Optional[UUID] = None

    class Config:
        extra = "forbid"


def require_release(sha, authorization):
    require_runner_token(authorization)
    if re.fullmatch(r"[0-9a-f]{40}", sha) is None:
        raise HTTPException(422, "Invalid merge SHA")
    try:
        installed = REVISION.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        raise HTTPException(503, "Immutable app revision unavailable") from None
    if installed != sha:
        raise HTTPException(409, "Deploy the exact merge SHA app before Minecraft")


def catalog_snapshot():
    with get_connection() as connection:
        repository = MinecraftCosmeticsRepository(connection)
        assets = records(repository)
        connection.commit()
    return assets


def build_archive(assets):
    with get_connection() as connection:
        revision = MinecraftCosmeticsRepository(connection).revision()
        connection.commit()
    return pack_zip(ROOT, assets, revision)


def require_operation(operation, operation_id, digest=None):
    if not isinstance(operation, dict) or operation.get("operation_id") != operation_id:
        raise HTTPException(409, "Minecraft operation superseded or unavailable")
    if digest is not None and operation.get("catalog_digest") != digest:
        raise HTTPException(409, "Minecraft operation catalog mismatch")


@router.post("/{sha}", status_code=202)
async def release(sha: str, request: ReleaseRequest,
                  authorization: Optional[str] = Header(default=None)):
    require_release(sha, authorization)
    try:
        # Reach the production Control API before generating from this app's code.
        await fetch_control_status()
        assets = await run_in_threadpool(catalog_snapshot)
        digest = catalog_digest(assets)
        operation_id = str(uuid5(NAMESPACE_URL, f"ichiyon-minecraft:{sha}:{digest}:{request.attempt}"))
        previous = await cosmetics_control()
        if previous.get("operation_id") == operation_id:
            operation = previous
        else:
            archive = await run_in_threadpool(build_archive, assets)
            operation = await cosmetics_control(operation_id, archive)
        require_operation(operation, operation_id, digest)
        return {"merge_sha": sha, "expected_catalog_digest": digest, "operation": operation}
    except MinecraftControlError:
        raise HTTPException(503, "Minecraft Control API unavailable; inspect operation before retry") from None


@router.get("/{sha}/operations/{operation_id}")
async def operation_status(sha: str, operation_id: UUID,
                           authorization: Optional[str] = Header(default=None)):
    require_release(sha, authorization)
    try:
        operation = await cosmetics_control()
        require_operation(operation, str(operation_id))
        runtime = None
        current_digest = None
        if operation.get("status") == "succeeded":
            runtime = await fetch_control_status()
            # Do not combine a superseded operation with another release's health.
            operation = await cosmetics_control()
            require_operation(operation, str(operation_id))
            current_digest = catalog_digest(await run_in_threadpool(catalog_snapshot))
        return {"merge_sha": sha, "operation": operation, "runtime": runtime,
                "current_catalog_digest": current_digest}
    except MinecraftControlError:
        raise HTTPException(503, "Minecraft Control API unavailable; no completion proof") from None
