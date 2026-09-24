from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx

from bot import config
from bot.services.minecraft_resource_packs import MAX_ARCHIVE


class MinecraftControlError(Exception):
    pass


def control_api_configured() -> bool:
    return bool(config.MINECRAFT_CONTROL_API_BASE and config.MINECRAFT_CONTROL_API_SECRET)


def _headers() -> Dict[str, str]:
    return {"X-Minecraft-Control-Secret": config.MINECRAFT_CONTROL_API_SECRET}


def _base_url() -> str:
    return config.MINECRAFT_CONTROL_API_BASE.rstrip("/")


async def fetch_control_status() -> Dict[str, Any]:
    if not control_api_configured():
        raise MinecraftControlError("control_api_not_configured")
    try:
        async with httpx.AsyncClient(timeout=config.MINECRAFT_CONTROL_TIMEOUT_SECONDS, trust_env=False) as client:
            response = await client.get(_base_url() + "/status", headers=_headers())
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise MinecraftControlError(type(exc).__name__) from exc


async def request_control_restart() -> Dict[str, Any]:
    if not control_api_configured():
        raise MinecraftControlError("control_api_not_configured")
    try:
        async with httpx.AsyncClient(timeout=config.MINECRAFT_RESTART_TIMEOUT_SECONDS, trust_env=False) as client:
            response = await client.post(_base_url() + "/restart", headers=_headers())
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise MinecraftControlError(type(exc).__name__) from exc


async def cosmetics_control(operation_id=None, archive=None):
    """Fixed private API; credential and URL never come from form input."""
    if not control_api_configured():
        raise MinecraftControlError('反映先に接続できません。')
    if operation_id is not None and (not isinstance(archive, bytes) or len(archive) > MAX_ARCHIVE):
        raise MinecraftControlError('Managed archive exceeds the 192 MiB limit or is invalid.')
    try:
        timeout = 60 if operation_id is None else httpx.Timeout(180, connect=10)
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            if operation_id is None:
                response = await client.get(_base_url() + '/cosmetics', headers=_headers())
            else:
                import uuid
                operation_id = str(uuid.UUID(operation_id))
                response = await client.post(_base_url() + '/cosmetics/' + operation_id, content=archive,
                                             headers={**_headers(), 'Content-Type': 'application/zip'})
            if response.status_code == 409:
                raise MinecraftControlError('別の反映処理が実行中です。完了するまでお待ちください。')
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, ValueError):
        raise MinecraftControlError('反映先との通信を確認できません。処理状況を確認してから再試行してください。') from None


def format_control_status(payload: Dict[str, Any]) -> str:
    container = payload.get("container") or {}
    host = payload.get("host") or {}
    bridge = payload.get("bridge") or {}
    bds = payload.get("bds") or {}
    status_text = str(payload.get("server_status") or "UNKNOWN")
    player_names = [str(name) for name in bridge.get("player_names") or []]
    lines = [
        "Minecraft Server: {0}".format(status_text),
        "Docker: {0}".format(_display(container.get("state"))),
        "Health: {0}".format(_display(container.get("health"))),
        "Restart Count: {0}".format(_display(container.get("restart_count"))),
        "Container Started: {0}".format(_format_timestamp(container.get("started_at"))),
        "BDS Uptime: {0}".format(_format_duration(container.get("uptime_seconds"))),
        "BDS: {0}".format(_display(bds.get("version"))),
        "Bridge: {0}".format("OK" if bridge.get("responding") else "応答なし"),
        "Players: {0}".format(_display(bridge.get("player_count"))),
    ]
    if player_names:
        lines.extend("- {0}".format(name) for name in player_names)
    else:
        lines.append("- なし")
    lines.extend(
        [
            "Host CPU: {0}".format(_display(host.get("cpu_percent"))),
            "Host Memory: {0}".format(_display(host.get("memory"))),
            "Container CPU: {0}".format(_display(container.get("cpu_percent"))),
            "Container Memory: {0}".format(_display(container.get("memory"))),
        ]
    )
    return "\n".join(lines)[:1900]


def format_restart_result(payload: Dict[str, Any]) -> str:
    status_text = str(payload.get("server_status") or "UNKNOWN")
    backup = str(payload.get("backup_file") or "")
    pack_sync = payload.get("pack_sync") or {}
    changed = pack_sync.get("changed_packs") or []
    lines = [
        "Minecraftサーバーを再起動しました。",
        "Minecraft Server: {0}".format(status_text),
        "Docker: {0}".format(_display((payload.get("container") or {}).get("state"))),
        "Health: {0}".format(_display((payload.get("container") or {}).get("health"))),
        "Backup: {0}".format(backup if backup else "未作成"),
        "Pack Sync: {0}".format(_display(pack_sync.get("status"))),
    ]
    if changed:
        lines.append("Changed Packs: {0}".format(", ".join(str(name) for name in changed)))
    return "\n".join(lines)[:1900]


def _display(value: Any) -> str:
    if value is None or value == "":
        return "取得不可"
    return str(value)


def _format_timestamp(value: Any) -> str:
    if not value:
        return "取得不可"
    text = str(value)
    try:
        normalized = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except ValueError:
        return text


def _format_duration(value: Any) -> str:
    try:
        seconds = int(float(value))
    except (TypeError, ValueError):
        return "取得不可"
    seconds = max(0, seconds)
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    if days:
        return "{0}d {1}h {2}m".format(days, hours, minutes)
    if hours:
        return "{0}h {1}m {2}s".format(hours, minutes, seconds)
    if minutes:
        return "{0}m {1}s".format(minutes, seconds)
    return "{0}s".format(seconds)
