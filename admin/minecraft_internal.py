import hmac
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query, status
from pydantic import BaseModel

from bot import config
from bot.db import get_connection
from bot.repositories.minecraft_bridge import MinecraftBridgeRepository


router = APIRouter(prefix="/internal/minecraft", tags=["internal-minecraft"])


class MinecraftCommandResult(BaseModel):
    status: str
    reason: str = ""
    message: str = ""


def require_minecraft_bridge_secret(secret: Optional[str]) -> None:
    expected = config.MINECRAFT_BRIDGE_SECRET
    if not expected:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="minecraft bridge disabled")
    if secret is None or not hmac.compare_digest(secret, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")


@router.get("/commands/next")
async def next_minecraft_command(
    bot_id: str = Query(..., min_length=1, max_length=64),
    guild_id: str = Query(..., min_length=1, max_length=32),
    x_minecraft_bridge_secret: Optional[str] = Header(default=None),
):
    require_minecraft_bridge_secret(x_minecraft_bridge_secret)
    with get_connection() as connection:
        repository = MinecraftBridgeRepository(connection, bot_id=bot_id)
        repository.fail_expired()
        command = repository.claim_next_pending(bot_id=bot_id, guild_id=guild_id)
        connection.commit()
    if command is None:
        return {"command": None}
    return {
        "command": {
            "request_id": str(command["request_id"]),
            "type": command["command_type"],
            "minecraft_player": command["minecraft_player_name"],
        }
    }


@router.post("/commands/{request_id}/result")
async def post_minecraft_command_result(
    request_id: str,
    result: MinecraftCommandResult,
    x_minecraft_bridge_secret: Optional[str] = Header(default=None),
):
    require_minecraft_bridge_secret(x_minecraft_bridge_secret)
    status_value = result.status.strip().lower()
    if status_value not in ("succeeded", "failed"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid result status")
    with get_connection() as connection:
        repository = MinecraftBridgeRepository(connection)
        row = repository.mark_result(
            request_id=request_id,
            status=status_value,
            reason=result.reason,
            message=result.message,
        )
        connection.commit()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="command not found")
    return {"ok": True}
