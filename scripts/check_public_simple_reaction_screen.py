import asyncio
import sys
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, UploadFile
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from admin import public
from admin.ux import save_uploaded_image


class FakeConnection:
    committed = False
    rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class FakeMentionReactionRepository:
    has_omikuji = True
    choices: List[Dict[str, Any]] = [
        {
            "id": 1,
            "body": "大吉",
            "image_path": "",
            "enabled": True,
            "appearance_rate": 100,
            "effect_config_json": {"hidden": True},
        },
        {
            "id": 2,
            "body": "中吉",
            "image_path": "assets/images/mention_reaction_choices/sample.png",
            "enabled": True,
            "appearance_rate": 1,
        },
        {
            "id": 3,
            "body": "無効",
            "image_path": "",
            "enabled": False,
            "appearance_rate": 1,
        },
    ]
    created_choices: List[Dict[str, Any]] = []

    def __init__(self, connection, bot_id: Optional[str] = None) -> None:
        self.bot_id = bot_id

    def list_reactions(self, guild_id: str, enabled=None, reaction_kind=None):
        if not self.has_omikuji:
            return []
        assert self.bot_id == public.PUBLIC_BOT_ID
        assert guild_id == public.PUBLIC_GUILD_ID
        assert enabled is True
        assert reaction_kind == "random_draw"
        return [
            {
                "id": 100,
                "guild_id": guild_id,
                "reaction_key": "omikuji",
                "name": "おみくじ",
                "keyword": "おみくじ",
                "enabled": True,
            }
        ]

    def list_choices(self, guild_id: str, mention_reaction_id: int, enabled=None):
        assert self.bot_id == public.PUBLIC_BOT_ID
        assert guild_id == public.PUBLIC_GUILD_ID
        assert mention_reaction_id == 100
        assert enabled is True
        return [choice for choice in self.choices if choice.get("enabled")]

    def create_choice(self, **kwargs):
        assert self.bot_id == public.PUBLIC_BOT_ID
        assert kwargs["guild_id"] == public.PUBLIC_GUILD_ID
        assert kwargs["mention_reaction_id"] == 100
        self.created_choices.append({"bot_id": self.bot_id, **kwargs})
        self.choices.append(
            {
                "id": 1000 + len(self.created_choices),
                "body": kwargs.get("body"),
                "image_path": kwargs.get("image_path") or "",
                "enabled": kwargs.get("enabled"),
                "appearance_rate": kwargs.get("appearance_rate"),
            }
        )
        return self.created_choices[-1]


def patch_public() -> None:
    public.get_connection = lambda: FakeConnection()
    public.MentionReactionRepository = FakeMentionReactionRepository


def build_test_client() -> TestClient:
    patch_public()
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=ROOT / "admin" / "static"), name="static")
    app.mount("/assets", StaticFiles(directory=ROOT / "assets"), name="assets")
    templates = Jinja2Templates(directory=ROOT / "admin" / "templates")
    public.register_public_routes(templates)
    app.include_router(public.router)
    return TestClient(app)


def assert_constants_are_fixed() -> None:
    assert public.PUBLIC_BOT_ID == "ichiyon"
    assert public.PUBLIC_GUILD_ID == "1392174489609179327"


def assert_form_validation() -> None:
    assert not public.validate_omikuji_form("大吉", False)
    assert not public.validate_omikuji_form("", True)
    assert public.validate_omikuji_form("", False)
    assert public.validate_omikuji_form("x" * 501, False)


async def assert_invalid_image_rejected_by_helper() -> None:
    upload = UploadFile(filename="bad.exe", file=BytesIO(b"not image"))
    path, error = await save_uploaded_image(upload, "public_check")
    assert path is None
    assert error


def assert_get_public_without_login() -> None:
    FakeMentionReactionRepository.has_omikuji = True
    FakeMentionReactionRepository.created_choices.clear()
    client = build_test_client()
    response = client.get("/public", follow_redirects=False)
    assert response.status_code == 200
    assert "Discordでログイン" not in response.text
    assert "サーバーを選択" not in response.text
    assert "Botを選択" not in response.text
    assert "現在のおみくじ" in response.text
    assert "新しいおみくじを追加" in response.text
    assert "大吉" in response.text
    assert "中吉" in response.text
    assert "無効" not in response.text
    assert "sample.png" not in response.text


def assert_public_html_hides_internal_terms() -> None:
    client = build_test_client()
    response = client.get("/public")
    forbidden_terms = [
        "Discordでログイン",
        "特殊効果",
        "effect_config_json",
        "weight",
        "bot_id",
        "guild_id",
        "target_type",
        "feature flag",
        "counter",
        "variable",
        "変数",
        "サーバーを選択",
        "Botを選択",
    ]
    lowered = response.text.lower()
    for term in forbidden_terms:
        assert term.lower() not in lowered, "{0} leaked".format(term)


def assert_text_only_adds_choice() -> None:
    FakeMentionReactionRepository.has_omikuji = True
    FakeMentionReactionRepository.created_choices.clear()
    client = build_test_client()
    response = client.post(
        "/public",
        data={"action": "add", "body": "何吉", "bot_id": "irsia", "guild_id": "evil"},
    )
    assert response.status_code == 200
    assert "追加しました。" in response.text
    created = FakeMentionReactionRepository.created_choices[-1]
    assert created["bot_id"] == public.PUBLIC_BOT_ID
    assert created["guild_id"] == public.PUBLIC_GUILD_ID
    assert created["body"] == "何吉"
    assert created["image_path"] is None
    assert created["appearance_rate"] == 1
    assert created["enabled"] is True
    assert "何吉" in response.text


def assert_image_only_adds_choice() -> None:
    FakeMentionReactionRepository.created_choices.clear()
    original_save = public.save_uploaded_image
    public.save_uploaded_image = lambda upload, category: _fake_save_uploaded_image("assets/images/mention_reaction_choices/fake.png", None)
    try:
        client = build_test_client()
        response = client.post(
            "/public",
            data={"action": "add", "body": ""},
            files={"image_upload": ("ok.png", b"image", "image/png")},
        )
    finally:
        public.save_uploaded_image = original_save
    assert response.status_code == 200
    created = FakeMentionReactionRepository.created_choices[-1]
    assert created["body"] is None
    assert created["name"] == "画像おみくじ"
    assert created["image_path"] == "assets/images/mention_reaction_choices/fake.png"


async def _fake_save_uploaded_image(path: Optional[str], error: Optional[str]):
    return path, error


def assert_empty_body_and_image_rejected() -> None:
    client = build_test_client()
    response = client.post("/public", data={"action": "add", "body": ""})
    assert response.status_code == 400
    assert "おみくじの内容または画像を入力してください。" in response.text


def assert_bad_image_rejected() -> None:
    original_save = public.save_uploaded_image
    public.save_uploaded_image = lambda upload, category: _fake_save_uploaded_image(None, "bad image")
    try:
        client = build_test_client()
        response = client.post(
            "/public",
            data={"action": "add", "body": ""},
            files={"image_upload": ("bad.exe", b"bad", "application/octet-stream")},
        )
    finally:
        public.save_uploaded_image = original_save
    assert response.status_code == 400
    assert "画像は png, jpg, jpeg, gif, webp の8MB以内でアップロードしてください。" in response.text


def assert_missing_omikuji_does_not_create() -> None:
    FakeMentionReactionRepository.has_omikuji = False
    FakeMentionReactionRepository.created_choices.clear()
    client = build_test_client()
    get_response = client.get("/public")
    assert "現在おみくじを追加できません。" in get_response.text
    response = client.post("/public", data={"action": "add", "body": "大吉"})
    assert response.status_code == 400
    assert "現在おみくじを追加できません。" in response.text
    assert not FakeMentionReactionRepository.created_choices
    FakeMentionReactionRepository.has_omikuji = True


def assert_preview_does_not_create() -> None:
    FakeMentionReactionRepository.created_choices.clear()
    client = build_test_client()
    response = client.post("/public", data={"action": "preview", "body": "プレビュー吉"})
    assert response.status_code == 200
    assert "プレビュー吉" in response.text
    assert not FakeMentionReactionRepository.created_choices


def assert_removed_public_templates_are_gone() -> None:
    removed = [
        "public_login.html",
        "public_select.html",
        "public_form.html",
        "public_denied.html",
    ]
    for name in removed:
        assert not (ROOT / "admin" / "templates" / name).exists(), name


def assert_admin_auth_has_no_public_oauth() -> None:
    text = (ROOT / "admin" / "auth.py").read_text(encoding="utf-8")
    assert "public_discord_user" not in text
    assert "identify guilds" not in text
    assert "/public/login" not in text


def main() -> None:
    assert_constants_are_fixed()
    assert_form_validation()
    asyncio.run(assert_invalid_image_rejected_by_helper())
    assert_get_public_without_login()
    assert_public_html_hides_internal_terms()
    assert_text_only_adds_choice()
    assert_image_only_adds_choice()
    assert_empty_body_and_image_rejected()
    assert_bad_image_rejected()
    assert_missing_omikuji_does_not_create()
    assert_preview_does_not_create()
    assert_removed_public_templates_are_gone()
    assert_admin_auth_has_no_public_oauth()
    print("public omikuji-only screen checks ok")


if __name__ == "__main__":
    main()
