from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Form, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from admin.auth import get_current_user
from admin.servers import can_access_guild, find_server, role_allows
from bot.db import connect, get_connection
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
    "prefix": "前方一致",
    "regex": "正規表現",
}

REACTION_MATCH_TYPES = ("exact", "prefix", "regex")
KEYWORD_DUPLICATE_ERROR = "このキーワードは既存のメンション反応で使用されています。"


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
            "mention_reaction_form.html",
            {
                "user": user,
                "server": server,
                "guild_id": guild_id,
                "mode": "new",
                "reaction": default_reaction_form(),
                "choices": [],
                "errors": [],
                "can_edit_reaction": True,
                "can_set_admin_only": role_allows(server["role"], "guild_admin"),
                "can_edit_choices": False,
                "search_readonly": False,
                "match_types": build_match_type_options(),
            },
        )

    @router.post("/guilds/{guild_id}/mention-reactions/new")
    async def create_mention_reaction(
        request: Request,
        guild_id: str,
        name: str = Form(""),
        description: str = Form(""),
        keyword: str = Form(""),
        match_type: str = Form("exact"),
        enabled: Optional[str] = Form(None),
        admin_only: Optional[str] = Form(None),
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)

        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

        server = find_server(guild_id, user["user_id"])
        if not role_allows(server["role"], "editor"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="mention reaction creation denied")

        form = build_reaction_form(name, description, keyword, match_type, enabled, admin_only)
        errors = validate_reaction_form(form)
        if form["admin_only"] and not role_allows(server["role"], "guild_admin"):
            errors.append("管理者限定の反応は guild_admin 以上だけが作成できます。")
        with get_connection() as connection:
            repository = MentionReactionRepository(connection)
            if not errors and repository.keyword_exists(guild_id, form["keyword"]):
                errors.append(KEYWORD_DUPLICATE_ERROR)

            if not errors:
                reaction = repository.create_reaction(
                    guild_id,
                    "custom_{0}".format(uuid4().hex[:12]),
                    form["keyword"],
                    form["match_type"],
                    "random_draw",
                    form["name"],
                    form["description"],
                    form["admin_only"],
                    False,
                    True,
                    form["enabled"],
                )
                connection.commit()
                return RedirectResponse(
                    url="/guilds/{0}/mention-reactions/{1}".format(guild_id, reaction["id"]),
                    status_code=303,
                )

        return templates.TemplateResponse(
            request,
            "mention_reaction_form.html",
            {
                "user": user,
                "server": server,
                "guild_id": guild_id,
                "mode": "new",
                "reaction": form,
                "choices": [],
                "errors": errors,
                "can_edit_reaction": True,
                "can_set_admin_only": role_allows(server["role"], "guild_admin"),
                "can_edit_choices": False,
                "search_readonly": False,
                "match_types": build_match_type_options(),
            },
            status_code=400,
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
            choices = repository.list_choices(guild_id, reaction_id)

        return templates.TemplateResponse(
            request,
            "mention_reaction_form.html",
            {
                "user": user,
                "server": server,
                "guild_id": guild_id,
                "mode": "edit",
                "reaction": build_reaction_view(reaction),
                "choices": choices,
                "errors": [],
                "choice_errors": [],
                "can_edit_reaction": can_edit_reaction(server["role"], reaction),
                "can_set_admin_only": role_allows(server["role"], "guild_admin"),
                "can_edit_choices": can_edit_reaction(server["role"], reaction) and reaction["reaction_kind"] == "random",
                "search_readonly": reaction["reaction_kind"] == "search",
                "match_types": build_match_type_options(),
            },
        )

    @router.post("/guilds/{guild_id}/mention-reactions/{reaction_id}")
    async def update_mention_reaction(
        request: Request,
        guild_id: str,
        reaction_id: int,
        name: str = Form(""),
        description: str = Form(""),
        keyword: str = Form(""),
        match_type: str = Form("exact"),
        enabled: Optional[str] = Form(None),
        admin_only: Optional[str] = Form(None),
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
            if reaction["reaction_kind"] == "search":
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="search reaction editing denied")
            if not can_edit_reaction(server["role"], reaction):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="mention reaction editing denied")

            form = build_reaction_form(name, description, keyword, match_type, enabled, admin_only)
            errors = validate_reaction_form(form)
            if form["admin_only"] != bool(reaction["admin_only"]) and not role_allows(server["role"], "guild_admin"):
                errors.append("管理者限定の変更は guild_admin 以上だけが実行できます。")
            if not errors and repository.keyword_exists(guild_id, form["keyword"], reaction_id):
                errors.append(KEYWORD_DUPLICATE_ERROR)

            if not errors:
                repository.update_reaction(
                    guild_id,
                    reaction_id,
                    form["keyword"],
                    form["match_type"],
                    form["name"],
                    form["description"],
                    form["admin_only"],
                    form["enabled"],
                )
                connection.commit()
                return RedirectResponse(
                    url="/guilds/{0}/mention-reactions/{1}".format(guild_id, reaction_id),
                    status_code=303,
                )

            choices = repository.list_choices(guild_id, reaction_id)

        form.update(
            {
                "id": reaction_id,
                "reaction_kind": reaction["reaction_kind"],
                "is_system": reaction["is_system"],
                "is_deletable": reaction["is_deletable"],
            }
        )
        return templates.TemplateResponse(
            request,
            "mention_reaction_form.html",
            {
                "user": user,
                "server": server,
                "guild_id": guild_id,
                "mode": "edit",
                "reaction": form,
                "choices": choices,
                "errors": errors,
                "choice_errors": [],
                "can_edit_reaction": True,
                "can_set_admin_only": role_allows(server["role"], "guild_admin"),
                "can_edit_choices": True,
                "search_readonly": False,
                "match_types": build_match_type_options(),
            },
            status_code=400,
        )

    @router.post("/guilds/{guild_id}/mention-reactions/{reaction_id}/choices")
    async def create_mention_reaction_choice(
        request: Request,
        guild_id: str,
        reaction_id: int,
        choice_name: str = Form(""),
        body: str = Form(""),
        image_path: str = Form(""),
        appearance_rate: str = Form("1"),
        choice_enabled: Optional[str] = Form(None),
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)

        server, repository, reaction, connection = prepare_choice_mutation(guild_id, reaction_id, user["user_id"])
        try:
            choice_form = build_choice_form(choice_name, body, image_path, appearance_rate, choice_enabled)
            errors = validate_choice_form(choice_form)
            if errors:
                choices = repository.list_choices(guild_id, reaction_id)
                return render_reaction_form_with_choice_errors(
                    templates,
                    request,
                    server,
                    guild_id,
                    reaction,
                    choices,
                    errors,
                    status_code=400,
                )

            repository.create_choice(
                guild_id,
                reaction_id,
                choice_form["name"],
                choice_form["body"] or None,
                choice_form["image_path"] or None,
                choice_form["appearance_rate"],
                choice_form["enabled"],
            )
            connection.commit()
        finally:
            connection.close()

        return RedirectResponse(
            url="/guilds/{0}/mention-reactions/{1}".format(guild_id, reaction_id),
            status_code=303,
        )

    @router.post("/guilds/{guild_id}/mention-reactions/{reaction_id}/choices/{choice_id}")
    async def update_mention_reaction_choice(
        request: Request,
        guild_id: str,
        reaction_id: int,
        choice_id: int,
        choice_name: str = Form(""),
        body: str = Form(""),
        image_path: str = Form(""),
        appearance_rate: str = Form("1"),
        choice_enabled: Optional[str] = Form(None),
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)

        server, repository, reaction, connection = prepare_choice_mutation(guild_id, reaction_id, user["user_id"])
        try:
            choice = repository.get_choice(guild_id, choice_id)
            if choice is None or int(choice["mention_reaction_id"]) != reaction_id:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="mention reaction choice not found")

            choice_form = build_choice_form(choice_name, body, image_path, appearance_rate, choice_enabled)
            errors = validate_choice_form(choice_form)
            if errors:
                choices = repository.list_choices(guild_id, reaction_id)
                return render_reaction_form_with_choice_errors(
                    templates,
                    request,
                    server,
                    guild_id,
                    reaction,
                    choices,
                    errors,
                    status_code=400,
                )

            repository.update_choice(
                guild_id,
                choice_id,
                choice_form["name"],
                choice_form["body"] or None,
                choice_form["image_path"] or None,
                choice_form["appearance_rate"],
                choice_form["enabled"],
            )
            connection.commit()
        finally:
            connection.close()

        return RedirectResponse(
            url="/guilds/{0}/mention-reactions/{1}".format(guild_id, reaction_id),
            status_code=303,
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


def can_edit_reaction(role: str, reaction: Dict[str, Any]) -> bool:
    if reaction.get("reaction_kind") == "search":
        return False
    if reaction.get("admin_only") or reaction.get("is_system"):
        return role_allows(role, "guild_admin")
    return role_allows(role, "editor")


def build_match_type_options() -> List[Dict[str, str]]:
    return [
        {"value": "exact", "label": "完全一致"},
        {"value": "prefix", "label": "前方一致"},
        {"value": "regex", "label": "正規表現"},
    ]


def default_reaction_form() -> Dict[str, Any]:
    return {
        "id": None,
        "reaction_kind": "random",
        "name": "",
        "description": "",
        "keyword": "",
        "match_type": "exact",
        "enabled": True,
        "admin_only": False,
        "is_system": False,
        "is_deletable": True,
    }


def build_reaction_view(reaction: Dict[str, Any]) -> Dict[str, Any]:
    row = dict(reaction)
    row["display_reaction_kind"] = display_reaction_kind(row["reaction_kind"])
    row["reaction_kind_label"] = KIND_LABELS.get(row["display_reaction_kind"], row["display_reaction_kind"])
    row["match_type_label"] = MATCH_TYPE_LABELS.get(row["match_type"], row["match_type"])
    return row


def build_reaction_form(
    name: str,
    description: str,
    keyword: str,
    match_type: str,
    enabled: Optional[str],
    admin_only: Optional[str],
) -> Dict[str, Any]:
    form = default_reaction_form()
    form.update(
        {
            "name": name.strip(),
            "description": description.strip(),
            "keyword": keyword.strip(),
            "match_type": match_type if match_type in REACTION_MATCH_TYPES else "exact",
            "enabled": enabled == "on",
            "admin_only": admin_only == "on",
        }
    )
    return form


def validate_reaction_form(form: Dict[str, Any]) -> List[str]:
    errors = []
    if not form["name"]:
        errors.append("反応名を入力してください。")
    if not form["keyword"]:
        errors.append("キーワード/パターンを入力してください。")
    if form["match_type"] not in REACTION_MATCH_TYPES:
        errors.append("一致方式を選択してください。")
    return errors


def build_choice_form(
    name: str,
    body: str,
    image_path: str,
    appearance_rate: str,
    enabled: Optional[str],
) -> Dict[str, Any]:
    try:
        parsed_rate = int(appearance_rate)
    except ValueError:
        parsed_rate = 0

    return {
        "name": name.strip(),
        "body": body.strip(),
        "image_path": image_path.strip(),
        "appearance_rate": parsed_rate,
        "enabled": enabled == "on",
    }


def validate_choice_form(form: Dict[str, Any]) -> List[str]:
    errors = []
    if not form["name"]:
        errors.append("候補名を入力してください。")
    if not form["body"] and not form["image_path"]:
        errors.append("本文と画像パスのどちらかは入力してください。")
    if form["appearance_rate"] < 1:
        errors.append("出やすさは1以上の整数で入力してください。")
    return errors


def prepare_choice_mutation(guild_id: str, reaction_id: int, discord_user_id: str):
    if not can_access_guild(guild_id, discord_user_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

    server = find_server(guild_id, discord_user_id)
    connection = connect()
    repository = MentionReactionRepository(connection)
    reaction = repository.get_by_id(guild_id, reaction_id)
    if reaction is None:
        connection.close()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="mention reaction not found")
    if not can_edit_reaction(server["role"], reaction):
        connection.close()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="mention reaction choice editing denied")
    if reaction["reaction_kind"] != "random":
        connection.close()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="search reaction choice editing denied")

    return server, repository, reaction, connection


def render_reaction_form_with_choice_errors(
    templates: Jinja2Templates,
    request: Request,
    server: Dict[str, Any],
    guild_id: str,
    reaction: Dict[str, Any],
    choices: List[Dict[str, Any]],
    errors: List[str],
    status_code: int = 400,
):
    return templates.TemplateResponse(
        request,
        "mention_reaction_form.html",
        {
            "user": get_current_user(request),
            "server": server,
            "guild_id": guild_id,
            "mode": "edit",
            "reaction": build_reaction_view(reaction),
            "choices": choices,
            "errors": [],
            "choice_errors": errors,
            "can_edit_reaction": True,
            "can_set_admin_only": role_allows(server["role"], "guild_admin"),
            "can_edit_choices": True,
            "search_readonly": False,
            "match_types": build_match_type_options(),
        },
        status_code=status_code,
    )
