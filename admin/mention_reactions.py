from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from admin.auth import get_current_user
from admin.servers import can_access_guild, find_server, role_allows
from bot.db import get_connection
from bot.repositories import MentionReactionRepository


router = APIRouter()

KIND_LABELS = {
    "random": "ランダム抽選",
    "random_draw": "ランダム抽選",
    "search": "検索",
}

MATCH_TYPE_LABELS = {
    "contains": "部分一致",
    "exact": "完全一致",
    "regex": "正規表現",
}


def register_mention_reaction_routes(templates: Jinja2Templates) -> None:
    @router.get("/guilds/{guild_id}/mention-reactions")
    async def mention_reactions_page(
        request: Request,
        guild_id: str,
        q: Optional[str] = Query(None),
        kind: str = Query("all"),
        system: str = Query("all"),
        enabled: str = Query("all"),
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)

        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

        server = find_server(guild_id, user["user_id"])
        filters = normalize_filters(q, kind, system, enabled)
        reactions = list_reaction_rows(guild_id, server["role"], filters)

        return templates.TemplateResponse(
            request,
            "mention_reactions.html",
            {
                "user": user,
                "server": server,
                "guild_id": guild_id,
                "filters": filters,
                "reactions": reactions,
                "can_create_random": role_allows(server["role"], "editor"),
            },
        )

    @router.post("/guilds/{guild_id}/mention-reactions/{reaction_id}/toggle")
    async def toggle_mention_reaction(
        request: Request,
        guild_id: str,
        reaction_id: int,
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)

        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

        server = find_server(guild_id, user["user_id"])
        with get_connection() as connection:
            repository = MentionReactionRepository(connection)
            reaction = repository.get_by_id(guild_id, reaction_id)
            if reaction is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="mention reaction not found")

            required_role = required_toggle_role(reaction)
            if not role_allows(server["role"], required_role):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="mention reaction toggle denied")

            repository.toggle_enabled(guild_id, reaction_id)
            connection.commit()

        return RedirectResponse(url="/guilds/{0}/mention-reactions".format(guild_id), status_code=303)

    @router.get("/guilds/{guild_id}/mention-reactions/new")
    async def new_mention_reaction_page(request: Request, guild_id: str):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)

        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

        server = find_server(guild_id, user["user_id"])
        if not role_allows(server["role"], "editor"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="mention reaction creation denied")

        return templates.TemplateResponse(
            request,
            "mention_reaction_placeholder.html",
            {
                "user": user,
                "server": server,
                "guild_id": guild_id,
                "title": "ランダム抽選を追加",
                "message": "ランダム抽選の作成フォームは次Phaseで実装予定です。検索型はシステム固定機能のため、管理画面から新規追加しません。",
            },
        )

    @router.get("/guilds/{guild_id}/mention-reactions/{reaction_id}")
    async def edit_mention_reaction_page(
        request: Request,
        guild_id: str,
        reaction_id: int,
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)

        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

        server = find_server(guild_id, user["user_id"])
        with get_connection() as connection:
            repository = MentionReactionRepository(connection)
            reaction = repository.get_by_id(guild_id, reaction_id)
            if reaction is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="mention reaction not found")

        return templates.TemplateResponse(
            request,
            "mention_reaction_placeholder.html",
            {
                "user": user,
                "server": server,
                "guild_id": guild_id,
                "title": "{0} の編集".format(reaction["name"]),
                "message": "メンション反応の編集画面は次Phaseで実装予定です。今回はDB上の一覧確認とON/OFF管理だけを追加しています。",
            },
        )


def normalize_filters(
    query: Optional[str],
    kind: str,
    system: str,
    enabled: str,
) -> Dict[str, Any]:
    normalized_query = (query or "").strip()
    normalized_kind = kind if kind in ("all", "random_draw", "search") else "all"
    normalized_system = system if system in ("all", "system", "custom") else "all"
    normalized_enabled = enabled if enabled in ("all", "true", "false") else "all"

    return {
        "q": normalized_query,
        "kind": normalized_kind,
        "system": normalized_system,
        "enabled": normalized_enabled,
    }


def list_reaction_rows(
    guild_id: str,
    role: str,
    filters: Dict[str, Any],
) -> List[Dict[str, Any]]:
    with get_connection() as connection:
        repository = MentionReactionRepository(connection)
        reactions = repository.list_reactions_for_admin(
            guild_id,
            query=filters["q"] or None,
            reaction_kind=None if filters["kind"] == "all" else filters["kind"],
            enabled=parse_bool_filter(filters["enabled"]),
            is_system=parse_system_filter(filters["system"]),
        )

    rows = []
    for reaction in reactions:
        row = dict(reaction)
        display_kind = display_reaction_kind(row["reaction_kind"])
        row["display_reaction_kind"] = display_kind
        row["reaction_kind_label"] = KIND_LABELS.get(display_kind, display_kind)
        row["match_type_label"] = MATCH_TYPE_LABELS.get(row["match_type"], row["match_type"])
        row["choice_count"] = int(row.get("choice_count") or 0)
        row["can_toggle"] = role_allows(role, required_toggle_role(row))
        row["edit_url"] = "/guilds/{0}/mention-reactions/{1}".format(guild_id, row["id"])
        row["toggle_url"] = "/guilds/{0}/mention-reactions/{1}/toggle".format(guild_id, row["id"])
        rows.append(row)

    return rows


def parse_bool_filter(value: str) -> Optional[bool]:
    if value == "true":
        return True
    if value == "false":
        return False
    return None


def parse_system_filter(value: str) -> Optional[bool]:
    if value == "system":
        return True
    if value == "custom":
        return False
    return None


def display_reaction_kind(reaction_kind: str) -> str:
    if reaction_kind == "random":
        return "random_draw"
    return reaction_kind


def required_toggle_role(reaction: Dict[str, Any]) -> str:
    if reaction.get("admin_only"):
        return "guild_admin"
    return "editor"
