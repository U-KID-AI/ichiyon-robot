import json
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Form, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from admin.auth import get_current_user
from admin.servers import can_access_guild, find_server, role_allows
from bot.db import get_connection
from bot.repositories import NgWordRepository, SpecialEffectRepository


router = APIRouter()

TARGET_TYPE = "ng_word"
EFFECT_TYPES = (
    "probability_message",
    "message",
    "reaction",
    "counter_delta",
    "counter_set",
    "probability_multiplier",
    "next_action_count",
    "mode_roll",
    "mode_enter",
    "temporary_state",
    "ng_behavior",
    "extra_choice",
)
DUPLICATE_ERROR = "同じワードが既に登録されています。"


def register_ng_word_routes(templates: Jinja2Templates) -> None:
    @router.get("/guilds/{guild_id}/ng-words")
    async def ng_words_page(
        request: Request,
        guild_id: str,
        q: Optional[str] = Query(None),
        enabled: str = Query("all"),
        has_effects: str = Query("all"),
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

        server = find_server(guild_id, user["user_id"])
        filters = normalize_filters(q, enabled, has_effects)
        words = list_word_rows(guild_id, server["role"], filters)
        return templates.TemplateResponse(
            request,
            "ng_words_db.html",
            {
                "user": user,
                "server": server,
                "guild_id": guild_id,
                "filters": filters,
                "words": words,
                "can_create": role_allows(server["role"], "editor"),
            },
        )

    @router.post("/guilds/{guild_id}/ng-words/{word_id}/toggle")
    async def toggle_ng_word(request: Request, guild_id: str, word_id: int):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"])
        if not role_allows(server["role"], "editor"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="ng word toggle denied")

        with get_connection() as connection:
            repository = NgWordRepository(connection)
            if repository.get_by_id(guild_id, word_id) is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ng word not found")
            repository.toggle_enabled(guild_id, word_id)
            connection.commit()

        return RedirectResponse(url="/guilds/{0}/ng-words".format(guild_id), status_code=303)

    @router.get("/guilds/{guild_id}/ng-words/new")
    async def new_ng_word_page(request: Request, guild_id: str):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"])
        if not role_allows(server["role"], "editor"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="ng word creation denied")

        return render_form(templates, request, server, guild_id, "new", default_form(), [], True)

    @router.post("/guilds/{guild_id}/ng-words/new")
    async def create_ng_word(
        request: Request,
        guild_id: str,
        word: str = Form(""),
        enabled: Optional[str] = Form(None),
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"])
        if not role_allows(server["role"], "editor"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="ng word creation denied")

        form, errors = build_form(word, enabled)
        with get_connection() as connection:
            repository = NgWordRepository(connection)
            if not errors and repository.word_exists(guild_id, form["word"]):
                errors.append(DUPLICATE_ERROR)
            if not errors:
                created = repository.create_word(guild_id, form["word"], form["enabled"])
                connection.commit()
                return RedirectResponse(
                    url="/guilds/{0}/ng-words/{1}".format(guild_id, created["id"]),
                    status_code=303,
                )

        return render_form(templates, request, server, guild_id, "new", form, errors, True, status_code=400)

    @router.get("/guilds/{guild_id}/ng-words/{word_id}")
    async def edit_ng_word_page(request: Request, guild_id: str, word_id: int):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"])
        with get_connection() as connection:
            repository = NgWordRepository(connection)
            row = repository.get_by_id(guild_id, word_id)
            if row is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ng word not found")
            form = build_word_view(connection, guild_id, row, server["role"])

        return render_form(
            templates,
            request,
            server,
            guild_id,
            "edit",
            form,
            [],
            role_allows(server["role"], "editor"),
            word_id=word_id,
        )

    @router.post("/guilds/{guild_id}/ng-words/{word_id}")
    async def update_ng_word(
        request: Request,
        guild_id: str,
        word_id: int,
        word: str = Form(""),
        enabled: Optional[str] = Form(None),
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"])
        if not role_allows(server["role"], "editor"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="ng word editing denied")

        form, errors = build_form(word, enabled)
        with get_connection() as connection:
            repository = NgWordRepository(connection)
            existing = repository.get_by_id(guild_id, word_id)
            if existing is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ng word not found")
            if not errors and repository.word_exists(guild_id, form["word"], word_id):
                errors.append(DUPLICATE_ERROR)
            if not errors:
                repository.update_word(guild_id, word_id, form["word"], form["enabled"])
                connection.commit()
                return RedirectResponse(url="/guilds/{0}/ng-words/{1}".format(guild_id, word_id), status_code=303)
            form["id"] = word_id
            form["effects"] = list_effects_for_target(connection, guild_id, word_id, server["role"])

        return render_form(templates, request, server, guild_id, "edit", form, errors, True, word_id=word_id, status_code=400)

    @router.get("/guilds/{guild_id}/ng-words/{word_id}/effects")
    async def ng_word_effects_page(
        request: Request,
        guild_id: str,
        word_id: int,
        q: Optional[str] = Query(None),
        effect_type: str = Query("all"),
        admin_only: str = Query("all"),
        include_disabled: str = Query("false"),
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"])
        filters = normalize_assignment_filters(q, effect_type, admin_only, include_disabled)
        with get_connection() as connection:
            repository = NgWordRepository(connection)
            word_row = repository.get_by_id(guild_id, word_id)
            if word_row is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ng word not found")
            tags = list_assignable_effect_rows(connection, guild_id, word_id, server["role"], filters)

        return templates.TemplateResponse(
            request,
            "ng_word_effects.html",
            {
                "user": user,
                "server": server,
                "guild_id": guild_id,
                "word": build_form_from_word(word_row),
                "filters": filters,
                "tags": tags,
                "effect_types": EFFECT_TYPES,
            },
        )

    @router.post("/guilds/{guild_id}/ng-words/{word_id}/effects")
    async def update_ng_word_effects(
        request: Request,
        guild_id: str,
        word_id: int,
        tag_id: int = Form(...),
        action: str = Form(...),
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"])
        with get_connection() as connection:
            word_repository = NgWordRepository(connection)
            if word_repository.get_by_id(guild_id, word_id) is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ng word not found")
            effect_repository = SpecialEffectRepository(connection)
            tag = effect_repository.get_by_id(guild_id, tag_id)
            if tag is None or tag.get("target_type") != TARGET_TYPE:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="special effect tag not found")
            if not can_manage_effect_assignment(server["role"], tag):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="special effect assignment denied")
            if action == "assign":
                effect_repository.assign_tag(guild_id, tag_id, TARGET_TYPE, word_id)
            elif action == "unassign":
                effect_repository.unassign_tag(guild_id, tag_id, TARGET_TYPE, word_id)
            else:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unknown assignment action")
            connection.commit()

        return RedirectResponse(url="/guilds/{0}/ng-words/{1}".format(guild_id, word_id), status_code=303)


def normalize_filters(q: Optional[str], enabled: str, has_effects: str) -> Dict[str, str]:
    return {
        "q": (q or "").strip(),
        "enabled": enabled if enabled in ("all", "true", "false") else "all",
        "has_effects": has_effects if has_effects in ("all", "true", "false") else "all",
    }


def parse_bool(value: str) -> Optional[bool]:
    if value == "true":
        return True
    if value == "false":
        return False
    return None


def list_word_rows(guild_id: str, role: str, filters: Dict[str, str]) -> List[Dict[str, Any]]:
    with get_connection() as connection:
        repository = NgWordRepository(connection)
        words = repository.list_words(guild_id, query=filters["q"] or None, enabled=parse_bool(filters["enabled"]))
        rows = [build_word_view(connection, guild_id, word, role) for word in words]

    if filters["has_effects"] == "true":
        rows = [row for row in rows if row["effects"]]
    elif filters["has_effects"] == "false":
        rows = [row for row in rows if not row["effects"]]
    return rows


def build_word_view(connection, guild_id: str, word: Dict[str, Any], role: str) -> Dict[str, Any]:
    row = build_form_from_word(word)
    row["effects"] = list_effects_for_target(connection, guild_id, int(row["id"]), role)
    row["edit_url"] = "/guilds/{0}/ng-words/{1}".format(guild_id, row["id"])
    row["toggle_url"] = "/guilds/{0}/ng-words/{1}/toggle".format(guild_id, row["id"])
    row["effects_url"] = "/guilds/{0}/ng-words/{1}/effects".format(guild_id, row["id"])
    return row


def default_form() -> Dict[str, Any]:
    return {"id": None, "word": "", "enabled": True, "effects": []}


def build_form_from_word(word: Dict[str, Any]) -> Dict[str, Any]:
    form = default_form()
    form.update({"id": word.get("id"), "word": word.get("word") or "", "enabled": bool(word.get("enabled"))})
    return form


def build_form(word: str, enabled: Optional[str]) -> Tuple[Dict[str, Any], List[str]]:
    form = default_form()
    form.update({"word": word.strip(), "enabled": enabled == "on"})
    errors = []
    if not form["word"]:
        errors.append("ワードを入力してください。")
    return form, errors


def render_form(
    templates: Jinja2Templates,
    request: Request,
    server: Dict[str, Any],
    guild_id: str,
    mode: str,
    form: Dict[str, Any],
    errors: List[str],
    can_edit: bool,
    word_id: Optional[int] = None,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        request,
        "ng_word_form.html",
        {
            "user": get_current_user(request),
            "server": server,
            "guild_id": guild_id,
            "mode": mode,
            "word_id": word_id,
            "word": form,
            "errors": errors,
            "can_edit": can_edit,
        },
        status_code=status_code,
    )


def can_see_effect_tag(role: str, tag: Dict[str, Any]) -> bool:
    if tag.get("admin_only"):
        return role_allows(role, "guild_admin")
    return True


def can_manage_effect_assignment(role: str, tag: Dict[str, Any]) -> bool:
    if tag.get("admin_only"):
        return role_allows(role, "guild_admin")
    return role_allows(role, "editor")


def list_effects_for_target(connection, guild_id: str, word_id: int, role: str) -> List[Dict[str, Any]]:
    repository = SpecialEffectRepository(connection)
    effects = repository.list_for_target(guild_id, TARGET_TYPE, word_id, enabled=None)
    return [
        build_effect_view(effect, role)
        for effect in effects
        if effect.get("assignment_enabled") and can_see_effect_tag(role, effect)
    ]


def build_effect_view(effect: Dict[str, Any], role: str) -> Dict[str, Any]:
    row = dict(effect)
    row["can_manage"] = can_manage_effect_assignment(role, effect)
    row["effect_config_summary"] = compact_json(effect.get("effect_config_json"))
    return row


def compact_json(value) -> str:
    if value is None:
        return "{}"
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return "{}"


def normalize_assignment_filters(
    query: Optional[str],
    effect_type: str,
    admin_only: str,
    include_disabled: str,
) -> Dict[str, Any]:
    return {
        "q": (query or "").strip(),
        "effect_type": effect_type if effect_type in EFFECT_TYPES or effect_type == "all" else "all",
        "admin_only": admin_only if admin_only in ("all", "true", "false") else "all",
        "include_disabled": include_disabled == "true",
    }


def list_assignable_effect_rows(
    connection,
    guild_id: str,
    word_id: int,
    role: str,
    filters: Dict[str, Any],
) -> List[Dict[str, Any]]:
    repository = SpecialEffectRepository(connection)
    tags = repository.list_tags(
        guild_id,
        query=filters["q"] or None,
        effect_type=None if filters["effect_type"] == "all" else filters["effect_type"],
        target_type=TARGET_TYPE,
        enabled=None if filters["include_disabled"] else True,
        admin_only=parse_bool(filters["admin_only"]),
    )
    assigned = {
        int(effect["id"]): bool(effect["assignment_enabled"])
        for effect in repository.list_for_target(guild_id, TARGET_TYPE, word_id, enabled=None)
    }
    rows = []
    for tag in tags:
        if not can_see_effect_tag(role, tag):
            continue
        row = build_effect_view(tag, role)
        row["assigned"] = assigned.get(int(tag["id"]), False)
        rows.append(row)
    return rows
