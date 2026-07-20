import asyncio
import sys
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List

from fastapi import UploadFile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from admin import auth
from admin import public
from admin.ux import save_uploaded_image


class FakeRequest:
    def __init__(self, session: Dict[str, Any]) -> None:
        self.session = session


class FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def commit(self):
        pass

    def rollback(self):
        pass


class FakePermissionRepository:
    def __init__(self, connection) -> None:
        self.connection = connection

    def list_public_bot_guilds(self, guild_ids: List[str]) -> List[Dict[str, Any]]:
        scopes = [
            {
                "bot_id": "ichiyon",
                "bot_display_name": "いちよんロボ",
                "guild_id": "guild-a",
                "guild_name": "ランセ地方",
            },
            {
                "bot_id": "irsia",
                "bot_display_name": "イルシア",
                "guild_id": "guild-b",
                "guild_name": "神聖イルシア皇国",
            },
        ]
        return [scope for scope in scopes if scope["guild_id"] in guild_ids]


class FakeAutoReactionRepository:
    created: List[Dict[str, Any]] = []

    def __init__(self, connection, bot_id=None) -> None:
        self.bot_id = bot_id

    def create_reaction(self, **kwargs):
        row = {"bot_id": self.bot_id, **kwargs}
        self.created.append(row)
        return row


class FakeMentionReactionRepository:
    created_reactions: List[Dict[str, Any]] = []
    created_choices: List[Dict[str, Any]] = []

    def __init__(self, connection, bot_id=None) -> None:
        self.bot_id = bot_id

    def create_reaction(self, **kwargs):
        row = {"bot_id": self.bot_id, "id": 100, **kwargs}
        self.created_reactions.append(row)
        return row

    def create_choice(self, **kwargs):
        row = {"bot_id": self.bot_id, **kwargs}
        self.created_choices.append(row)
        return row

    def list_reactions(self, guild_id, enabled=None, reaction_kind=None):
        return [
            {
                "id": 201,
                "bot_id": self.bot_id,
                "guild_id": guild_id,
                "reaction_key": "omikuji",
                "name": "おみくじ",
                "keyword": "おみくじ",
            },
            {
                "id": 202,
                "bot_id": self.bot_id,
                "guild_id": guild_id,
                "reaction_key": "quote",
                "name": "名言",
                "keyword": "",
            },
        ]


def patch_public_repositories() -> None:
    public.get_connection = lambda: FakeConnection()
    public.PermissionRepository = FakePermissionRepository
    public.AutoReactionRepository = FakeAutoReactionRepository
    public.MentionReactionRepository = FakeMentionReactionRepository


def assert_public_session_does_not_require_admin() -> None:
    request = FakeRequest(
        {
            auth.SESSION_PUBLIC_USER_KEY: {"user_id": "member-1", "username": "member"},
            auth.SESSION_PUBLIC_GUILD_IDS_KEY: ["guild-a"],
        }
    )
    assert auth.get_public_user(request)["user_id"] == "member-1"
    assert auth.get_public_guild_ids(request) == ["guild-a"]


def assert_scope_selection_is_server_side() -> None:
    patch_public_repositories()
    request = FakeRequest(
        {
            auth.SESSION_PUBLIC_USER_KEY: {"user_id": "member-1", "username": "member"},
            auth.SESSION_PUBLIC_GUILD_IDS_KEY: ["guild-a"],
            public.PUBLIC_SELECTED_BOT_KEY: "irsia",
            public.PUBLIC_SELECTED_GUILD_KEY: "guild-b",
        }
    )
    assert public.selected_public_scope(request) is None
    assert public.PUBLIC_SELECTED_BOT_KEY not in request.session
    scopes = public.public_scopes_for_request(request)
    assert len(scopes) == 1
    public.set_selected_public_scope(request, scopes[0])
    assert public.selected_public_scope(request)["bot_display_name"] == "いちよんロボ"


def assert_form_validation() -> None:
    assert public.validate_public_form("normal", "", "返事", False)
    assert public.validate_public_form("normal", "hello", "", False)
    assert not public.validate_public_form("normal", "hello", "", True)
    assert not public.validate_public_form("quote", "", "名言です", False)
    assert not public.validate_public_form("quote", "", "", True)
    assert public.validate_public_form("quote", "", "", False)


async def assert_invalid_image_rejected() -> None:
    upload = UploadFile(filename="bad.exe", file=BytesIO(b"not image"))
    path, error = await save_uploaded_image(upload, "public_check")
    assert path is None
    assert error


def assert_create_uses_selected_scope_only() -> None:
    patch_public_repositories()
    FakeAutoReactionRepository.created.clear()
    FakeMentionReactionRepository.created_reactions.clear()
    FakeMentionReactionRepository.created_choices.clear()

    ok, message = public.create_public_entry(
        FakeConnection(),
        bot_id="ichiyon",
        guild_id="guild-a",
        entry_type="normal",
        trigger_text="こんにちは",
        body="やあ",
        image_path=None,
    )
    assert ok and message is None
    assert FakeAutoReactionRepository.created[-1]["bot_id"] == "ichiyon"
    assert FakeAutoReactionRepository.created[-1]["guild_id"] == "guild-a"
    assert FakeAutoReactionRepository.created[-1]["enabled"] is True
    assert FakeAutoReactionRepository.created[-1]["priority"] == 0

    ok, message = public.create_public_entry(
        FakeConnection(),
        bot_id="ichiyon",
        guild_id="guild-a",
        entry_type="mention",
        trigger_text="呼んだ",
        body="返事",
        image_path=None,
    )
    assert ok and message is None
    assert FakeMentionReactionRepository.created_reactions[-1]["bot_id"] == "ichiyon"
    assert FakeMentionReactionRepository.created_reactions[-1]["guild_id"] == "guild-a"
    assert FakeMentionReactionRepository.created_reactions[-1]["enabled"] is True
    assert FakeMentionReactionRepository.created_reactions[-1]["is_deletable"] is True
    assert FakeMentionReactionRepository.created_choices[-1]["appearance_rate"] == 1

    ok, message = public.create_public_entry(
        FakeConnection(),
        bot_id="ichiyon",
        guild_id="guild-a",
        entry_type="omikuji",
        trigger_text="ignored",
        body="大吉",
        image_path=None,
    )
    assert ok and message is None
    assert FakeMentionReactionRepository.created_choices[-1]["mention_reaction_id"] == 201

    ok, message = public.create_public_entry(
        FakeConnection(),
        bot_id="ichiyon",
        guild_id="guild-a",
        entry_type="quote",
        trigger_text="ignored",
        body="いい言葉",
        image_path=None,
    )
    assert ok and message is None
    assert FakeMentionReactionRepository.created_choices[-1]["mention_reaction_id"] == 202


def assert_public_templates_hide_internal_terms() -> None:
    forbidden_terms = [
        "effect_config_json",
        "target_type",
        "feature flag",
        "weight",
        "bot_id",
        "guild_id",
        "特殊効果",
        "変数",
    ]
    for template in (ROOT / "admin" / "templates").glob("public_*.html"):
        text = template.read_text(encoding="utf-8")
        lowered = text.lower()
        for term in forbidden_terms:
            assert term.lower() not in lowered, "{0} leaked in {1}".format(term, template.name)


def assert_public_router_registered() -> None:
    main_text = (ROOT / "admin" / "main.py").read_text(encoding="utf-8")
    assert "register_public_routes" in main_text
    assert "public_router" in main_text


def main() -> None:
    assert_public_session_does_not_require_admin()
    assert_scope_selection_is_server_side()
    assert_form_validation()
    asyncio.run(assert_invalid_image_rejected())
    assert_create_uses_selected_scope_only()
    assert_public_templates_hide_internal_terms()
    assert_public_router_registered()
    print("public simple reaction screen checks ok")


if __name__ == "__main__":
    main()
