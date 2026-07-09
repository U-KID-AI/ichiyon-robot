from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from admin.bot_context import selected_bot_id, set_selected_bot_id
from admin.auth import get_current_user
from bot.db import get_connection
from bot.repositories import FeatureFlagRepository, PermissionRepository, VoiceLineRepository


router = APIRouter()

ROLE_LEVELS = {
    "viewer": 1,
    "editor": 2,
    "guild_admin": 3,
    "global_admin": 4,
}

DISPLAY_FEATURES = [
    {
        "key": "mention_random_draw",
        "label": "ランダム抽選",
        "edit_path": "mention-reactions?kind=random_draw",
        "required_role": "editor",
        "overview": "候補からランダムに返す。",
        "settings": "呼び出しワード、抽選候補、出やすさ、画像、リアクション",
        "off_behavior": "OFFにすると、ランダム抽選だけを止める。",
        "notes": "名言・おみくじ・お前も〇〇よな？など。",
    },
    {
        "key": "mention_search",
        "label": "検索",
        "edit_path": "mention-reactions?kind=search",
        "required_role": "editor",
        "overview": "入力内容で検索して返す。",
        "settings": "呼び出しワード、検索設定、デッキ検索設定",
        "off_behavior": "OFFにすると、検索だけを止める。",
        "notes": "デッキ検索など。",
    },
    {
        "key": "mention_limited",
        "label": "限定機能",
        "edit_path": "mention-reactions/limited",
        "required_role": "editor",
        "overview": "特定ユーザーに追加効果を付ける。",
        "settings": "対象ユーザーID、特殊効果タグ、有効/無効",
        "off_behavior": "OFFにすると、限定機能だけを止める。",
        "notes": "限定タグはDB backend時のみ実行。",
    },
    {
        "key": "reactions",
        "label": "自動反応",
        "edit_path": "auto-reactions",
        "required_role": "editor",
        "overview": "特定の言葉に自動で返答。",
        "settings": "トリガー、返答、画像、絵文字、優先度、有効/無効",
        "off_behavior": "OFFにすると、自動反応を実行しない。",
        "notes": "NGワード検知時は通常反応を止める。",
    },
    {
        "key": "ng_words",
        "label": "NGワード",
        "edit_path": "ng-words",
        "required_role": "editor",
        "overview": "反応を止める言葉を設定。",
        "settings": "NGワード、有効/無効、特殊効果タグ",
        "off_behavior": "OFFにすると、NGワード判定を使わない。",
        "notes": "検知時はメンション反応・自動反応を止める。",
    },
    {
        "key": "modes",
        "label": "モード",
        "edit_path": "modes",
        "required_role": "editor",
        "overview": "一時的な特殊状態を設定。",
        "settings": "発動条件、返答候補、終了条件、見た目、通知",
        "off_behavior": "OFFにすると、モード抽選やモード中挙動を使わない。",
        "notes": "モード中は他機能を止める設定あり。",
    },
    {
        "key": "auto_posts",
        "label": "自動投稿",
        "edit_path": "auto-posts",
        "required_role": "editor",
        "overview": "決まった日時の投稿を設定。",
        "settings": "投稿名、本文、画像、チャンネル、スケジュール、有効/無効",
        "off_behavior": "OFFにすると、自動投稿を実行しない。",
        "notes": "投稿済み履歴で二重投稿を防止。",
    },
    {
        "key": "x_updates",
        "label": "X更新通知",
        "edit_path": "x-updates",
        "required_role": "guild_admin",
        "overview": "登録したXアカウントの新着を通知。",
        "settings": "Xユーザー名、通知先、対象投稿、必須/除外ワード、確認間隔、投稿文",
        "off_behavior": "OFFにすると、X更新通知を実行しない。",
        "notes": "初回は最新Post IDだけ保存し、過去投稿は流さない。",
    },
    {
        "key": "special_effect_tags",
        "label": "特殊効果タグ",
        "edit_path": "special-effects",
        "required_role": "editor",
        "overview": "追加投稿やカウント変更を設定。",
        "settings": "タグ名、対象、効果の種類、詳細設定、最大倍率、有効/無効",
        "off_behavior": "OFFにすると、特殊効果タグを適用しない。",
        "notes": "メンション反応本体とモードには直接付けない。",
    },
    {
        "key": "reaction_thresholds",
        "label": "リアクション返信",
        "edit_path": "reaction-thresholds",
        "required_role": "editor",
        "overview": "同じリアクションが集まった時に返答。",
        "settings": "しきい値、返答元、対象チャンネル、対象絵文字、除外条件",
        "off_behavior": "OFFにすると、リアクション数による返信を行わない。",
        "notes": "同じメッセージと絵文字の組み合わせでは重複返信しない。",
    },
    {
        "key": "voice_lines",
        "label": "入室時・復活時セリフ",
        "edit_path": "voice-lines",
        "required_role": "editor",
        "overview": "Botの入室時・復活時に使うセリフを設定します。",
        "settings": "Bot別、サーバー別の入室セリフと復活セリフ、有効/無効",
        "off_behavior": "OFFにすると、この設定からのセリフ送信を止めます。",
        "notes": "未設定のいちよんロボは既存の復活セリフを維持します。",
    },
    {
        "key": "schedule_templates",
        "label": "スケジュール募集",
        "edit_path": "schedule-templates",
        "required_role": "editor",
        "overview": "メンションコマンドで日別の募集投稿を作成します。",
        "settings": "テンプレート名、1〜14日目の本文、有効/無効",
        "off_behavior": "OFFにすると、スケジュール募集コマンドを実行しません。",
        "notes": "投稿先はコマンドを実行したチャンネルです。各投稿に⭕/❌を付けます。",
    },
]


def register_server_routes(templates: Jinja2Templates) -> None:
    @router.get("/servers")
    async def servers_page(request: Request):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)

        bot_id = selected_bot_id(request)
        servers = list_manageable_servers(user["user_id"], bot_id)
        return templates.TemplateResponse(
            request,
            "servers.html",
            {
                "user": user,
                "servers": servers,
                "bot_id": bot_id,
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

        bot_id = selected_bot_id(request)
        if not can_access_guild(guild_id, user["user_id"], bot_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

        server = find_server(guild_id, user["user_id"], bot_id)
        features = build_feature_rows(guild_id, server["role"], bot_id)
        return templates.TemplateResponse(
            request,
            "guild_top.html",
            {
                "user": user,
                "server": server,
                "guild_id": guild_id,
                "features": features,
                "bot_id": bot_id,
                "can_edit_any": role_allows(server["role"], "editor"),
            },
        )

    @router.get("/guilds/{guild_id}/features/mention_reactions")
    async def redirect_legacy_mention_reactions_feature(
        request: Request,
        guild_id: str,
        kind: Optional[str] = None,
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)

        bot_id = selected_bot_id(request)
        if not can_access_guild(guild_id, user["user_id"], bot_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

        return RedirectResponse(url=legacy_mention_feature_redirect_url(guild_id, kind), status_code=303)

    @router.post("/guilds/{guild_id}/features/{feature_key}/toggle")
    async def toggle_feature(
        request: Request,
        guild_id: str,
        feature_key: str,
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)

        bot_id = selected_bot_id(request)
        if not can_access_guild(guild_id, user["user_id"], bot_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

        server = find_server(guild_id, user["user_id"], bot_id)
        feature = get_feature_definition(feature_key)
        if feature is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="feature not found")

        if not role_allows(server["role"], feature["required_role"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="feature toggle denied")

        with get_connection() as connection:
            repository = FeatureFlagRepository(connection, bot_id=bot_id)
            repository.toggle_flag(
                guild_id,
                feature_key,
                default=True,
                updated_by_discord_user_id=user["user_id"],
            )
            connection.commit()

        return RedirectResponse(url="/guilds/{0}".format(guild_id), status_code=303)


def list_manageable_servers(discord_user_id: str, bot_id: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_connection() as connection:
        repository = PermissionRepository(connection)
        if bot_id:
            return repository.list_manageable_guilds_for_bot(bot_id, discord_user_id)
        return repository.list_manageable_guilds(discord_user_id)


def can_access_guild(guild_id: str, discord_user_id: str, bot_id: Optional[str] = None) -> bool:
    with get_connection() as connection:
        repository = PermissionRepository(connection)
        if bot_id:
            return repository.can_access_bot_guild(bot_id, guild_id, discord_user_id)
        return repository.can_access_guild(guild_id, discord_user_id)


def find_server(guild_id: str, discord_user_id: str, bot_id: Optional[str] = None) -> Dict[str, Any]:
    for server in list_manageable_servers(discord_user_id, bot_id):
        if server["guild_id"] == guild_id:
            return server
    return {"guild_id": guild_id, "name": guild_id, "icon_url": None, "role": ""}


def get_feature_definition(feature_key: str) -> Optional[Dict[str, Any]]:
    for feature in DISPLAY_FEATURES:
        if feature["key"] == feature_key:
            return feature
    return None


def legacy_mention_feature_redirect_url(guild_id: str, kind: Optional[str] = None) -> str:
    if kind == "random_draw":
        return "/guilds/{0}/mention-reactions?kind=random_draw".format(guild_id)
    if kind == "search":
        return "/guilds/{0}/mention-reactions?kind=search".format(guild_id)
    if kind == "limited":
        return "/guilds/{0}/mention-reactions/limited".format(guild_id)
    return "/guilds/{0}".format(guild_id)


def role_allows(role: str, required_role: str) -> bool:
    return ROLE_LEVELS.get(role, 0) >= ROLE_LEVELS.get(required_role, 0)


def build_feature_rows(
    guild_id: str,
    role: str,
    bot_id: str = "ichiyon",
) -> List[Dict[str, Any]]:
    with get_connection() as connection:
        repository = FeatureFlagRepository(connection, bot_id=bot_id)
        flags = {
            flag["feature_key"]: bool(flag["enabled"])
            for flag in repository.list_flags(guild_id)
        }

    rows = []
    for feature in DISPLAY_FEATURES:
        row = dict(feature)
        if feature["key"] == "voice_lines":
            with get_connection() as connection:
                voice_line = VoiceLineRepository(connection).get(bot_id, guild_id)
            row["enabled"] = True if voice_line is None else bool(voice_line.get("enabled"))
        else:
            row["enabled"] = flags.get(feature["key"], True)
        row["can_toggle"] = role_allows(role, feature["required_role"])
        row["edit_url"] = "/guilds/{0}/{1}".format(guild_id, feature["edit_path"])
        if feature["key"] == "voice_lines":
            row["toggle_url"] = "/guilds/{0}/voice-lines/toggle".format(guild_id)
        else:
            row["toggle_url"] = "/guilds/{0}/features/{1}/toggle".format(guild_id, feature["key"])
        rows.append(row)

    return rows
