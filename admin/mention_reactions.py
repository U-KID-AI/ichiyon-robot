import json
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from admin.auth import get_current_user
from admin.bot_context import current_selected_bot_id, selected_bot_id
from admin.servers import can_access_guild, find_server, role_allows
from admin.ux import (
    ADDITIONAL_POST_TIMING_LABELS,
    COOLDOWN_SCOPE_LABELS,
    EFFECT_TYPE_LABELS,
    EXPIRES_TYPE_LABELS,
    MATCH_TYPE_LABELS as UX_MATCH_TYPE_LABELS,
    REACTION_KIND_LABELS,
    TARGET_TYPE_LABELS,
    is_test_data,
    parse_show_test_data,
    save_uploaded_image,
)
from bot import config as bot_config
from bot.db import connect, get_connection
from bot.repositories import DeckSearchSettingsRepository, MentionReactionRepository, SpecialEffectRepository
from bot.services.deck_search import DEFAULT_EXCLUDED_KEYWORDS, DEFAULT_REQUIRED_CONTEXT_TERMS, DEFAULT_X_QUERY_TEMPLATE
from bot.services.deck_search_settings import (
    DEFAULT_MAX_LOOKBACK_DAYS,
    parse_fetch_since_date,
    settings_fetch_since_date,
    settings_max_lookback_days,
    validate_fetch_since_date,
)


router = APIRouter()

KIND_LABELS = {
    "random": "ランダム抽選",
    "random_draw": "ランダム抽選",
    "search": "検索",
}
KIND_LABELS.update(REACTION_KIND_LABELS)

MATCH_TYPE_LABELS = {
    "contains": "部分一致",
    "exact": "完全一致",
    "prefix": "前方一致",
    "regex": "正規表現",
}
MATCH_TYPE_LABELS.update(UX_MATCH_TYPE_LABELS)

REACTION_MATCH_TYPES = ("exact", "prefix", "regex")
SEARCH_MATCH_TYPES = ("exact", "prefix", "regex")
MISSING_FORMAT_BEHAVIORS = ("ask_format", "latest", "reject")
DECK_SEARCH_KEY = "deck_search"
KEYWORD_DUPLICATE_ERROR = "この呼び出しワードは使用済み。"
ASSIGNMENT_TARGET_TYPE = "mention_reaction_choice"
ASSIGNMENT_EFFECT_TYPES = (
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
    "destroy",
)


def mention_reaction_kind_list_url(guild_id: str, reaction_kind: Optional[str]) -> str:
    if reaction_kind == "search":
        return "/guilds/{0}/mention-reactions?kind=search".format(guild_id)
    return "/guilds/{0}/mention-reactions?kind=random_draw".format(guild_id)


def register_mention_reaction_routes(templates: Jinja2Templates) -> None:
    @router.get("/guilds/{guild_id}/mention-reactions")
    async def mention_reactions_page(
        request: Request,
        guild_id: str,
        q: Optional[str] = Query(None),
        kind: str = Query("all"),
        system: str = Query("all"),
        enabled: str = Query("all"),
        show_test_data: str = Query("false"),
        message: str = Query(""),
        error: str = Query(""),
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)

        if not can_access_guild(guild_id, user["user_id"], selected_bot_id(request)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

        server = find_server(guild_id, user["user_id"], selected_bot_id(request))
        if kind == "limited":
            return RedirectResponse(url="/guilds/{0}/mention-reactions/limited".format(guild_id), status_code=303)
        if kind not in ("random_draw", "search"):
            return RedirectResponse(url="/guilds/{0}".format(guild_id), status_code=303)

        filters = normalize_filters(q, kind, system, enabled, show_test_data)

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
                "can_create_deck_search": role_allows(server["role"], "guild_admin"),
                "has_deck_search": has_deck_search_reaction(guild_id),
                "message": message,
                "error": error,
            },
        )

    @router.post("/guilds/{guild_id}/mention-reactions/bulk-enabled")
    async def bulk_set_mention_reactions_enabled(
        request: Request,
        guild_id: str,
        action: str = Form(""),
        reaction_ids: List[int] = Form([]),
        kind: str = Form("random_draw"),
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"], selected_bot_id(request)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"], selected_bot_id(request))
        redirect_url = mention_reaction_kind_list_url(guild_id, kind)
        if not reaction_ids:
            return RedirectResponse(url="{0}&error={1}".format(redirect_url, quote("項目を選択してね")), status_code=303)
        if action not in ("on", "off"):
            return RedirectResponse(url="{0}&error={1}".format(redirect_url, quote("操作を選んでね")), status_code=303)
        updated_count = 0
        with get_connection() as connection:
            repository = MentionReactionRepository(connection, bot_id=current_selected_bot_id())
            for reaction_id in reaction_ids:
                reaction = repository.get_by_id(guild_id, reaction_id)
                if reaction is None or not role_allows(server["role"], required_toggle_role(reaction)):
                    continue
                if repository.set_enabled(guild_id, reaction_id, action == "on") is not None:
                    updated_count += 1
            connection.commit()
        failed_count = max(0, len(reaction_ids) - updated_count)
        return RedirectResponse(
            url="{0}&message={1}".format(redirect_url, quote("成功{0}件 / 失敗{1}件".format(updated_count, failed_count))),
            status_code=303,
        )

    @router.post("/guilds/{guild_id}/mention-reactions/deck-search/create")
    async def create_deck_search_reaction(request: Request, guild_id: str):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)

        if not can_access_guild(guild_id, user["user_id"], selected_bot_id(request)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

        server = find_server(guild_id, user["user_id"], selected_bot_id(request))
        if not role_allows(server["role"], "guild_admin"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="deck search creation denied")

        with get_connection() as connection:
            repository = MentionReactionRepository(connection, bot_id=current_selected_bot_id())
            reaction = repository.ensure_deck_search_reaction(guild_id, enabled=False)
            connection.commit()

        return RedirectResponse(
            url="/guilds/{0}/mention-reactions/{1}".format(guild_id, reaction["id"]),
            status_code=303,
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

        if not can_access_guild(guild_id, user["user_id"], selected_bot_id(request)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

        server = find_server(guild_id, user["user_id"], selected_bot_id(request))
        redirect_url = mention_reaction_kind_list_url(guild_id, None)
        with get_connection() as connection:
            repository = MentionReactionRepository(connection, bot_id=current_selected_bot_id())
            reaction = repository.get_by_id(guild_id, reaction_id)
            if reaction is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="mention reaction not found")
            redirect_url = mention_reaction_kind_list_url(guild_id, reaction.get("reaction_kind"))

            required_role = required_toggle_role(reaction)
            if not role_allows(server["role"], required_role):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="mention reaction toggle denied")

            repository.toggle_enabled(guild_id, reaction_id)
            connection.commit()

        return RedirectResponse(url=redirect_url, status_code=303)

    @router.post("/guilds/{guild_id}/mention-reactions/{reaction_id}/copy")
    async def copy_mention_reaction(request: Request, guild_id: str, reaction_id: int):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"], selected_bot_id(request)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"], selected_bot_id(request))
        with get_connection() as connection:
            repository = MentionReactionRepository(connection, bot_id=current_selected_bot_id())
            reaction = repository.get_by_id(guild_id, reaction_id)
            if reaction is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="mention reaction not found")
            if not role_allows(server["role"], required_toggle_role(reaction)):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="mention reaction copy denied")
            copied = repository.copy_reaction(guild_id, reaction_id)
            if copied is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="mention reaction not found")
            connection.commit()
        return RedirectResponse(url="/guilds/{0}/mention-reactions/{1}".format(guild_id, copied["id"]), status_code=303)

    @router.post("/guilds/{guild_id}/mention-reactions/{reaction_id}/delete")
    async def delete_mention_reaction(
        request: Request,
        guild_id: str,
        reaction_id: int,
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)

        if not can_access_guild(guild_id, user["user_id"], selected_bot_id(request)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="サーバーを見る権限がありません。")

        server = find_server(guild_id, user["user_id"], selected_bot_id(request))
        redirect_url = mention_reaction_kind_list_url(guild_id, None)
        with get_connection() as connection:
            repository = MentionReactionRepository(connection, bot_id=current_selected_bot_id())
            reaction = repository.get_by_id(guild_id, reaction_id)
            if reaction is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="メンション反応が見つかりません。")
            redirect_url = mention_reaction_kind_list_url(guild_id, reaction.get("reaction_kind"))
            if not role_allows(server["role"], "editor"):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="削除する権限がありません。")
            if reaction.get("admin_only") and not role_allows(server["role"], "guild_admin"):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="管理者限定の反応はサーバー管理者だけ削除可。")
            if reaction.get("is_system") or not reaction.get("is_deletable", True):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="固定機能のため削除不可。")

            repository.delete_reaction(guild_id, reaction_id)
            connection.commit()

        return RedirectResponse(url=redirect_url, status_code=303)

    @router.get("/guilds/{guild_id}/mention-reactions/new")
    async def new_mention_reaction_page(request: Request, guild_id: str):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)

        if not can_access_guild(guild_id, user["user_id"], selected_bot_id(request)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

        server = find_server(guild_id, user["user_id"], selected_bot_id(request))
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

        if not can_access_guild(guild_id, user["user_id"], selected_bot_id(request)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

        server = find_server(guild_id, user["user_id"], selected_bot_id(request))
        if not role_allows(server["role"], "editor"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="mention reaction creation denied")

        form = build_reaction_form(name, description, keyword, match_type, enabled, admin_only)
        errors = validate_reaction_form(form)
        if form["admin_only"] and not role_allows(server["role"], "guild_admin"):
            errors.append("管理者限定の反応はサーバー管理者以上だけ作成可。")
        with get_connection() as connection:
            repository = MentionReactionRepository(connection, bot_id=current_selected_bot_id())
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

        if not can_access_guild(guild_id, user["user_id"], selected_bot_id(request)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

        server = find_server(guild_id, user["user_id"], selected_bot_id(request))
        with get_connection() as connection:
            repository = MentionReactionRepository(connection, bot_id=current_selected_bot_id())
            reaction = repository.get_by_id(guild_id, reaction_id)
            if reaction is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="mention reaction not found")
            choices = repository.list_choices(guild_id, reaction_id)
            choices = attach_choice_effects(connection, guild_id, choices, server["role"])
            reaction_view = build_reaction_view(reaction)
            deck_settings = build_deck_settings(reaction_view) if is_deck_search_reaction(reaction_view) else None
            if deck_settings is not None:
                runtime_settings = DeckSearchSettingsRepository(connection).get(current_selected_bot_id(), guild_id)
                deck_settings = merge_deck_runtime_settings(deck_settings, runtime_settings)

        return templates.TemplateResponse(
            request,
            "mention_reaction_form.html",
            {
                "user": user,
                "server": server,
                "guild_id": guild_id,
                "mode": "edit",
                "reaction": reaction_view,
                "choices": choices,
                "errors": [],
                "choice_errors": [],
                "deck_errors": [],
                "can_edit_reaction": can_edit_reaction(server["role"], reaction),
                "can_set_admin_only": role_allows(server["role"], "guild_admin"),
                "can_edit_choices": can_edit_reaction(server["role"], reaction) and reaction["reaction_kind"] == "random",
                "search_readonly": reaction["reaction_kind"] == "search",
                "deck_settings": deck_settings,
                "can_edit_deck_settings": can_edit_deck_settings(server["role"], reaction),
                "missing_format_behaviors": MISSING_FORMAT_BEHAVIORS,
                "match_types": build_match_type_options(),
            },
        )

    @router.post("/guilds/{guild_id}/mention-reactions/{reaction_id}/search-settings")
    async def update_search_settings(
        request: Request,
        guild_id: str,
        reaction_id: int,
        keyword: str = Form(""),
        match_type: str = Form("prefix"),
        enabled: Optional[str] = Form(None),
        allowed_channel_ids: str = Form(""),
        max_results: str = Form("3"),
        x_search_max_results: str = Form("50"),
        deny_message: str = Form(""),
        not_found_message: str = Form(""),
        missing_format_behavior: str = Form("ask_format"),
        x_query_template: str = Form(""),
        required_context_terms: str = Form(""),
        search_mode: str = Form("full_archive"),
        lookback_days: str = Form("14"),
        excluded_keywords: str = Form(""),
        include_retweets: Optional[str] = Form(None),
        include_replies: Optional[str] = Form(None),
        image_scan_limit: str = Form("30"),
        image_scan_concurrency: str = Form("2"),
        stop_after_candidates: Optional[str] = Form(None),
        image_fetch_timeout_seconds: str = Form("5"),
        high_accuracy_enabled: Optional[str] = Form(None),
        high_accuracy_x_search_max_results: str = Form("100"),
        high_accuracy_image_scan_limit: str = Form("100"),
        high_accuracy_image_scan_concurrency: str = Form("2"),
        high_accuracy_stop_after_candidates: Optional[str] = Form(None),
        request_timeout_seconds: str = Form("10"),
        cache_ttl_seconds: str = Form("300"),
        result_format: str = Form("default"),
        class_filter_required: Optional[str] = Form(None),
        fetch_since_date: str = Form(""),
        max_lookback_days: str = Form("30"),
        description: str = Form(""),
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)

        if not can_access_guild(guild_id, user["user_id"], selected_bot_id(request)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

        server = find_server(guild_id, user["user_id"], selected_bot_id(request))
        with get_connection() as connection:
            repository = MentionReactionRepository(connection, bot_id=current_selected_bot_id())
            reaction = repository.get_by_id(guild_id, reaction_id)
            if reaction is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="mention reaction not found")
            if not is_deck_search_reaction(reaction):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="deck search settings denied")
            if not can_edit_deck_settings(server["role"], reaction):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="deck search settings editing denied")

            deck_settings, errors = build_deck_settings_form(
                keyword,
                match_type,
                enabled,
                allowed_channel_ids,
                max_results,
                x_search_max_results,
                deny_message,
                not_found_message,
                missing_format_behavior,
                x_query_template,
                required_context_terms,
                search_mode,
                lookback_days,
                excluded_keywords,
                include_retweets,
                include_replies,
                image_scan_limit,
                image_scan_concurrency,
                stop_after_candidates,
                image_fetch_timeout_seconds,
                high_accuracy_enabled,
                high_accuracy_x_search_max_results,
                high_accuracy_image_scan_limit,
                high_accuracy_image_scan_concurrency,
                high_accuracy_stop_after_candidates,
                request_timeout_seconds,
                cache_ttl_seconds,
                result_format,
                class_filter_required,
                fetch_since_date,
                max_lookback_days,
                description,
            )
            if not errors and repository.keyword_exists(guild_id, deck_settings["keyword"], reaction_id):
                errors.append(KEYWORD_DUPLICATE_ERROR)

            if errors:
                choices = repository.list_choices(guild_id, reaction_id)
                choices = attach_choice_effects(connection, guild_id, choices, server["role"])
                reaction_view = build_reaction_view(reaction)
                reaction_view.update(
                    {
                        "keyword": deck_settings["keyword"],
                        "match_type": deck_settings["match_type"],
                        "enabled": deck_settings["enabled"],
                        "description": deck_settings["description"],
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
                        "reaction": reaction_view,
                        "choices": choices,
                        "errors": [],
                        "choice_errors": [],
                        "deck_errors": errors,
                        "can_edit_reaction": False,
                        "can_set_admin_only": role_allows(server["role"], "guild_admin"),
                        "can_edit_choices": False,
                        "search_readonly": True,
                        "deck_settings": deck_settings,
                        "can_edit_deck_settings": True,
                        "missing_format_behaviors": MISSING_FORMAT_BEHAVIORS,
                        "match_types": build_match_type_options(),
                    },
                    status_code=400,
                )

            repository.update_search_settings(
                guild_id,
                reaction_id,
                deck_settings["keyword"],
                deck_settings["match_type"],
                deck_settings["description"],
                deck_settings["enabled"],
                deck_settings["config_json"],
            )
            DeckSearchSettingsRepository(connection).upsert(
                current_selected_bot_id(),
                guild_id,
                deck_settings["fetch_since_date_value"],
                deck_settings["max_lookback_days"],
                user["user_id"],
            )
            connection.commit()

        return RedirectResponse(
            url="/guilds/{0}/mention-reactions/{1}".format(guild_id, reaction_id),
            status_code=303,
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

        if not can_access_guild(guild_id, user["user_id"], selected_bot_id(request)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

        server = find_server(guild_id, user["user_id"], selected_bot_id(request))
        with get_connection() as connection:
            repository = MentionReactionRepository(connection, bot_id=current_selected_bot_id())
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
                errors.append("管理者限定の変更はサーバー管理者以上だけ。")
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
            choices = attach_choice_effects(connection, guild_id, choices, server["role"])

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
        result_label: str = Form(""),
        body: str = Form(""),
        emoji_internal: str = Form(""),
        image_path: str = Form(""),
        image_upload: Optional[UploadFile] = File(None),
        appearance_rate: str = Form("1"),
        choice_enabled: Optional[str] = Form(None),
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)

        server, repository, reaction, connection = prepare_choice_mutation(guild_id, reaction_id, user["user_id"])
        try:
            uploaded_path, upload_error = await save_uploaded_image(image_upload, "mention_reaction_choices")
            if uploaded_path:
                image_path = uploaded_path
            choice_form = build_choice_form(
                choice_name,
                result_label,
                body,
                emoji_internal,
                image_path,
                appearance_rate,
                choice_enabled,
            )
            errors = validate_choice_form(choice_form)
            if upload_error:
                errors.append(upload_error)
            if errors:
                choices = repository.list_choices(guild_id, reaction_id)
                choices = attach_choice_effects(connection, guild_id, choices, server["role"])
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
                choice_form["result_label"] or None,
                choice_form["emoji_internal"] or None,
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
        result_label: str = Form(""),
        body: str = Form(""),
        emoji_internal: str = Form(""),
        image_path: str = Form(""),
        image_upload: Optional[UploadFile] = File(None),
        delete_image: Optional[str] = Form(None),
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

            if delete_image:
                image_path = ""
            uploaded_path, upload_error = await save_uploaded_image(image_upload, "mention_reaction_choices")
            if uploaded_path:
                image_path = uploaded_path
            choice_form = build_choice_form(
                choice_name,
                result_label,
                body,
                emoji_internal,
                image_path,
                appearance_rate,
                choice_enabled,
            )
            errors = validate_choice_form(choice_form)
            if upload_error:
                errors.append(upload_error)
            if errors:
                choices = repository.list_choices(guild_id, reaction_id)
                choices = attach_choice_effects(connection, guild_id, choices, server["role"])
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
                choice_form["result_label"] or None,
                choice_form["emoji_internal"] or None,
            )
            connection.commit()
        finally:
            connection.close()

        return RedirectResponse(
            url="/guilds/{0}/mention-reactions/{1}".format(guild_id, reaction_id),
            status_code=303,
        )

    @router.post("/guilds/{guild_id}/mention-reactions/{reaction_id}/choices/{choice_id}/delete")
    async def delete_mention_reaction_choice(
        request: Request,
        guild_id: str,
        reaction_id: int,
        choice_id: int,
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)

        server, repository, reaction, connection = prepare_choice_mutation(guild_id, reaction_id, user["user_id"])
        try:
            choice = repository.get_choice(guild_id, choice_id)
            if choice is None or int(choice["mention_reaction_id"]) != reaction_id:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="候補が見つかりません。")
            repository.delete_choice(guild_id, choice_id)
            connection.commit()
        finally:
            connection.close()

        return RedirectResponse(
            url="/guilds/{0}/mention-reactions/{1}".format(guild_id, reaction_id),
            status_code=303,
        )

    @router.get("/guilds/{guild_id}/mention-reactions/{reaction_id}/choices/{choice_id}/effects")
    async def choice_effects_page(
        request: Request,
        guild_id: str,
        reaction_id: int,
        choice_id: int,
        q: Optional[str] = Query(None),
        effect_type: str = Query("all"),
        admin_only: str = Query("all"),
        include_disabled: str = Query("false"),
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"], selected_bot_id(request)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

        server = find_server(guild_id, user["user_id"], selected_bot_id(request))
        with get_connection() as connection:
            mention_repository = MentionReactionRepository(connection, bot_id=current_selected_bot_id())
            reaction = mention_repository.get_by_id(guild_id, reaction_id)
            if reaction is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="mention reaction not found")
            choice = mention_repository.get_choice(guild_id, choice_id)
            if choice is None or int(choice["mention_reaction_id"]) != reaction_id:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="mention reaction choice not found")

            filters = normalize_assignment_filters(q, effect_type, admin_only, include_disabled)
            tags = list_assignable_effect_rows(connection, guild_id, choice_id, server["role"], filters)

        return templates.TemplateResponse(
            request,
            "choice_effects.html",
            {
                "user": user,
                "server": server,
                "guild_id": guild_id,
                "reaction": build_reaction_view(reaction),
                "choice": choice,
                "filters": filters,
                "tags": tags,
                "effect_types": ASSIGNMENT_EFFECT_TYPES,
                "effect_type_labels": EFFECT_TYPE_LABELS,
                "can_manage_any": role_allows(server["role"], "editor"),
            },
        )

    @router.post("/guilds/{guild_id}/mention-reactions/{reaction_id}/choices/{choice_id}/effects")
    async def update_choice_effects(
        request: Request,
        guild_id: str,
        reaction_id: int,
        choice_id: int,
        tag_id: int = Form(...),
        action: str = Form(...),
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"], selected_bot_id(request)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

        server = find_server(guild_id, user["user_id"], selected_bot_id(request))
        with get_connection() as connection:
            mention_repository = MentionReactionRepository(connection, bot_id=current_selected_bot_id())
            reaction = mention_repository.get_by_id(guild_id, reaction_id)
            if reaction is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="mention reaction not found")
            choice = mention_repository.get_choice(guild_id, choice_id)
            if choice is None or int(choice["mention_reaction_id"]) != reaction_id:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="mention reaction choice not found")

            effect_repository = SpecialEffectRepository(connection, bot_id=current_selected_bot_id())
            tag = effect_repository.get_by_id(guild_id, tag_id)
            if tag is None or tag.get("target_type") != ASSIGNMENT_TARGET_TYPE:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="special effect tag not found")
            if not can_manage_effect_assignment(server["role"], tag):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="special effect assignment denied")

            if action == "assign":
                effect_repository.assign_tag(guild_id, tag_id, ASSIGNMENT_TARGET_TYPE, choice_id)
            elif action == "unassign":
                effect_repository.unassign_tag(guild_id, tag_id, ASSIGNMENT_TARGET_TYPE, choice_id)
            else:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unknown assignment action")

            connection.commit()

        return RedirectResponse(
            url="/guilds/{0}/mention-reactions/{1}".format(
                guild_id,
                reaction_id,
            ),
            status_code=303,
        )


def normalize_filters(
    query: Optional[str],
    kind: str,
    system: str,
    enabled: str,
    show_test_data: str = "false",
) -> Dict[str, Any]:
    normalized_query = (query or "").strip()
    normalized_kind = kind if kind in ("random_draw", "search") else "random_draw"
    normalized_system = system if system in ("all", "system", "custom") else "all"
    normalized_enabled = enabled if enabled in ("all", "true", "false") else "all"

    return {
        "q": normalized_query,
        "kind": normalized_kind,
        "system": normalized_system,
        "enabled": normalized_enabled,
        "show_test_data": parse_show_test_data(show_test_data),
    }

def list_reaction_rows(
    guild_id: str,
    role: str,
    filters: Dict[str, Any],
) -> List[Dict[str, Any]]:
    with get_connection() as connection:
        repository = MentionReactionRepository(connection, bot_id=current_selected_bot_id())
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
        if not filters["show_test_data"] and row_is_hidden_test_data(row):
            continue
        display_kind = display_reaction_kind(row["reaction_kind"])
        row["display_reaction_kind"] = display_kind
        row["reaction_kind_label"] = KIND_LABELS.get(display_kind, display_kind)
        row["match_type_label"] = MATCH_TYPE_LABELS.get(row["match_type"], row["match_type"])
        row["choice_count"] = int(row.get("choice_count") or 0)
        row["can_toggle"] = role_allows(role, required_toggle_role(row))
        row["edit_url"] = "/guilds/{0}/mention-reactions/{1}".format(guild_id, row["id"])
        row["toggle_url"] = "/guilds/{0}/mention-reactions/{1}/toggle".format(guild_id, row["id"])
        row["copy_url"] = "/guilds/{0}/mention-reactions/{1}/copy".format(guild_id, row["id"])
        row["delete_url"] = "/guilds/{0}/mention-reactions/{1}/delete".format(guild_id, row["id"])
        row["can_delete"] = (
            role_allows(role, "editor")
            and not bool(row.get("is_system"))
            and bool(row.get("is_deletable", True))
            and (not row.get("admin_only") or role_allows(role, "guild_admin"))
        )
        rows.append(row)

    return rows


def row_is_hidden_test_data(row: Dict[str, Any]) -> bool:
    return (
        is_test_data(row.get("reaction_key"))
        or is_test_data(row.get("name"))
        or is_test_data(row.get("keyword"))
        or is_test_data(row.get("description"))
    )


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


def can_see_effect_tag(role: str, tag: Dict[str, Any]) -> bool:
    if tag.get("admin_only"):
        return role_allows(role, "guild_admin")
    return True


def can_manage_effect_assignment(role: str, tag: Dict[str, Any]) -> bool:
    if tag.get("admin_only"):
        return role_allows(role, "guild_admin")
    return role_allows(role, "editor")


def attach_choice_effects(
    connection,
    guild_id: str,
    choices: List[Dict[str, Any]],
    role: str,
) -> List[Dict[str, Any]]:
    repository = SpecialEffectRepository(connection, bot_id=current_selected_bot_id())
    next_choices = []
    for choice in choices:
        row = dict(choice)
        effects = repository.list_for_target(
            guild_id,
            ASSIGNMENT_TARGET_TYPE,
            int(choice["id"]),
            enabled=None,
        )
        row["effects"] = [
            build_effect_assignment_view(effect, role)
            for effect in effects
            if effect.get("assignment_enabled") and can_see_effect_tag(role, effect)
        ]
        row["effects_url"] = "/guilds/{0}/mention-reactions/{1}/choices/{2}/effects".format(
            guild_id,
            choice["mention_reaction_id"],
            choice["id"],
        )
        next_choices.append(row)
    return next_choices


def build_effect_assignment_view(effect: Dict[str, Any], role: str) -> Dict[str, Any]:
    row = dict(effect)
    row["can_manage"] = can_manage_effect_assignment(role, effect)
    row["effect_config_summary"] = compact_effect_json(effect.get("effect_config_json"))
    row["effect_type_label"] = EFFECT_TYPE_LABELS.get(row.get("effect_type"), row.get("effect_type"))
    row["additional_post_timing_label"] = ADDITIONAL_POST_TIMING_LABELS.get(
        row.get("additional_post_timing"),
        row.get("additional_post_timing"),
    )
    row["cooldown_scope_label"] = COOLDOWN_SCOPE_LABELS.get(row.get("cooldown_scope"), row.get("cooldown_scope"))
    row["expires_type_label"] = EXPIRES_TYPE_LABELS.get(row.get("expires_type"), row.get("expires_type"))
    row["target_type_label"] = TARGET_TYPE_LABELS.get(row.get("target_type"), row.get("target_type"))
    return row


def compact_effect_json(value) -> str:
    if value is None:
        return "{}"
    if isinstance(value, str):
        return value
    try:
        import json

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
        "effect_type": effect_type if effect_type in ASSIGNMENT_EFFECT_TYPES or effect_type == "all" else "all",
        "admin_only": admin_only if admin_only in ("all", "true", "false") else "all",
        "include_disabled": include_disabled == "true",
    }


def parse_assignment_bool(value: str) -> Optional[bool]:
    if value == "true":
        return True
    if value == "false":
        return False
    return None


def list_assignable_effect_rows(
    connection,
    guild_id: str,
    choice_id: int,
    role: str,
    filters: Dict[str, Any],
) -> List[Dict[str, Any]]:
    repository = SpecialEffectRepository(connection, bot_id=current_selected_bot_id())
    tags = repository.list_tags(
        guild_id,
        query=filters["q"] or None,
        effect_type=None if filters["effect_type"] == "all" else filters["effect_type"],
        target_type=ASSIGNMENT_TARGET_TYPE,
        enabled=None if filters["include_disabled"] else True,
        admin_only=parse_assignment_bool(filters["admin_only"]),
    )
    assigned = {
        int(effect["id"]): bool(effect["assignment_enabled"])
        for effect in repository.list_for_target(
            guild_id,
            ASSIGNMENT_TARGET_TYPE,
            choice_id,
            enabled=None,
        )
    }

    rows = []
    for tag in tags:
        if not can_see_effect_tag(role, tag):
            continue
        row = build_effect_assignment_view(tag, role)
        row["assigned"] = assigned.get(int(tag["id"]), False)
        rows.append(row)
    return rows


def can_edit_reaction(role: str, reaction: Dict[str, Any]) -> bool:
    if reaction.get("reaction_kind") == "search":
        return False
    if reaction.get("admin_only") or reaction.get("is_system"):
        return role_allows(role, "guild_admin")
    return role_allows(role, "editor")


def can_edit_deck_settings(role: str, reaction: Dict[str, Any]) -> bool:
    if not is_deck_search_reaction(reaction):
        return False
    if reaction.get("admin_only"):
        return role_allows(role, "guild_admin")
    return role_allows(role, "editor")


def has_deck_search_reaction(guild_id: str) -> bool:
    with get_connection() as connection:
        repository = MentionReactionRepository(connection, bot_id=current_selected_bot_id())
        return repository.get_by_key(guild_id, DECK_SEARCH_KEY) is not None


def is_deck_search_reaction(reaction: Dict[str, Any]) -> bool:
    if reaction.get("reaction_kind") != "search":
        return False
    config = normalize_config_json(reaction.get("config_json"))
    return (
        config.get("search_type") == "deck_search"
        or reaction.get("reaction_key") == DECK_SEARCH_KEY
        or reaction.get("name") == "デッキ検索"
    )


def normalize_config_json(value) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    if isinstance(parsed, dict):
        return parsed
    return {}


def build_deck_settings(reaction: Dict[str, Any]) -> Dict[str, Any]:
    config = normalize_config_json(reaction.get("config_json"))
    excluded_keywords = config.get("excluded_keywords")
    if excluded_keywords is None:
        excluded_keywords = DEFAULT_EXCLUDED_KEYWORDS
    return {
        "keyword": reaction.get("keyword") or "",
        "match_type": reaction.get("match_type") or "prefix",
        "enabled": bool(reaction.get("enabled")),
        "description": reaction.get("description") or "",
        "allowed_channel_ids": "\n".join(config.get("allowed_channel_ids") or []),
        "max_results": int(config.get("max_results") or 3),
        "x_search_max_results": int(config.get("x_search_max_results") or 50),
        "deny_message": config.get("deny_message") or "このチャンネルではデッキ検索は使えません。",
        "not_found_message": config.get("not_found_message") or "おい ないんだが",
        "missing_format_behavior": config.get("missing_format_behavior") or "ask_format",
        "x_query_template": config.get("x_query_template") or DEFAULT_X_QUERY_TEMPLATE,
        "required_context_terms": "\n".join([str(item) for item in (config.get("required_context_terms") or DEFAULT_REQUIRED_CONTEXT_TERMS)]),
        "search_mode": config.get("search_mode") or "full_archive",
        "lookback_days": int(config.get("lookback_days") or 14),
        "excluded_keywords": "\n".join([str(item) for item in excluded_keywords]),
        "include_retweets": bool(config.get("include_retweets", False)),
        "include_replies": bool(config.get("include_replies", False)),
        "image_scan_limit": int(config.get("image_scan_limit") or 30),
        "image_scan_concurrency": int(config.get("image_scan_concurrency") or 2),
        "stop_after_candidates": bool(config.get("stop_after_candidates", True)),
        "image_fetch_timeout_seconds": int(config.get("image_fetch_timeout_seconds") or 5),
        "high_accuracy_enabled": bool(config.get("high_accuracy_enabled", True)),
        "high_accuracy_x_search_max_results": int(config.get("high_accuracy_x_search_max_results") or 100),
        "high_accuracy_image_scan_limit": int(config.get("high_accuracy_image_scan_limit") or 100),
        "high_accuracy_image_scan_concurrency": int(config.get("high_accuracy_image_scan_concurrency") or 2),
        "high_accuracy_stop_after_candidates": bool(config.get("high_accuracy_stop_after_candidates", False)),
        "request_timeout_seconds": int(config.get("request_timeout_seconds") or 10),
        "cache_ttl_seconds": int(config.get("cache_ttl_seconds") or 300),
        "result_format": config.get("result_format") or "default",
        "class_filter_required": bool(config.get("class_filter_required", True)),
        "config_json": {
            "search_type": "deck_search",
            "allowed_channel_ids": config.get("allowed_channel_ids") or [],
            "max_results": int(config.get("max_results") or 3),
            "x_search_max_results": int(config.get("x_search_max_results") or 50),
            "deny_message": config.get("deny_message") or "このチャンネルではデッキ検索は使えません。",
            "not_found_message": config.get("not_found_message") or "おい ないんだが",
            "missing_format_behavior": config.get("missing_format_behavior") or "ask_format",
            "x_query_template": config.get("x_query_template") or DEFAULT_X_QUERY_TEMPLATE,
            "required_context_terms": config.get("required_context_terms") or DEFAULT_REQUIRED_CONTEXT_TERMS,
            "search_mode": config.get("search_mode") or "full_archive",
            "lookback_days": int(config.get("lookback_days") or 14),
            "excluded_keywords": excluded_keywords,
            "include_retweets": bool(config.get("include_retweets", False)),
            "include_replies": bool(config.get("include_replies", False)),
            "image_scan_limit": int(config.get("image_scan_limit") or 30),
            "image_scan_concurrency": int(config.get("image_scan_concurrency") or 2),
            "stop_after_candidates": bool(config.get("stop_after_candidates", True)),
            "image_fetch_timeout_seconds": int(config.get("image_fetch_timeout_seconds") or 5),
            "high_accuracy_enabled": bool(config.get("high_accuracy_enabled", True)),
            "high_accuracy_x_search_max_results": int(config.get("high_accuracy_x_search_max_results") or 100),
            "high_accuracy_image_scan_limit": int(config.get("high_accuracy_image_scan_limit") or 100),
            "high_accuracy_image_scan_concurrency": int(config.get("high_accuracy_image_scan_concurrency") or 2),
            "high_accuracy_stop_after_candidates": bool(config.get("high_accuracy_stop_after_candidates", False)),
            "request_timeout_seconds": int(config.get("request_timeout_seconds") or 10),
            "cache_ttl_seconds": int(config.get("cache_ttl_seconds") or 300),
            "result_format": config.get("result_format") or "default",
            "class_filter_required": bool(config.get("class_filter_required", True)),
        },
    }


def merge_deck_runtime_settings(settings: Dict[str, Any], runtime_settings: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    merged = dict(settings)
    fetch_since = settings_fetch_since_date(runtime_settings)
    merged["fetch_since_date"] = fetch_since.isoformat() if fetch_since else ""
    merged["max_lookback_days"] = settings_max_lookback_days(runtime_settings)
    return merged


def build_deck_settings_form(
    keyword: str,
    match_type: str,
    enabled: Optional[str],
    allowed_channel_ids: str,
    max_results: str,
    x_search_max_results: str,
    deny_message: str,
    not_found_message: str,
    missing_format_behavior: str,
    x_query_template: str,
    required_context_terms: str,
    search_mode: str,
    lookback_days: str,
    excluded_keywords: str,
    include_retweets: Optional[str],
    include_replies: Optional[str],
    image_scan_limit: str,
    image_scan_concurrency: str,
    stop_after_candidates: Optional[str],
    image_fetch_timeout_seconds: str,
    high_accuracy_enabled: Optional[str],
    high_accuracy_x_search_max_results: str,
    high_accuracy_image_scan_limit: str,
    high_accuracy_image_scan_concurrency: str,
    high_accuracy_stop_after_candidates: Optional[str],
    request_timeout_seconds: str,
    cache_ttl_seconds: str,
    result_format: str,
    class_filter_required: Optional[str],
    fetch_since_date: str,
    max_lookback_days: str,
    description: str,
) -> Tuple[Dict[str, Any], List[str]]:
    channel_ids = split_channel_ids(allowed_channel_ids)
    try:
        result_count = int(max_results)
    except ValueError:
        result_count = 0
    try:
        x_result_count = int(x_search_max_results)
    except ValueError:
        x_result_count = 0
    try:
        scan_limit = int(image_scan_limit)
    except ValueError:
        scan_limit = 0
    try:
        scan_concurrency = int(image_scan_concurrency)
    except ValueError:
        scan_concurrency = 0
    try:
        fetch_timeout_seconds = int(image_fetch_timeout_seconds)
    except ValueError:
        fetch_timeout_seconds = 0
    try:
        high_accuracy_scan_limit = int(high_accuracy_image_scan_limit)
    except ValueError:
        high_accuracy_scan_limit = 0
    try:
        high_accuracy_x_result_count = int(high_accuracy_x_search_max_results)
    except ValueError:
        high_accuracy_x_result_count = 0
    try:
        high_accuracy_scan_concurrency = int(high_accuracy_image_scan_concurrency)
    except ValueError:
        high_accuracy_scan_concurrency = 0
    try:
        timeout_seconds = int(request_timeout_seconds)
    except ValueError:
        timeout_seconds = 0
    try:
        ttl_seconds = int(cache_ttl_seconds)
    except ValueError:
        ttl_seconds = 0
    try:
        search_days = int(lookback_days)
    except ValueError:
        search_days = 0
    try:
        max_days = int(max_lookback_days)
    except ValueError:
        max_days = DEFAULT_MAX_LOOKBACK_DAYS
    parsed_fetch_since_date = None
    if fetch_since_date.strip():
        try:
            parsed_fetch_since_date = parse_fetch_since_date(fetch_since_date)
        except ValueError:
            parsed_fetch_since_date = None
    excluded_keyword_list = split_channel_ids(excluded_keywords)
    required_context_term_list = split_channel_ids(required_context_terms) or list(DEFAULT_REQUIRED_CONTEXT_TERMS)

    settings = {
        "keyword": keyword.strip(),
        "match_type": match_type if match_type in SEARCH_MATCH_TYPES else "prefix",
        "enabled": enabled == "on",
        "description": description.strip(),
        "allowed_channel_ids": "\n".join(channel_ids),
        "max_results": result_count,
        "x_search_max_results": x_result_count,
        "deny_message": deny_message.strip(),
        "not_found_message": not_found_message.strip() or "おい ないんだが",
        "missing_format_behavior": missing_format_behavior if missing_format_behavior in MISSING_FORMAT_BEHAVIORS else "ask_format",
        "x_query_template": x_query_template.strip() or DEFAULT_X_QUERY_TEMPLATE,
        "required_context_terms": "\n".join(required_context_term_list),
        "search_mode": search_mode if search_mode in ("recent", "full_archive") else "full_archive",
        "lookback_days": search_days,
        "excluded_keywords": "\n".join(excluded_keyword_list),
        "include_retweets": include_retweets == "on",
        "include_replies": include_replies == "on",
        "image_scan_limit": scan_limit,
        "image_scan_concurrency": scan_concurrency,
        "stop_after_candidates": stop_after_candidates == "on",
        "image_fetch_timeout_seconds": fetch_timeout_seconds,
        "high_accuracy_enabled": high_accuracy_enabled == "on",
        "high_accuracy_x_search_max_results": high_accuracy_x_result_count,
        "high_accuracy_image_scan_limit": high_accuracy_scan_limit,
        "high_accuracy_image_scan_concurrency": high_accuracy_scan_concurrency,
        "high_accuracy_stop_after_candidates": high_accuracy_stop_after_candidates == "on",
        "request_timeout_seconds": timeout_seconds,
        "cache_ttl_seconds": ttl_seconds,
        "result_format": result_format.strip() or "default",
        "class_filter_required": class_filter_required == "on",
        "fetch_since_date": fetch_since_date.strip(),
        "fetch_since_date_value": parsed_fetch_since_date,
        "max_lookback_days": max_days,
    }
    settings["config_json"] = {
        "search_type": "deck_search",
        "allowed_channel_ids": channel_ids,
        "max_results": result_count,
        "x_search_max_results": x_result_count,
        "deny_message": settings["deny_message"],
        "not_found_message": settings["not_found_message"],
        "missing_format_behavior": settings["missing_format_behavior"],
        "x_query_template": settings["x_query_template"],
        "required_context_terms": required_context_term_list,
        "search_mode": settings["search_mode"],
        "lookback_days": settings["lookback_days"],
        "excluded_keywords": excluded_keyword_list,
        "include_retweets": settings["include_retweets"],
        "include_replies": settings["include_replies"],
        "image_scan_limit": settings["image_scan_limit"],
        "image_scan_concurrency": settings["image_scan_concurrency"],
        "stop_after_candidates": settings["stop_after_candidates"],
        "image_fetch_timeout_seconds": settings["image_fetch_timeout_seconds"],
        "high_accuracy_enabled": settings["high_accuracy_enabled"],
        "high_accuracy_x_search_max_results": settings["high_accuracy_x_search_max_results"],
        "high_accuracy_image_scan_limit": settings["high_accuracy_image_scan_limit"],
        "high_accuracy_image_scan_concurrency": settings["high_accuracy_image_scan_concurrency"],
        "high_accuracy_stop_after_candidates": settings["high_accuracy_stop_after_candidates"],
        "request_timeout_seconds": settings["request_timeout_seconds"],
        "cache_ttl_seconds": settings["cache_ttl_seconds"],
        "result_format": settings["result_format"],
        "class_filter_required": settings["class_filter_required"],
    }

    errors = []
    if not settings["keyword"]:
        errors.append("デッキ検索の呼び出しワードを入力。")
    if match_type not in SEARCH_MATCH_TYPES:
        errors.append("一致方式を選択。")
    if result_count < 1:
        errors.append("返す件数は1以上。")
    if x_result_count < 10 or x_result_count > 100:
        errors.append("Xから取得する投稿数は10から100まで")
    if high_accuracy_x_result_count < 10 or high_accuracy_x_result_count > 100:
        errors.append("高精度のX取得数は10から100まで")
    if search_days < 1 or search_days > 30:
        errors.append("検索対象日数は1から30まで")
    if max_days < 1:
        errors.append("最大遡り日数は1以上")
    if fetch_since_date.strip() and parsed_fetch_since_date is None:
        errors.append("取得開始日の日付が不正")
    if parsed_fetch_since_date is not None:
        date_error = validate_fetch_since_date(parsed_fetch_since_date, max_days)
        if date_error:
            errors.append(date_error)
    if scan_limit < 1:
        errors.append("image_scan_limit must be 1 or more")
    if scan_concurrency < 1 or scan_concurrency > 10:
        errors.append("同時確認数は1から10まで")
    if fetch_timeout_seconds < 1:
        errors.append("画像取得秒数は1以上")
    if high_accuracy_scan_limit < 1:
        errors.append("高精度の画像確認数は1以上")
    if high_accuracy_scan_concurrency < 1 or high_accuracy_scan_concurrency > 10:
        errors.append("高精度の同時確認数は1から10まで")
    if timeout_seconds < 1:
        errors.append("request_timeout_seconds must be 1 or more")
    if ttl_seconds < 0:
        errors.append("cache_ttl_seconds must be 0 or more")
    if missing_format_behavior not in MISSING_FORMAT_BEHAVIORS:
        errors.append("フォーマット未指定時の扱いを選択。")
    return settings, errors


def split_channel_ids(value: str) -> List[str]:
    normalized = value.replace(",", "\n")
    return [item.strip() for item in normalized.splitlines() if item.strip()]


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
        errors.append("反応名を入力。")
    if not form["keyword"]:
        errors.append("呼び出しワードを入力。")
    if form["match_type"] not in REACTION_MATCH_TYPES:
        errors.append("一致方式を選択。")
    return errors


def build_choice_form(
    name: str,
    result_label: str,
    body: str,
    emoji_internal: str,
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
        "result_label": result_label.strip(),
        "body": body.strip(),
        "emoji_internal": emoji_internal.strip(),
        "image_path": image_path.strip(),
        "appearance_rate": parsed_rate,
        "enabled": enabled == "on",
    }


def validate_choice_form(form: Dict[str, Any]) -> List[str]:
    errors = []
    if not form["name"]:
        errors.append("候補名を入力。")
    if form["appearance_rate"] < 1:
        errors.append("出やすさは1以上の整数。")
    return errors


def prepare_choice_mutation(guild_id: str, reaction_id: int, discord_user_id: str):
    if not can_access_guild(guild_id, discord_user_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

    server = find_server(guild_id, discord_user_id)
    connection = connect()
    repository = MentionReactionRepository(connection, bot_id=current_selected_bot_id())
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
