from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from fastapi.templating import Jinja2Templates

from admin.ux import save_uploaded_image
from bot.db import get_connection
from bot.repositories import MentionReactionRepository


router = APIRouter()

PUBLIC_BOT_ID = "ichiyon"
PUBLIC_BOT_NAME = "いちよんロボ"
PUBLIC_GUILD_ID = "1392174489609179327"
PUBLIC_GUILD_NAME = "ランセ地方"
OMIKUJI_NAMES = ("おみくじ", "omikuji", "kuji")
PUBLIC_IMAGE_CATEGORY = "mention_reaction_choices"
MAX_BODY_LENGTH = 500


def _clean_text(value: Optional[str]) -> str:
    return str(value or "").strip()


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


def _resolve_public_image_path(image_path: str) -> Optional[Path]:
    if not image_path:
        return None
    base_dir = Path(__file__).resolve().parent.parent
    image_root = (base_dir / "assets" / "images").resolve()
    resolved = (base_dir / image_path).resolve()
    try:
        resolved.relative_to(image_root)
    except ValueError:
        return None
    if not resolved.is_file():
        return None
    return resolved


def _find_omikuji_reaction(repository: MentionReactionRepository) -> Optional[Dict[str, Any]]:
    targets = {value.strip() for value in OMIKUJI_NAMES}
    for reaction in repository.list_reactions(
        PUBLIC_GUILD_ID,
        enabled=True,
        reaction_kind="random_draw",
    ):
        values = {
            str(reaction.get("name") or "").strip(),
            str(reaction.get("keyword") or "").strip(),
            str(reaction.get("reaction_key") or "").strip(),
        }
        if values & targets:
            return reaction
    return None


def get_current_omikuji_choices(connection) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    repository = MentionReactionRepository(connection, bot_id=PUBLIC_BOT_ID)
    reaction = _find_omikuji_reaction(repository)
    if reaction is None:
        return None, []
    choices = repository.list_choices(
        PUBLIC_GUILD_ID,
        int(reaction["id"]),
        enabled=True,
    )
    return reaction, choices


def validate_omikuji_form(body: str, has_image: bool) -> List[str]:
    errors: List[str] = []
    if not body and not has_image:
        errors.append("おみくじの内容または画像を入力してください。")
    if len(body) > MAX_BODY_LENGTH:
        errors.append("おみくじの内容は500文字以内で入力してください。")
    return errors


def add_omikuji_choice(
    connection,
    body: str,
    image_path: Optional[str],
) -> Tuple[bool, Optional[str]]:
    repository = MentionReactionRepository(connection, bot_id=PUBLIC_BOT_ID)
    reaction = _find_omikuji_reaction(repository)
    if reaction is None:
        return False, "現在おみくじを追加できません。"
    repository.create_choice(
        guild_id=PUBLIC_GUILD_ID,
        mention_reaction_id=int(reaction["id"]),
        name=body or "画像おみくじ",
        body=body or None,
        image_path=image_path,
        appearance_rate=1,
        enabled=True,
    )
    return True, None


def _load_public_page_context(
    form_body: str = "",
    errors: Optional[List[str]] = None,
    success: str = "",
    preview: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    try:
        with get_connection() as connection:
            reaction, choices = get_current_omikuji_choices(connection)
    except Exception:
        reaction = None
        choices = []
        errors = list(errors or []) + ["現在おみくじを表示できません。"]
    return {
        "bot_name": PUBLIC_BOT_NAME,
        "guild_name": PUBLIC_GUILD_NAME,
        "choices": choices,
        "choice_count": len(choices),
        "can_add": reaction is not None,
        "missing_message": "" if reaction is not None else "現在おみくじを追加できません。",
        "form": {"body": form_body},
        "errors": errors or [],
        "success": success,
        "preview": preview,
        "max_body_length": MAX_BODY_LENGTH,
    }


def register_public_routes(templates: Jinja2Templates) -> None:
    @router.get("/public")
    async def public_home(request: Request):
        return templates.TemplateResponse(
            request,
            "public_home.html",
            _load_public_page_context(),
        )

    @router.get("/public/omikuji-images/{image_index}")
    async def public_omikuji_image(image_index: int):
        if image_index < 1:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        try:
            with get_connection() as connection:
                _, choices = get_current_omikuji_choices(connection)
        except Exception:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        if image_index > len(choices):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        image_path = _resolve_public_image_path(str(choices[image_index - 1].get("image_path") or ""))
        if image_path is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        return FileResponse(image_path)

    @router.post("/public")
    async def public_submit(
        request: Request,
        action: str = Form("add"),
        body: str = Form(""),
        image_upload: Optional[UploadFile] = File(None),
    ):
        cleaned_body = _clean_text(body)
        has_image = _image_selected(image_upload)
        errors = validate_omikuji_form(cleaned_body, has_image)
        if action == "preview" and not errors:
            return templates.TemplateResponse(
                request,
                "public_home.html",
                _load_public_page_context(
                    form_body=cleaned_body,
                    preview={"body": cleaned_body, "has_image": has_image},
                ),
            )
        if errors:
            return templates.TemplateResponse(
                request,
                "public_home.html",
                _load_public_page_context(form_body=cleaned_body, errors=errors),
                status_code=400,
            )

        image_path, upload_error = await save_uploaded_image(image_upload, PUBLIC_IMAGE_CATEGORY)
        if upload_error:
            return templates.TemplateResponse(
                request,
                "public_home.html",
                _load_public_page_context(
                    form_body=cleaned_body,
                    errors=["画像は png, jpg, jpeg, gif, webp の8MB以内でアップロードしてください。"],
                ),
                status_code=400,
            )

        try:
            with get_connection() as connection:
                ok, message = add_omikuji_choice(connection, cleaned_body, image_path)
                if ok:
                    connection.commit()
                else:
                    connection.rollback()
                    _remove_saved_file(image_path)
                    errors = [message or "現在おみくじを追加できません。"]
        except Exception:
            _remove_saved_file(image_path)
            errors = ["追加に失敗しました。時間をおいてもう一度お試しください。"]

        if errors:
            return templates.TemplateResponse(
                request,
                "public_home.html",
                _load_public_page_context(form_body=cleaned_body, errors=errors),
                status_code=400,
            )

        return templates.TemplateResponse(
            request,
            "public_home.html",
            _load_public_page_context(success="追加しました。"),
        )
