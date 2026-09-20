import re
import uuid
from typing import Any, Dict, Optional, Tuple

import discord

from bot import config
from bot.db import get_connection
from bot.repositories.ai_tasks import AITaskRepository


MAX_DISCORD_DESCRIPTION_LENGTH = 1800
MAX_DISCORD_RESPONSE_LENGTH = 1900
AI_USAGE = (
    "使い方: @いちよんロボ AI 開発 <依頼内容> / "
    "AI 状態 <task_id> / AI 一覧"
)
AI_UNAUTHORIZED = "AI開発taskを利用する権限がありません。"
AI_DB_ERROR = "AI taskを処理できませんでした。"
AI_DB_REQUIRED = "AI task機能はDB構成時のみ利用できます。"
AI_COMMAND_RE = re.compile(r"^AI(?:[\s\u3000]+|$)")
TERMINAL_STATUSES = frozenset(("completed", "failed", "needs_human", "cancelled"))


def is_ai_task_channel(message: discord.Message) -> bool:
    # Location only: redact this channel even in another bot instance.
    return (
        getattr(getattr(message, "guild", None), "id", None) == config.AI_TASK_DISCORD_GUILD_ID
        and getattr(getattr(message, "channel", None), "id", None) == config.AI_TASK_DISCORD_CHANNEL_ID
    )


async def handle_ai_task_channel_message(
    message: discord.Message, command_text: Optional[str] = None,
) -> bool:
    if not is_ai_task_channel(message) or config.BOT_INSTANCE_ID != "ichiyon":
        return False
    if getattr(message.author, "bot", False):
        return False
    if not is_ai_task_allowed(message.author.id):
        await _send_ai_response(message, AI_UNAUTHORIZED)
        return True
    if config.DATA_BACKEND != "db":
        await _send_ai_response(message, AI_DB_REQUIRED)
        return True
    # The mention parser supplies text with only the leading Ichiyon mention removed.
    text = message.content if command_text is None else command_text
    action, _argument, _owned = parse_ai_command(text)
    if action is not None:
        return await handle_ai_task_command(message, text)
    return await _handle_create(message, text)


def parse_ai_command(command_text: Optional[str]) -> Tuple[Optional[str], Optional[str], bool]:
    text = str(command_text or "").strip()
    if AI_COMMAND_RE.match(text) is None:
        return None, None, False
    remainder = text[2:].strip(" \t\u3000")
    if not remainder:
        return None, None, True

    parts = re.split(r"[\s\u3000]+", remainder, maxsplit=1)
    action = parts[0]
    argument = parts[1] if len(parts) == 2 else ""
    if action == "開発":
        return action, argument.strip(), True
    if action == "状態":
        return action, argument.strip(), True
    if action == "一覧":
        return action, argument.strip(), True
    return None, None, True


def is_ai_task_allowed(user_id: object) -> bool:
    normalized_user_id = str(user_id or "").strip()
    return bool(
        normalized_user_id
        and normalized_user_id in config.AI_TASK_ALLOWED_USER_IDS
    )


def parse_task_id(value: str) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(str(value).strip())
    except (AttributeError, ValueError, TypeError):
        return None


def build_task_names(task_id: uuid.UUID) -> Tuple[str, str]:
    task_text = str(task_id)
    return "ai/task/{0}".format(task_text), "ai-task-{0}".format(task_text)


def _safe_text(value: Any) -> str:
    return str(value).replace("@everyone", "@\u200beveryone").replace("@here", "@\u200bhere")


def _format_datetime(value: Any) -> str:
    return _safe_text(value.isoformat() if hasattr(value, "isoformat") else value)


def _response(text: str) -> str:
    return text[:MAX_DISCORD_RESPONSE_LENGTH]


async def _send_ai_response(message: discord.Message, text: str) -> None:
    await message.channel.send(
        _response(text),
        allowed_mentions=discord.AllowedMentions.none(),
    )


def format_task_created(row: Dict[str, Any]) -> str:
    return _response(
        "AI taskを受け付けました。\n"
        "task ID: {0}\n"
        "status: {1}\n"
        "branch: {2}\n"
        "worktree: {3}".format(
            _safe_text(row.get("task_id")),
            _safe_text(row.get("status")),
            _safe_text(row.get("branch_name")),
            _safe_text(row.get("worktree_name")),
        )
    )


def format_task_status(row: Dict[str, Any]) -> str:
    labels = (
        ("task ID", row.get("task_id")),
        ("status", row.get("status")),
        ("requester", row.get("requester_discord_user_id")),
        ("created_at", row.get("created_at")),
        ("updated_at", row.get("updated_at")),
        ("branch", row.get("branch_name")),
        ("worktree", row.get("worktree_name")),
        ("current_step", row.get("current_step")),
        ("progress", row.get("progress_summary")),
        ("result", row.get("result_summary")),
        ("error", row.get("error_message")),
    )
    lines = ["AI task状態"]
    for label, value in labels:
        if value is None or str(value).strip() == "":
            continue
        formatted = _format_datetime(value) if label.endswith("_at") else _safe_text(value)
        lines.append("{0}: {1}".format(label, formatted))
    return _response("\n".join(lines))


def format_task_list(rows: list[Dict[str, Any]]) -> str:
    if not rows:
        return "AI taskはありません。"
    lines = ["AI task一覧"]
    for index, row in enumerate(rows[:20]):
        line = "{0} | {1} | requester={2} | created_at={3} | branch={4}".format(
            _safe_text(row.get("task_id")),
            _safe_text(row.get("status")),
            _safe_text(row.get("requester_discord_user_id")),
            _format_datetime(row.get("created_at")),
            _safe_text(row.get("branch_name")),
        )
        candidate = "\n".join(lines + [line])
        if len(candidate) > MAX_DISCORD_RESPONSE_LENGTH:
            remaining = len(rows[:20]) - index
            suffix = "...他{0}件".format(remaining)
            if len("\n".join(lines + [suffix])) <= MAX_DISCORD_RESPONSE_LENGTH:
                lines.append(suffix)
            break
        lines.append(line)
    return "\n".join(lines)


async def handle_ai_task_command(
    message: discord.Message,
    command_text: Optional[str],
) -> bool:
    if not is_ai_task_channel(message) or config.BOT_INSTANCE_ID != "ichiyon":
        return False
    action, argument, owned = parse_ai_command(command_text)
    if not owned:
        return False
    if not is_ai_task_allowed(getattr(getattr(message, "author", None), "id", None)):
        await _send_ai_response(message, AI_UNAUTHORIZED)
        return True
    if action is None:
        await _send_ai_response(message, AI_USAGE)
        return True
    if config.DATA_BACKEND != "db":
        await _send_ai_response(message, AI_DB_REQUIRED)
        return True
    if action == "開発":
        return await _handle_create(message, argument or "")
    if action == "状態":
        return await _handle_status(message, argument or "")
    if action == "一覧":
        if argument:
            await _send_ai_response(message, AI_USAGE)
            return True
        return await _handle_list(message)
    await _send_ai_response(message, AI_USAGE)
    return True


async def _handle_create(message: discord.Message, description: str) -> bool:
    if not description.strip() or len(description) > MAX_DISCORD_DESCRIPTION_LENGTH:
        await _send_ai_response(
            message,
            "依頼内容は1文字以上1800文字以内で入力してください。"
        )
        return True

    task_id = uuid.uuid4()
    branch_name, worktree_name = build_task_names(task_id)
    guild = getattr(message, "guild", None)
    guild_id = str(getattr(guild, "id", "") or "") or None
    try:
        with get_connection() as connection:
            row = AITaskRepository(connection).create_task(
                task_id=task_id,
                guild_id=guild_id,
                discord_channel_id=str(getattr(message.channel, "id", "") or ""),
                discord_message_id=str(getattr(message, "id", "") or ""),
                requester_discord_user_id=str(getattr(message.author, "id", "") or ""),
                description=description,
                branch_name=branch_name,
                worktree_name=worktree_name,
            )
            connection.commit()
    except Exception as exc:
        print("[WARN] AI task create failed: error={0}".format(type(exc).__name__))
        await _send_ai_response(message, AI_DB_ERROR)
        return True

    await _send_ai_response(message, format_task_created(row))
    return True


async def _handle_status(message: discord.Message, argument: str) -> bool:
    task_id = parse_task_id(argument)
    if task_id is None:
        await _send_ai_response(message, "task IDが不正です。UUIDを指定してください。")
        return True
    try:
        with get_connection() as connection:
            row = AITaskRepository(connection).get_task(task_id)
    except Exception as exc:
        print("[WARN] AI task status failed: error={0}".format(type(exc).__name__))
        await _send_ai_response(message, AI_DB_ERROR)
        return True
    if row is None:
        await _send_ai_response(message, "指定されたAI taskが見つかりません。")
        return True
    await _send_ai_response(message, format_task_status(row))
    return True


async def _handle_list(message: discord.Message) -> bool:
    try:
        with get_connection() as connection:
            rows = AITaskRepository(connection).list_tasks(limit=20)
    except Exception as exc:
        print("[WARN] AI task list failed: error={0}".format(type(exc).__name__))
        await _send_ai_response(message, AI_DB_ERROR)
        return True
    await _send_ai_response(message, format_task_list(rows))
    return True


def format_task_terminal(row: Dict[str, Any]) -> str:
    if row.get("status") not in TERMINAL_STATUSES:
        raise ValueError("terminal status required")
    # Only explicit result fields; never include description or transport settings.
    fields = (
        ("task ID", "task_id"), ("status", "status"), ("PR", "pr_url"),
        ("deployed SHA", "deployed_commit_sha"), ("result", "result_summary"),
        ("progress", "progress_summary"), ("error", "error_message"),
        ("deployment", "deployment_summary"),
    )
    lines = ["AI task結果"]
    for label, key in fields:
        if row.get(key):
            lines.append("{0}: {1}".format(label, _safe_text(row[key])[:250]))
    return _response("\n".join(lines))


async def notify_ai_task_terminal_updates_once(bot) -> None:
    if config.BOT_INSTANCE_ID != "ichiyon" or config.DATA_BACKEND != "db":
        return
    channel = bot.get_channel(config.AI_TASK_DISCORD_CHANNEL_ID)
    if (
        channel is None
        or getattr(channel, "id", None) != config.AI_TASK_DISCORD_CHANNEL_ID
        or getattr(getattr(channel, "guild", None), "id", None) != config.AI_TASK_DISCORD_GUILD_ID
    ):
        return
    with get_connection() as connection:
        repository = AITaskRepository(connection)
        # Row locks prevent concurrent notifier processes sending the same batch.
        rows = repository.list_unnotified_terminal_tasks(
            guild_id=str(config.AI_TASK_DISCORD_GUILD_ID),
            discord_channel_id=str(config.AI_TASK_DISCORD_CHANNEL_ID), limit=10,
        )
        for row in rows:
            try:
                sent = await channel.send(
                    format_task_terminal(row), allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception as exc:
                print("[WARN] AI terminal notification send failed: " + type(exc).__name__)
                continue
            marked = repository.mark_terminal_notified(
                task_id=row["task_id"], status=row["status"],
                message_id=str(sent.id) if getattr(sent, "id", None) is not None else None,
            )
            if marked is None:
                raise RuntimeError("terminal notification state changed")
        connection.commit()
