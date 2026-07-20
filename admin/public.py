from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from admin.auth import (
    get_public_guild_ids,
    get_public_user,
    get_oauth_config_status,
    oauth_is_configured,
)
from admin.ux import save_uploaded_image
from bot.db import get_connection
from bot.repositories import AutoReactionRepository, MentionReactionRepository, PermissionRepository


router = APIRouter()

PUBLIC_SELECTED_BOT_KEY = "public_selected_bot_id"
PUBLIC_SELECTED_GUILD_KEY = "public_selected_guild_id"

ENTRY_TYPES = {
    "normal": {
        "title": "普通のメッセージへの返事",
        "description": "誰かの投稿に含まれる言葉へ、Botが返事をします。",
        "trigger_label": "反応する言葉",
        "body_label": "返事",
        "image_category": "reactions",
        "requires_trigger": True,
    },
    "mention": {
        "title": "Botを呼んだ時の返事",
        "description": "Botを呼んだあとに続く言葉へ、Botが返事をします。",
        "trigger_label": "呼びかけの言葉",
        "body_label": "返事",
        "image_category": "mention_reaction_choices",
        "requires_trigger": True,
    },
    "omikuji": {
        "title": "おみくじの結果",
        "description": "既存のおみくじに、結果を1つ追加します。",
        "body_label": "結果",
        "image_category": "mention_reaction_choices",
        "requires_trigger": False,
        "target_names": ("おみくじ",),
        "missing_message": "このサーバーでは現在おみくじを追加できません。",
    },
    "quote": {
        "title": "名言",
        "description": "既存の名言に、候補を1つ追加します。",
        "body_label": "名言",
        "image_category": "mention_reaction_choices",
        "requires_trigger": False,
        "target_names": ("名言",),
        "missing_message": "このサーバーでは現在名言を追加できません。",
    },
}


def _clean_text(value: Optional[str]) -> str:
    return str(value or "").strip()


def _public_user_label(user: Dict[str, Any]) -> str:
    return str(user.get("global_name") or user.get("username") or "Discordユーザー")


def _image_selected(upload: Optional[UploadFile]) -> bool:
    return bool(upload is not None and upload.filename)


def _remove_saved_file(image_path: Optional[str]) -> None:
    if not image_path:
        return
    base_dir = Path(__file__).resolve().parent.parent
    resolved = (base_dir / image_path).resolve()
    image_root = (base_dir / "assets" / "images").resolve()
    try:
        resolved.relative_to(image_root)
    except ValueError:
        return
    try:
        resolved.unlink(missing_ok=True)
    except OSError:
        pass


def public_scopes_for_request(request: Request) -> List[Dict[str, Any]]:
    guild_ids = get_public_guild_ids(request)
    if not guild_ids:
        return []
    with get_connection() as connection:
        return PermissionRepository(connection).list_public_bot_guilds(guild_ids)


def selected_public_scope(request: Request) -> Optional[Dict[str, Any]]:
    bot_id = str(request.session.get(PUBLIC_SELECTED_BOT_KEY) or "")
    guild_id = str(request.session.get(PUBLIC_SELECTED_GUILD_KEY) or "")
    if not bot_id or not guild_id:
        return None
    for scope in public_scopes_for_request(request):
        if str(scope.get("bot_id")) == bot_id and str(scope.get("guild_id")) == guild_id:
            return scope
    request.session.pop(PUBLIC_SELECTED_BOT_KEY, None)
    request.session.pop(PUBLIC_SELECTED_GUILD_KEY, None)
    return None


def set_selected_public_scope(request: Request, scope: Dict[str, Any]) -> None:
    request.session[PUBLIC_SELECTED_BOT_KEY] = str(scope.get("bot_id") or "")
    request.session[PUBLIC_SELECTED_GUILD_KEY] = str(scope.get("guild_id") or "")


def validate_public_form(entry_type: str, trigger_text: str, body: str, has_image: bool) -> List[str]:
    config = ENTRY_TYPES[entry_type]
    errors: List[str] = []
    if config.get("requires_trigger") and not trigger_text:
        errors.append("{0}を入力してください。".format(config.get("trigger_label") or "言葉"))
    if not body and not has_image:
        errors.append("文字または画像のどちらかを入力してください。")
    return errors


def _find_named_random_draw(
    repository: MentionReactionRepository,
    guild_id: str,
    target_names: Tuple[str, ...],
) -> Optional[Dict[str, Any]]:
    target_set = {name.strip() for name in target_names}
    for reaction in repository.list_reactions(guild_id, enabled=True, reaction_kind="random_draw"):
        values = {
            str(reaction.get("name") or "").strip(),
            str(reaction.get("keyword") or "").strip(),
            str(reaction.get("reaction_key") or "").strip(),
        }
        if values & target_set:
            return reaction
    return None


def create_public_entry(
    connection,
    bot_id: str,
    guild_id: str,
    entry_type: str,
    trigger_text: str,
    body: str,
    image_path: Optional[str],
) -> Tuple[bool, Optional[str]]:
    if entry_type == "normal":
        AutoReactionRepository(connection, bot_id=bot_id).create_reaction(
            guild_id=guild_id,
            trigger_text=trigger_text,
            response_text=body or None,
            image_path=image_path,
            emoji_internal=None,
            match_type="contains",
            priority=0,
            enabled=True,
        )
        return True, None

    repository = MentionReactionRepository(connection, bot_id=bot_id)
    if entry_type == "mention":
        reaction = repository.create_reaction(
            guild_id=guild_id,
            reaction_key="public_mention_{0}".format(uuid4().hex),
            keyword=trigger_text,
            match_type="exact",
            reaction_kind="random_draw",
            name=trigger_text,
            description="",
            admin_only=False,
            is_system=False,
            is_deletable=True,
            enabled=True,
        )
        repository.create_choice(
            guild_id=guild_id,
            mention_reaction_id=int(reaction["id"]),
            name=body or trigger_text,
            body=body or None,
            image_path=image_path,
            appearance_rate=1,
            enabled=True,
        )
        return True, None

    config = ENTRY_TYPES[entry_type]
    reaction = _find_named_random_draw(
        repository,
        guild_id,
        tuple(config.get("target_names") or ()),
    )
    if reaction is None:
        return False, str(config.get("missing_message") or "現在追加できません。")
    repository.create_choice(
        guild_id=guild_id,
        mention_reaction_id=int(reaction["id"]),
        name=body or config["title"],
        body=body or None,
        image_path=image_path,
        appearance_rate=1,
        enabled=True,
    )
    return True, None


def _render_public_page(
    templates: Jinja2Templates,
    request: Request,
    template_name: str,
    context: Dict[str, Any],
    status_code: int = 200,
):
    user = get_public_user(request)
    base_context = {
        "public_user": user,
        "public_user_label": _public_user_label(user or {}),
        "selected_scope": selected_public_scope(request) if user else None,
    }
    base_context.update(context)
    return templates.TemplateResponse(
        request,
        template_name,
        base_context,
        status_code=status_code,
    )


def register_public_routes(templates: Jinja2Templates) -> None:
    @router.get("/public/login")
    async def public_login(request: Request, error: Optional[str] = None):
        return templates.TemplateResponse(
            request,
            "public_login.html",
            {
                "error": error,
                "oauth_configured": oauth_is_configured(),
                "oauth_status": get_oauth_config_status(),
            },
        )

    @router.get("/public")
    async def public_home(request: Request):
        if get_public_user(request) is None:
            return RedirectResponse(url="/public/login", status_code=303)
        scopes = public_scopes_for_request(request)
        if not scopes:
            return _render_public_page(
                templates,
                request,
                "public_denied.html",
                {"message": "追加できるサーバーがありません。"},
                status_code=403,
            )
        scope = selected_public_scope(request)
        if scope is None:
            if len(scopes) == 1:
                set_selected_public_scope(request, scopes[0])
                scope = scopes[0]
            else:
                return _render_public_page(
                    templates,
                    request,
                    "public_select.html",
                    {"scopes": list(enumerate(scopes))},
                )
        return _render_public_page(
            templates,
            request,
            "public_home.html",
            {"entry_types": ENTRY_TYPES, "selected_scope": scope},
        )

    @router.post("/public/select")
    async def public_select(request: Request, scope_index: int = Form(...)):
        if get_public_user(request) is None:
            return RedirectResponse(url="/public/login", status_code=303)
        scopes = public_scopes_for_request(request)
        if 0 <= scope_index < len(scopes):
            set_selected_public_scope(request, scopes[scope_index])
        return RedirectResponse(url="/public", status_code=303)

    @router.post("/public/switch")
    async def public_switch(request: Request):
        request.session.pop(PUBLIC_SELECTED_BOT_KEY, None)
        request.session.pop(PUBLIC_SELECTED_GUILD_KEY, None)
        return RedirectResponse(url="/public", status_code=303)

    @router.get("/public/{entry_type}")
    async def public_entry_form(request: Request, entry_type: str):
        if get_public_user(request) is None:
            return RedirectResponse(url="/public/login", status_code=303)
        if entry_type not in ENTRY_TYPES:
            return RedirectResponse(url="/public", status_code=303)
        scope = selected_public_scope(request)
        if scope is None:
            return RedirectResponse(url="/public", status_code=303)
        return _render_public_page(
            templates,
            request,
            "public_form.html",
            {
                "entry_type": entry_type,
                "entry_config": ENTRY_TYPES[entry_type],
                "form": {"trigger_text": "", "body": ""},
                "errors": [],
                "success": "",
                "preview": None,
                "selected_scope": scope,
            },
        )

    @router.post("/public/{entry_type}")
    async def public_entry_submit(
        request: Request,
        entry_type: str,
        action: str = Form("add"),
        trigger_text: str = Form(""),
        body: str = Form(""),
        image_upload: Optional[UploadFile] = File(None),
    ):
        if get_public_user(request) is None:
            return RedirectResponse(url="/public/login", status_code=303)
        if entry_type not in ENTRY_TYPES:
            return RedirectResponse(url="/public", status_code=303)
        scope = selected_public_scope(request)
        if scope is None:
            return RedirectResponse(url="/public", status_code=303)

        trigger = _clean_text(trigger_text)
        response_body = _clean_text(body)
        form = {"trigger_text": trigger, "body": response_body}
        errors = validate_public_form(entry_type, trigger, response_body, _image_selected(image_upload))
        preview = None
        if action == "preview" and not errors:
            preview = {
                "trigger_text": trigger,
                "body": response_body,
                "has_image": _image_selected(image_upload),
            }
            return _render_public_page(
                templates,
                request,
                "public_form.html",
                {
                    "entry_type": entry_type,
                    "entry_config": ENTRY_TYPES[entry_type],
                    "form": form,
                    "errors": [],
                    "success": "",
                    "preview": preview,
                    "selected_scope": scope,
                },
            )
        if errors:
            return _render_public_page(
                templates,
                request,
                "public_form.html",
                {
                    "entry_type": entry_type,
                    "entry_config": ENTRY_TYPES[entry_type],
                    "form": form,
                    "errors": errors,
                    "success": "",
                    "preview": preview,
                    "selected_scope": scope,
                },
                status_code=400,
            )

        image_path, upload_error = await save_uploaded_image(
            image_upload,
            str(ENTRY_TYPES[entry_type]["image_category"]),
        )
        if upload_error:
            return _render_public_page(
                templates,
                request,
                "public_form.html",
                {
                    "entry_type": entry_type,
                    "entry_config": ENTRY_TYPES[entry_type],
                    "form": form,
                    "errors": ["画像は png, jpg, jpeg, gif, webp の8MB以内でアップロードしてください。"],
                    "success": "",
                    "preview": preview,
                    "selected_scope": scope,
                },
                status_code=400,
            )

        try:
            with get_connection() as connection:
                ok, message = create_public_entry(
                    connection=connection,
                    bot_id=str(scope.get("bot_id")),
                    guild_id=str(scope.get("guild_id")),
                    entry_type=entry_type,
                    trigger_text=trigger,
                    body=response_body,
                    image_path=image_path,
                )
                if ok:
                    connection.commit()
                else:
                    connection.rollback()
                    _remove_saved_file(image_path)
                    errors = [message or "現在追加できません。"]
        except Exception:
            _remove_saved_file(image_path)
            errors = ["追加に失敗しました。時間をおいてもう一度お試しください。"]

        if errors:
            return _render_public_page(
                templates,
                request,
                "public_form.html",
                {
                    "entry_type": entry_type,
                    "entry_config": ENTRY_TYPES[entry_type],
                    "form": form,
                    "errors": errors,
                    "success": "",
                    "preview": preview,
                    "selected_scope": scope,
                },
                status_code=400,
            )

        return _render_public_page(
            templates,
            request,
            "public_form.html",
            {
                "entry_type": entry_type,
                "entry_config": ENTRY_TYPES[entry_type],
                "form": {"trigger_text": "", "body": ""},
                "errors": [],
                "success": "追加しました。",
                "preview": None,
                "selected_scope": scope,
            },
        )
