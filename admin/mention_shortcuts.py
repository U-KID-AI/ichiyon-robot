from typing import Dict, List, Optional

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from admin.auth import get_current_user
from admin.bot_context import current_selected_bot_id, selected_bot_id
from admin.servers import can_access_guild, find_server, role_allows
from bot.db import get_connection
from bot.repositories.audio_assets import AudioAssetRepository
from bot.repositories.mention_shortcuts import MentionShortcutRepository, normalize_shortcut_trigger
from bot.services import game_provider


router = APIRouter()


def register_mention_shortcut_routes(templates: Jinja2Templates) -> None:
    @router.get("/guilds/{guild_id}/mention-shortcuts")
    async def mention_shortcuts_page(request: Request, guild_id: str, message: str = "", error: str = ""):
        user, server = require_access(request, guild_id)
        bot_id = current_selected_bot_id()
        with get_connection() as connection:
            repo = MentionShortcutRepository(connection, bot_id=bot_id)
            shortcuts = repo.list_shortcuts(guild_id)
        return templates.TemplateResponse(
            request,
            "mention_shortcuts.html",
            {
                "user": user,
                "server": server,
                "guild_id": guild_id,
                "shortcuts": shortcuts,
                "can_edit": role_allows(server["role"], "editor"),
                "message": message,
                "error": error,
                "provider_status": provider_status_rows(),
            },
        )

    @router.get("/guilds/{guild_id}/mention-shortcuts/new")
    async def new_shortcut_page(request: Request, guild_id: str):
        user, server = require_editor(request, guild_id)
        return render_form(templates, request, server, guild_id, default_form(), "new")

    @router.post("/guilds/{guild_id}/mention-shortcuts/new")
    async def create_shortcut(
        request: Request,
        guild_id: str,
        name: str = Form(""),
        trigger_text: str = Form(""),
        enabled: Optional[str] = Form(None),
        provider_1: str = Form("steam"),
        product_id_1: str = Form(""),
        lookup_type_1: str = Form("app_id"),
        display_name_1: str = Form("Steam"),
        provider_2: str = Form(""),
        product_id_2: str = Form(""),
        lookup_type_2: str = Form(""),
        display_name_2: str = Form(""),
        provider_3: str = Form(""),
        product_id_3: str = Form(""),
        lookup_type_3: str = Form(""),
        display_name_3: str = Form(""),
        audio_asset_id: str = Form(""),
        volume_override: str = Form(""),
    ):
        user, server = require_editor(request, guild_id)
        form = collect_form(
            name,
            trigger_text,
            enabled,
            provider_1,
            product_id_1,
            lookup_type_1,
            display_name_1,
            provider_2,
            product_id_2,
            lookup_type_2,
            display_name_2,
            provider_3,
            product_id_3,
            lookup_type_3,
            display_name_3,
            audio_asset_id,
            volume_override,
        )
        errors = validate_form(form)
        if errors:
            return render_form(templates, request, server, guild_id, form, "new", errors, status_code=400)
        save_shortcut(guild_id, form)
        return RedirectResponse(url="/guilds/{0}/mention-shortcuts?message=created".format(guild_id), status_code=303)

    @router.get("/guilds/{guild_id}/mention-shortcuts/{shortcut_id}")
    async def edit_shortcut_page(request: Request, guild_id: str, shortcut_id: int):
        user, server = require_access(request, guild_id)
        form = load_shortcut_form(guild_id, shortcut_id)
        if form is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="shortcut not found")
        return render_form(
            templates,
            request,
            server,
            guild_id,
            form,
            "edit",
            can_edit=role_allows(server["role"], "editor"),
            shortcut_id=shortcut_id,
        )

    @router.post("/guilds/{guild_id}/mention-shortcuts/{shortcut_id}")
    async def update_shortcut(
        request: Request,
        guild_id: str,
        shortcut_id: int,
        name: str = Form(""),
        trigger_text: str = Form(""),
        enabled: Optional[str] = Form(None),
        provider_1: str = Form("steam"),
        product_id_1: str = Form(""),
        lookup_type_1: str = Form("app_id"),
        display_name_1: str = Form("Steam"),
        provider_2: str = Form(""),
        product_id_2: str = Form(""),
        lookup_type_2: str = Form(""),
        display_name_2: str = Form(""),
        provider_3: str = Form(""),
        product_id_3: str = Form(""),
        lookup_type_3: str = Form(""),
        display_name_3: str = Form(""),
        audio_asset_id: str = Form(""),
        volume_override: str = Form(""),
    ):
        user, server = require_editor(request, guild_id)
        form = collect_form(
            name,
            trigger_text,
            enabled,
            provider_1,
            product_id_1,
            lookup_type_1,
            display_name_1,
            provider_2,
            product_id_2,
            lookup_type_2,
            display_name_2,
            provider_3,
            product_id_3,
            lookup_type_3,
            display_name_3,
            audio_asset_id,
            volume_override,
        )
        errors = validate_form(form)
        if errors:
            return render_form(templates, request, server, guild_id, form, "edit", errors, shortcut_id=shortcut_id, status_code=400)
        save_shortcut(guild_id, form, shortcut_id)
        return RedirectResponse(url="/guilds/{0}/mention-shortcuts?message=updated".format(guild_id), status_code=303)

    @router.post("/guilds/{guild_id}/mention-shortcuts/{shortcut_id}/toggle")
    async def toggle_shortcut(request: Request, guild_id: str, shortcut_id: int):
        user, server = require_editor(request, guild_id)
        with get_connection() as connection:
            repo = MentionShortcutRepository(connection, bot_id=current_selected_bot_id())
            shortcut = repo.get_shortcut(guild_id, shortcut_id)
            if shortcut is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="shortcut not found")
            repo.set_enabled(guild_id, shortcut_id, not bool(shortcut.get("enabled")))
            connection.commit()
        return RedirectResponse(url="/guilds/{0}/mention-shortcuts".format(guild_id), status_code=303)

    @router.post("/guilds/{guild_id}/mention-shortcuts/{shortcut_id}/delete")
    async def delete_shortcut(request: Request, guild_id: str, shortcut_id: int):
        user, server = require_editor(request, guild_id)
        with get_connection() as connection:
            MentionShortcutRepository(connection, bot_id=current_selected_bot_id()).delete_shortcut(guild_id, shortcut_id)
            connection.commit()
        return RedirectResponse(url="/guilds/{0}/mention-shortcuts?message=deleted".format(guild_id), status_code=303)


def require_access(request: Request, guild_id: str):
    user = get_current_user(request)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="login required")
    bot_id = selected_bot_id(request)
    if not can_access_guild(guild_id, user["user_id"], bot_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
    return user, find_server(guild_id, user["user_id"], bot_id)


def require_editor(request: Request, guild_id: str):
    user, server = require_access(request, guild_id)
    if not role_allows(server["role"], "editor"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="shortcut edit denied")
    return user, server


def default_form() -> Dict:
    return {
        "name": "",
        "trigger_text": "",
        "enabled": True,
        "targets": [{"provider": "steam", "provider_product_id": "", "lookup_type": "app_id", "display_name": "Steam"}],
        "audio_asset_id": "",
        "volume_override": "",
    }


def collect_form(name, trigger_text, enabled, *values) -> Dict:
    raw_targets = []
    for index in range(0, 12, 4):
        provider, product_id, lookup_type, display_name = values[index:index + 4]
        if str(provider or "").strip() or str(product_id or "").strip():
            raw_targets.append(
                {
                    "provider": str(provider or "").strip().lower(),
                    "provider_product_id": str(product_id or "").strip(),
                    "lookup_type": str(lookup_type or "").strip(),
                    "display_name": str(display_name or "").strip(),
                    "include_historical_low": True,
                    "enabled": True,
                }
            )
    return {
        "name": str(name or "").strip(),
        "trigger_text": str(trigger_text or "").strip(),
        "enabled": enabled == "on",
        "targets": raw_targets,
        "audio_asset_id": str(values[12] or "").strip() if len(values) > 12 else "",
        "volume_override": str(values[13] or "").strip() if len(values) > 13 else "",
    }


def validate_form(form: Dict) -> List[str]:
    errors = []
    if not form["name"]:
        errors.append("名前を入力してください。")
    if not form["trigger_text"]:
        errors.append("呼び出し文字を入力してください。")
    if not normalize_shortcut_trigger(form["trigger_text"]):
        errors.append("呼び出し文字が不正です。")
    if not form["targets"]:
        errors.append("価格対象を1件以上入力してください。")
    allowed_providers = {"steam", "itad", "ntprices"}
    for target in form["targets"]:
        if target["provider"] not in allowed_providers:
            errors.append("Providerは steam / itad / ntprices から選んでください。")
        if not target["provider_product_id"]:
            errors.append("Product IDを入力してください。")
    if form.get("volume_override"):
        try:
            value = int(form["volume_override"])
            if value < 0 or value > 100:
                errors.append("音量上書きは0～100で指定してください。")
        except ValueError:
            errors.append("音量上書きは数値で指定してください。")
    return errors


def save_shortcut(guild_id: str, form: Dict, shortcut_id: Optional[int] = None) -> None:
    with get_connection() as connection:
        repo = MentionShortcutRepository(connection, bot_id=current_selected_bot_id())
        shortcut = repo.upsert_shortcut(guild_id, form, shortcut_id)
        repo.replace_price_targets(int(shortcut["id"]), normalize_targets(form["targets"]))
        audio_actions = []
        if form.get("audio_asset_id"):
            audio_actions.append(
                {
                    "audio_asset_id": int(form["audio_asset_id"]),
                    "volume_override": int(form["volume_override"]) if form.get("volume_override") else None,
                    "enabled": True,
                }
            )
        repo.replace_audio_actions(int(shortcut["id"]), audio_actions)
        connection.commit()


def normalize_targets(targets: List[Dict]) -> List[Dict]:
    normalized = []
    for index, target in enumerate(targets, start=1):
        item = dict(target)
        item["sort_order"] = index * 10
        if item["provider"] == "steam" and not item.get("lookup_type"):
            item["lookup_type"] = "app_id"
        if item["provider"] == "itad" and not item.get("lookup_type"):
            item["lookup_type"] = "steam_app_id"
        if item["provider"] == "ntprices" and not item.get("lookup_type"):
            item["lookup_type"] = "ppid"
        normalized.append(item)
    return normalized


def load_shortcut_form(guild_id: str, shortcut_id: int) -> Optional[Dict]:
    with get_connection() as connection:
        repo = MentionShortcutRepository(connection, bot_id=current_selected_bot_id())
        shortcut = repo.get_shortcut(guild_id, shortcut_id)
        if shortcut is None:
            return None
        targets = repo.list_price_targets(shortcut_id)
        actions = repo.list_audio_actions(shortcut_id)
    form = default_form()
    form.update(shortcut)
    form["targets"] = targets or []
    if actions:
        form["audio_asset_id"] = str(actions[0].get("audio_asset_id") or "")
        form["volume_override"] = "" if actions[0].get("volume_override") is None else str(actions[0].get("volume_override"))
    return form


def provider_status_rows() -> List[Dict[str, str]]:
    return [
        {"provider": "Steam", "status": "利用可能"},
        {"provider": "IsThereAnyDeal", "status": "API key設定済み" if game_provider.itad_api_key() else "API key未設定"},
        {"provider": "NTPrices", "status": "API key設定済み / Region: {0}".format(game_provider.ntprices_region()) if game_provider.ntprices_api_key() else "API key未設定 / Region: {0}".format(game_provider.ntprices_region())},
    ]


def render_form(templates, request, server, guild_id, form, mode, errors=None, can_edit=True, shortcut_id=None, asset_id=None, status_code=200):
    with get_connection() as connection:
        audio_assets = AudioAssetRepository(connection, bot_id=current_selected_bot_id()).list_assets(guild_id, enabled=True)
    targets = list(form.get("targets") or [])
    while len(targets) < 3:
        targets.append({"provider": "", "provider_product_id": "", "lookup_type": "", "display_name": ""})
    form["targets"] = targets[:3]
    return templates.TemplateResponse(
        request,
        "mention_shortcut_form.html",
        {
            "user": get_current_user(request),
            "server": server,
            "guild_id": guild_id,
            "shortcut": form,
            "mode": mode,
            "errors": errors or [],
            "can_edit": can_edit,
            "shortcut_id": shortcut_id or asset_id,
            "audio_assets": audio_assets,
        },
        status_code=status_code,
    )
