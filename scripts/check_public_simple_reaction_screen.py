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
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def commit(self):
        pass

    def rollback(self):
        pass


class FakeMentionReactionRepository:
    has_omikuji = True
    choices: List[Dict[str, Any]] = []
    created_choices: List[Dict[str, Any]] = []

    def __init__(self, connection, bot_id: Optional[str] = None) -> None:
        self.bot_id = bot_id

    @classmethod
    def reset(cls) -> None:
        cls.has_omikuji = True
        cls.created_choices = []
        cls.choices = [
            {
                "id": 1,
                "result_label": "大吉",
                "body": "今日は良いことが起こるでしょう",
                "image_path": "",
                "enabled": True,
                "appearance_rate": 100,
                "effect_config_json": {"hidden": True},
            },
            {
                "id": 2,
                "result_label": "",
                "body": "忘れ物に注意しましょう",
                "image_path": "",
                "enabled": True,
                "appearance_rate": 1,
            },
            {
                "id": 3,
                "result_label": "画像吉",
                "body": "",
                "image_path": "assets/images/mention_reaction_choices/sample.png",
                "enabled": True,
                "appearance_rate": 1,
            },
            {
                "id": 4,
                "result_label": "無効吉",
                "body": "表示しない",
                "image_path": "",
                "enabled": False,
                "appearance_rate": 1,
            },
        ]

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
                "result_label": kwargs.get("result_label") or "",
                "body": kwargs.get("body") or "",
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


def response_text() -> str:
    FakeMentionReactionRepository.reset()
    client = build_test_client()
    response = client.get("/public", follow_redirects=False)
    assert response.status_code == 200
    return response.text


def assert_constants_are_fixed() -> None:
    assert public.PUBLIC_BOT_ID == "ichiyon"
    assert public.PUBLIC_GUILD_ID == "1392174489609179327"


def assert_form_validation() -> None:
    assert not public.validate_omikuji_form("大吉", "今日は良い日", False)
    assert not public.validate_omikuji_form("", "今日は良い日", False)
    assert not public.validate_omikuji_form("大吉", "", True)
    assert not public.validate_omikuji_form("", "", True)
    assert public.validate_omikuji_form("大吉", "", False)
    assert public.validate_omikuji_form("", "", False)
    assert public.validate_omikuji_form("x" * 81, "本文", False)
    assert public.validate_omikuji_form("", "x" * 501, False)


async def assert_invalid_image_rejected_by_helper() -> None:
    upload = UploadFile(filename="bad.exe", file=BytesIO(b"not image"))
    path, error = await save_uploaded_image(upload, "public_check")
    assert path is None
    assert error


def assert_get_public_without_login_and_no_selector() -> None:
    text = response_text()
    assert "Discordでログイン" not in text
    assert "サーバーを選択" not in text
    assert "Botを選択" not in text
    assert "現在のおみくじ" in text
    assert "新しいおみくじを追加" in text


def assert_list_displays_result_label_and_body_separately() -> None:
    text = response_text()
    assert "大吉" in text
    assert "今日は良いことが起こるでしょう" in text
    assert text.index("大吉") < text.index("今日は良いことが起こるでしょう")
    assert "何吉なし" in text
    assert "忘れ物に注意しましょう" in text
    assert "画像吉" in text
    assert "画像のおみくじ" in text
    assert "無効吉" not in text
    assert "表示しない" not in text
    assert "sample.png" not in text


def assert_public_html_hides_internal_terms() -> None:
    text = response_text()
    forbidden_terms = [
        "result_label",
        "bot_id",
        "guild_id",
        "candidate ID",
        "random draw ID",
        "appearance_rate",
        "weight",
        "enabled",
        "特殊効果",
        "変数",
        "effect_config_json",
        "DB情報",
    ]
    lowered = text.lower()
    for term in forbidden_terms:
        assert term.lower() not in lowered, "{0} leaked".format(term)
    assert 'name="body"' not in lowered
    assert 'name="result_label"' not in lowered


def assert_text_and_label_adds_choice() -> None:
    FakeMentionReactionRepository.reset()
    client = build_test_client()
    response = client.post(
        "/public",
        data={
            "action": "add",
            "fortune_label": "ゲーム吉",
            "omikuji_text": "レアドロップが出るかも",
            "bot_id": "irsia",
            "guild_id": "evil",
        },
    )
    assert response.status_code == 200
    assert "追加しました。" in response.text
    created = FakeMentionReactionRepository.created_choices[-1]
    assert created["bot_id"] == public.PUBLIC_BOT_ID
    assert created["guild_id"] == public.PUBLIC_GUILD_ID
    assert created["result_label"] == "ゲーム吉"
    assert created["body"] == "レアドロップが出るかも"
    assert "ゲーム吉\nレアドロップ" not in str(created["body"])
    assert created["image_path"] is None
    assert created["appearance_rate"] == 1
    assert created["enabled"] is True
    assert "ゲーム吉" in response.text
    assert "レアドロップが出るかも" in response.text


def assert_body_only_adds_choice() -> None:
    FakeMentionReactionRepository.reset()
    client = build_test_client()
    response = client.post("/public", data={"action": "add", "fortune_label": "", "omikuji_text": "本文だけ"})
    assert response.status_code == 200
    created = FakeMentionReactionRepository.created_choices[-1]
    assert created["result_label"] is None
    assert created["body"] == "本文だけ"
    assert "何吉なし" in response.text
    assert "本文だけ" in response.text


def assert_image_only_variations_add_choice() -> None:
    original_save = public.save_uploaded_image
    public.save_uploaded_image = lambda upload, category: _fake_save_uploaded_image("assets/images/mention_reaction_choices/fake.png", None)
    try:
        FakeMentionReactionRepository.reset()
        client = build_test_client()
        response = client.post(
            "/public",
            data={"action": "add", "fortune_label": "画像吉", "omikuji_text": ""},
            files={"image_upload": ("ok.png", b"image", "image/png")},
        )
        assert response.status_code == 200
        created = FakeMentionReactionRepository.created_choices[-1]
        assert created["result_label"] == "画像吉"
        assert created["body"] is None
        assert created["name"] == "画像吉"
        assert created["image_path"] == "assets/images/mention_reaction_choices/fake.png"

        FakeMentionReactionRepository.reset()
        client = build_test_client()
        response = client.post(
            "/public",
            data={"action": "add", "fortune_label": "", "omikuji_text": ""},
            files={"image_upload": ("ok.png", b"image", "image/png")},
        )
        assert response.status_code == 200
        created = FakeMentionReactionRepository.created_choices[-1]
        assert created["result_label"] is None
        assert created["body"] is None
        assert created["name"] == "画像おみくじ"
    finally:
        public.save_uploaded_image = original_save


async def _fake_save_uploaded_image(path: Optional[str], error: Optional[str]):
    return path, error


def assert_label_only_and_empty_rejected() -> None:
    FakeMentionReactionRepository.reset()
    client = build_test_client()
    response = client.post("/public", data={"action": "add", "fortune_label": "大吉", "omikuji_text": ""})
    assert response.status_code == 400
    assert "おみくじの内容または画像を入力してください。" in response.text
    assert not FakeMentionReactionRepository.created_choices

    response = client.post("/public", data={"action": "add", "fortune_label": "", "omikuji_text": ""})
    assert response.status_code == 400
    assert "おみくじの内容または画像を入力してください。" in response.text
    assert not FakeMentionReactionRepository.created_choices


def assert_bad_image_rejected() -> None:
    original_save = public.save_uploaded_image
    public.save_uploaded_image = lambda upload, category: _fake_save_uploaded_image(None, "bad image")
    try:
        FakeMentionReactionRepository.reset()
        client = build_test_client()
        response = client.post(
            "/public",
            data={"action": "add", "fortune_label": "大吉", "omikuji_text": ""},
            files={"image_upload": ("bad.exe", b"bad", "application/octet-stream")},
        )
    finally:
        public.save_uploaded_image = original_save
    assert response.status_code == 400
    assert "画像は png, jpg, jpeg, gif, webp の8MB以内でアップロードしてください。" in response.text


def assert_missing_omikuji_does_not_create() -> None:
    FakeMentionReactionRepository.reset()
    FakeMentionReactionRepository.has_omikuji = False
    client = build_test_client()
    get_response = client.get("/public")
    assert "現在おみくじを追加できません。" in get_response.text
    response = client.post("/public", data={"action": "add", "fortune_label": "大吉", "omikuji_text": "本文"})
    assert response.status_code == 400
    assert "現在おみくじを追加できません。" in response.text
    assert not FakeMentionReactionRepository.created_choices
    FakeMentionReactionRepository.has_omikuji = True


def assert_preview_separates_label_and_body() -> None:
    FakeMentionReactionRepository.reset()
    client = build_test_client()
    response = client.post(
        "/public",
        data={"action": "preview", "fortune_label": "大吉", "omikuji_text": "良いことあり"},
    )
    assert response.status_code == 200
    assert "大吉" in response.text
    assert "良いことあり" in response.text
    assert "大吉\n良いことあり" not in response.text
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
    assert_get_public_without_login_and_no_selector()
    assert_list_displays_result_label_and_body_separately()
    assert_public_html_hides_internal_terms()
    assert_text_and_label_adds_choice()
    assert_body_only_adds_choice()
    assert_image_only_variations_add_choice()
    assert_label_only_and_empty_rejected()
    assert_bad_image_rejected()
    assert_missing_omikuji_does_not_create()
    assert_preview_separates_label_and_body()
    assert_removed_public_templates_are_gone()
    assert_admin_auth_has_no_public_oauth()
    print("public omikuji result-label checks ok")


if __name__ == "__main__":
    main()
