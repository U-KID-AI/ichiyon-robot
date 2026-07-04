from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from admin.auth import get_current_user
from bot.db import get_connection
from bot.repositories import FeatureFlagRepository, PermissionRepository


router = APIRouter()

FEATURES = [
    {
        "key": "mention_reactions",
        "label": "メンション反応",
        "edit_path": "mention-reactions",
        "required_role": "editor",
        "overview": "Botへのメンションを起点に、ランダム抽選や検索型の返答を管理。",
        "settings": "キーワード、種類、抽選候補、出やすさ、有効/無効",
        "off_behavior": "OFFにすると、このサーバーではメンション反応を使わない。",
        "notes": "デッキ検索はここでは表示せず、後続で「メンション反応 > 検索 > デッキ検索」に配置。",
    },
    {
        "key": "reactions",
        "label": "自動反応",
        "edit_path": "auto-reactions",
        "required_role": "editor",
        "overview": "通常投稿のトリガーに反応する自動返答を管理。",
        "settings": "トリガー、返答、画像、絵文字、優先度、有効/無効",
        "off_behavior": "OFFにすると、自動反応を実行しない想定。",
        "notes": "優先度とトリガー長を考慮した判定はBot反映Phaseで扱う。",
    },
    {
        "key": "ng_words",
        "label": "NGワード",
        "edit_path": "ng-words",
        "required_role": "editor",
        "overview": "通常反応を止めたいワードを管理。",
        "settings": "NGワード、有効/無効、特殊効果タグ",
        "off_behavior": "OFFにすると、NGワード判定を使わない想定。",
        "notes": "特殊効果タグ付与は後続Phaseで実装。",
    },
    {
        "key": "modes",
        "label": "モード",
        "edit_path": "modes",
        "required_role": "editor",
        "overview": "モードを管理。",
        "settings": "発動条件、クールタイム、返答候補、通知、見た目、チャンネル",
        "off_behavior": "OFFにすると、モード抽選やモード中挙動を使わない想定。",
        "notes": "モード中は他の機能を使わない設計。",
    },
    {
        "key": "auto_posts",
        "label": "自動投稿",
        "edit_path": "auto-posts",
        "required_role": "editor",
        "overview": "日付・スケジュール投稿を管理。",
        "settings": "投稿名、本文、画像、投稿チャンネル、スケジュール、有効/無効",
        "off_behavior": "OFFにすると、自動投稿を実行しない想定。",
        "notes": "投稿済み管理は後続Phaseで扱う。",
    },
    {
        "key": "x_updates",
        "label": "X更新通知",
        "edit_path": "x-updates",
        "required_role": "guild_admin",
        "overview": "登録したXアカウントの新規投稿をDiscordへ通知。",
        "settings": "Xユーザー名、通知チャンネル、返信/リポスト/引用、確認間隔、投稿文",
        "off_behavior": "OFFにすると、このサーバーではX更新通知を実行しない。",
        "notes": "初回は最新投稿IDだけ保存し、過去投稿は流さない。",
    },
    {
        "key": "special_effect_tags",
        "label": "特殊効果タグ",
        "edit_path": "special-effects",
        "required_role": "editor",
        "overview": "抽選候補、自動反応、NGワードへ付与する追加効果を管理。",
        "settings": "タグ名、色、管理者限定、効果タイプ、効果設定、追加投稿テキスト",
        "off_behavior": "OFFにすると、特殊効果タグを適用しない想定。",
        "notes": "メンション反応本体とモードには付与しない。",
    },
    {
        "key": "reaction_thresholds",
        "label": "リアクション返信",
        "edit_path": "reaction-thresholds",
        "required_role": "editor",
        "overview": "同じリアクションが一定数ついた時の返信を設定。",
        "settings": "しきい値、返信文、対象チャンネル、対象絵文字、除外条件",
        "off_behavior": "OFFにすると、リアクション数による返信を行わない。",
        "notes": "同じメッセージと絵文字の組み合わせでは重複返信しない。",
    },
]

ROLE_LEVELS = {
    "viewer": 1,
    "editor": 2,
    "guild_admin": 3,
    "global_admin": 4,
}


def register_server_routes(templates: Jinja2Templates) -> None:
    @router.get("/servers")
    async def servers_page(request: Request):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)

        servers = list_manageable_servers(user["user_id"])
        return templates.TemplateResponse(
            request,
            "servers.html",
            {
                "user": user,
                "servers": servers,
            },
        )

    @router.get("/guilds/{guild_id}")
    async def guild_top(
        request: Request,
        guild_id: str,
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)

        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

        server = find_server(guild_id, user["user_id"])
        features = build_feature_rows(guild_id, server["role"])
        return templates.TemplateResponse(
            request,
            "guild_top.html",
            {
                "user": user,
                "server": server,
                "guild_id": guild_id,
                "features": features,
                "can_edit_any": role_allows(server["role"], "editor"),
            },
        )

    @router.post("/guilds/{guild_id}/features/{feature_key}/toggle")
    async def toggle_feature(
        request: Request,
        guild_id: str,
        feature_key: str,
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)

        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

        server = find_server(guild_id, user["user_id"])
        feature = get_feature_definition(feature_key)
        if feature is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="feature not found")

        if not role_allows(server["role"], feature["required_role"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="feature toggle denied")

        with get_connection() as connection:
            repository = FeatureFlagRepository(connection)
            repository.toggle_flag(
                guild_id,
                feature_key,
                default=True,
                updated_by_discord_user_id=user["user_id"],
            )
            connection.commit()

        return RedirectResponse(url="/guilds/{0}".format(guild_id), status_code=303)


def list_manageable_servers(discord_user_id: str) -> List[Dict[str, Any]]:
    with get_connection() as connection:
        repository = PermissionRepository(connection)
        return repository.list_manageable_guilds(discord_user_id)


def can_access_guild(guild_id: str, discord_user_id: str) -> bool:
    with get_connection() as connection:
        repository = PermissionRepository(connection)
        return repository.can_access_guild(guild_id, discord_user_id)


def find_server(guild_id: str, discord_user_id: str) -> Dict[str, Any]:
    for server in list_manageable_servers(discord_user_id):
        if server["guild_id"] == guild_id:
            return server
    return {"guild_id": guild_id, "name": guild_id, "icon_url": None, "role": ""}


def get_feature_definition(feature_key: str) -> Optional[Dict[str, Any]]:
    for feature in FEATURES:
        if feature["key"] == feature_key:
            return feature
    return None


def role_allows(role: str, required_role: str) -> bool:
    return ROLE_LEVELS.get(role, 0) >= ROLE_LEVELS.get(required_role, 0)


def build_feature_rows(
    guild_id: str,
    role: str,
) -> List[Dict[str, Any]]:
    with get_connection() as connection:
        repository = FeatureFlagRepository(connection)
        flags = {
            flag["feature_key"]: bool(flag["enabled"])
            for flag in repository.list_flags(guild_id)
        }

    rows = []
    for feature in FEATURES:
        row = dict(feature)
        row["enabled"] = flags.get(feature["key"], True)
        row["can_toggle"] = role_allows(role, feature["required_role"])
        row["edit_url"] = "/guilds/{0}/{1}".format(guild_id, feature["edit_path"])
        row["toggle_url"] = "/guilds/{0}/features/{1}/toggle".format(guild_id, feature["key"])
        rows.append(row)

    return rows
