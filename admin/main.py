import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from admin.auto_reactions import (
    register_auto_reaction_routes,
    router as auto_reaction_router,
)
from admin.auto_posts import register_auto_post_routes, router as auto_post_router
from admin.auth import get_session_secret, register_auth_routes, router as auth_router
from admin.mention_reactions import (
    register_mention_reaction_routes,
    router as mention_reaction_router,
)
from admin.mention_limited_effects import (
    register_mention_limited_effect_routes,
    router as mention_limited_effect_router,
)
from admin.modes import register_mode_routes, router as mode_router
from admin.ng_words_db import register_ng_word_routes, router as ng_word_router
from admin.reaction_thresholds import (
    register_reaction_threshold_routes,
    router as reaction_threshold_router,
)
from admin.servers import register_server_routes, router as server_router
from admin.special_effects import (
    register_special_effect_routes,
    router as special_effect_router,
)
from admin.x_updates import register_x_update_routes, router as x_update_router


BASE_DIR = Path(__file__).resolve().parent.parent
QUOTES_FILE = BASE_DIR / "data" / "quotes.json"
REACTIONS_FILE = BASE_DIR / "data" / "reactions.json"
NG_WORDS_FILE = BASE_DIR / "data" / "ng_words.json"
KUJI_FILE = BASE_DIR / "data" / "kuji.json"
BACKUP_DIR = BASE_DIR / "data" / "backups"
IMAGE_ROOT = BASE_DIR / "assets" / "images"
ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
MAX_IMAGE_SIZE = 8 * 1024 * 1024

for image_category in ("quotes", "kuji", "reactions"):
    (IMAGE_ROOT / image_category).mkdir(parents=True, exist_ok=True)

app = FastAPI(title="いちよんロボ 管理画面")
app.add_middleware(
    SessionMiddleware,
    secret_key=get_session_secret(),
    same_site="lax",
    https_only=False,
)
app.mount("/static", StaticFiles(directory=Path(__file__).resolve().parent / "static"), name="static")
app.mount("/assets", StaticFiles(directory=BASE_DIR / "assets"), name="assets")
templates = Jinja2Templates(directory=Path(__file__).resolve().parent / "templates")
register_auth_routes(templates)
register_server_routes(templates)
register_mention_limited_effect_routes(templates)
register_mention_reaction_routes(templates)
register_special_effect_routes(templates)
register_auto_reaction_routes(templates)
register_ng_word_routes(templates)
register_mode_routes(templates)
register_auto_post_routes(templates)
register_reaction_threshold_routes(templates)
register_x_update_routes(templates)
app.include_router(auth_router)
app.include_router(server_router)
app.include_router(mention_limited_effect_router)
app.include_router(mention_reaction_router)
app.include_router(special_effect_router)
app.include_router(auto_reaction_router)
app.include_router(ng_word_router)
app.include_router(mode_router)
app.include_router(auto_post_router)
app.include_router(reaction_threshold_router)
app.include_router(x_update_router)


LEGACY_JSON_PATHS = ("/quotes", "/reactions", "/ng-words", "/kuji")


def legacy_json_pages_enabled() -> bool:
    return os.getenv("ADMIN_ENABLE_LEGACY_JSON_PAGES", "").strip().lower() == "true"


def is_legacy_json_path(path: str) -> bool:
    for legacy_path in LEGACY_JSON_PATHS:
        if path == legacy_path or path.startswith(legacy_path + "/"):
            return True
    return False


@app.middleware("http")
async def redirect_legacy_json_pages(request: Request, call_next):
    if is_legacy_json_path(request.url.path) and not legacy_json_pages_enabled():
        return RedirectResponse(url="/servers", status_code=303)
    return await call_next(request)


def load_json_file(path: Path, default):
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def backup_json_file(path: Path) -> None:
    if not path.exists():
        return

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"{timestamp}_{path.name}"
    try:
        backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    except OSError as e:
        print(f"[WARN] Failed to backup {path}: {e}")


def save_quotes_data(data: dict) -> None:
    backup_json_file(QUOTES_FILE)
    QUOTES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with QUOTES_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def save_reactions_data(data: dict) -> None:
    backup_json_file(REACTIONS_FILE)
    REACTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with REACTIONS_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def save_ng_words_data(data: dict) -> None:
    backup_json_file(NG_WORDS_FILE)
    NG_WORDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with NG_WORDS_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def save_kuji_data(data: dict) -> None:
    backup_json_file(KUJI_FILE)
    KUJI_FILE.parent.mkdir(parents=True, exist_ok=True)
    with KUJI_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def normalize_image_path(value) -> Tuple[str, bool]:
    if isinstance(value, str):
        return value, False
    return "", True


def safe_filename_stem(filename: str) -> str:
    stem = Path(filename).stem
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("._")
    return stem or "image"


async def save_uploaded_image(
    upload: Optional[UploadFile],
    item_id: str,
    category: str,
) -> Tuple[Optional[str], Optional[str]]:
    if upload is None or not upload.filename:
        return None, None

    suffix = Path(upload.filename).suffix.lower()
    if suffix not in ALLOWED_IMAGE_EXTENSIONS:
        return None, "対応外の画像形式。"

    content = await upload.read()
    if len(content) > MAX_IMAGE_SIZE:
        return None, "画像サイズは8MB以下。"
    if not content:
        return None, "画像ファイルが空。"

    target_dir = IMAGE_ROOT / category
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    filename = f"{item_id}_{timestamp}_{safe_filename_stem(upload.filename)}{suffix}"
    target_path = target_dir / filename
    target_path.write_bytes(content)
    return target_path.relative_to(BASE_DIR).as_posix(), None


def resolve_managed_image_path(image_path: str) -> Optional[Path]:
    if not image_path:
        return None

    path = Path(image_path)
    if path.is_absolute() or path.suffix.lower() not in ALLOWED_IMAGE_EXTENSIONS:
        return None

    resolved_path = (BASE_DIR / path).resolve()
    try:
        resolved_path.relative_to(IMAGE_ROOT.resolve())
    except ValueError:
        return None

    return resolved_path


def image_path_is_referenced(image_path: str, exclude_id: Optional[str] = None) -> bool:
    if not image_path:
        return False

    datasets = (
        load_quotes_data().get("quotes", []),
        load_reactions_data().get("reactions", []),
        load_kuji_data().get("results", []),
    )
    for items in datasets:
        for item in items:
            if item.get("id") == exclude_id:
                continue
            if item.get("image_path") == image_path:
                return True
    return False


def delete_image_if_unreferenced(
    image_path: str,
    exclude_id: Optional[str] = None,
) -> None:
    resolved_path = resolve_managed_image_path(image_path)
    if resolved_path is None or not resolved_path.exists():
        return
    if image_path_is_referenced(image_path, exclude_id):
        return

    try:
        resolved_path.unlink()
    except OSError as e:
        print(f"[WARN] Failed to delete image {image_path}: {e}")


def normalize_quotes_data(data) -> Tuple[Dict, bool]:
    if isinstance(data, list):
        quotes = [
            {
                "id": f"quote_{index:03d}",
                "text": quote,
                "image_path": "",
                "enabled": True,
            }
            for index, quote in enumerate(data, start=1)
            if isinstance(quote, str)
        ]
        return {"quotes": quotes}, True

    if not isinstance(data, dict):
        return {"quotes": []}, True

    raw_quotes = data.get("quotes", [])
    if not isinstance(raw_quotes, list):
        return {"quotes": []}, True

    normalized_quotes = []
    changed = False
    for index, quote in enumerate(raw_quotes, start=1):
        if not isinstance(quote, dict):
            changed = True
            continue

        quote_id = quote.get("id")
        text = quote.get("text")
        image_path, image_path_changed = normalize_image_path(quote.get("image_path", ""))
        enabled = quote.get("enabled", True)
        if not isinstance(quote_id, str) or not quote_id:
            quote_id = f"quote_{index:03d}"
            changed = True
        if not isinstance(text, str):
            text = ""
            changed = True
        if image_path_changed:
            changed = True
        if not isinstance(enabled, bool):
            enabled = True
            changed = True

        normalized_quotes.append(
            {
                "id": quote_id,
                "text": text,
                "image_path": image_path,
                "enabled": enabled,
            }
        )

    normalized_data = {"quotes": normalized_quotes}
    return normalized_data, changed or data != normalized_data


def load_quotes_data() -> dict:
    data = load_json_file(QUOTES_FILE, {"quotes": []})
    normalized_data, changed = normalize_quotes_data(data)
    if changed:
        save_quotes_data(normalized_data)
    return normalized_data


def normalize_priority(value) -> Tuple[int, bool]:
    if isinstance(value, int) and value >= 1:
        return value, False
    if isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return 1, True
        if parsed >= 1:
            return parsed, False
    return 1, True


def normalize_reactions_data(data) -> Tuple[Dict, bool]:
    if not isinstance(data, dict):
        return {"reactions": []}, True

    raw_reactions = data.get("reactions", [])
    if not isinstance(raw_reactions, list):
        return {"reactions": []}, True

    normalized_reactions = []
    changed = False
    for index, reaction in enumerate(raw_reactions, start=1):
        if not isinstance(reaction, dict):
            changed = True
            continue

        reaction_id = reaction.get("id")
        trigger = reaction.get("trigger")
        response = reaction.get("response")
        image_path, image_path_changed = normalize_image_path(
            reaction.get("image_path", "")
        )
        priority, priority_changed = normalize_priority(reaction.get("priority", 1))
        match_type = reaction.get("match_type", "contains")
        enabled = reaction.get("enabled", True)
        if not isinstance(reaction_id, str) or not reaction_id:
            reaction_id = f"reaction_{index:03d}"
            changed = True
        if not isinstance(trigger, str):
            changed = True
            continue
        if not isinstance(response, str):
            response = ""
            changed = True
        if image_path_changed:
            changed = True
        if priority_changed:
            changed = True
        if match_type != "contains":
            match_type = "contains"
            changed = True
        if not isinstance(enabled, bool):
            enabled = True
            changed = True

        normalized_reactions.append(
            {
                "id": reaction_id,
                "trigger": trigger,
                "response": response,
                "image_path": image_path,
                "priority": priority,
                "match_type": match_type,
                "enabled": enabled,
            }
        )

    normalized_data = {"reactions": normalized_reactions}
    return normalized_data, changed or data != normalized_data


def load_reactions_data() -> dict:
    data = load_json_file(REACTIONS_FILE, {"reactions": []})
    normalized_data, changed = normalize_reactions_data(data)
    if changed:
        save_reactions_data(normalized_data)
    return normalized_data


def normalize_ng_words_data(data) -> Tuple[Dict, bool]:
    if isinstance(data, list):
        words = [
            {
                "id": f"ng_{index:03d}",
                "word": word,
                "enabled": True,
            }
            for index, word in enumerate(data, start=1)
            if isinstance(word, str)
        ]
        return {"words": words}, True

    if not isinstance(data, dict):
        return {"words": []}, True

    raw_words = data.get("words", [])
    if not isinstance(raw_words, list):
        return {"words": []}, True

    normalized_words = []
    changed = False
    for index, word_item in enumerate(raw_words, start=1):
        if isinstance(word_item, str):
            normalized_words.append(
                {
                    "id": f"ng_{index:03d}",
                    "word": word_item,
                    "enabled": True,
                }
            )
            changed = True
            continue

        if not isinstance(word_item, dict):
            changed = True
            continue

        word_id = word_item.get("id")
        word = word_item.get("word")
        enabled = word_item.get("enabled", True)
        if not isinstance(word_id, str) or not word_id:
            word_id = f"ng_{index:03d}"
            changed = True
        if not isinstance(word, str):
            changed = True
            continue
        if not isinstance(enabled, bool):
            enabled = True
            changed = True

        normalized_words.append({"id": word_id, "word": word, "enabled": enabled})

    normalized_data = {"words": normalized_words}
    return normalized_data, changed or data != normalized_data


def load_ng_words_data() -> dict:
    data = load_json_file(NG_WORDS_FILE, {"words": []})
    normalized_data, changed = normalize_ng_words_data(data)
    if changed:
        save_ng_words_data(normalized_data)
    return normalized_data


def normalize_weight(value) -> Tuple[int, bool]:
    if isinstance(value, int) and value >= 1:
        return value, False
    if isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return 1, True
        if parsed >= 1:
            return parsed, False
    return 1, True


def normalize_kuji_data(data) -> Tuple[Dict, bool]:
    if not isinstance(data, dict):
        return {"results": []}, True

    raw_results = data.get("results", [])
    if not isinstance(raw_results, list):
        return {"results": []}, True

    normalized_results = []
    changed = False
    for index, result in enumerate(raw_results, start=1):
        if not isinstance(result, dict):
            changed = True
            continue

        result_id = result.get("id")
        name = result.get("name")
        message = result.get("message")
        image_path, image_path_changed = normalize_image_path(result.get("image_path", ""))
        weight, weight_changed = normalize_weight(result.get("weight", 1))
        enabled = result.get("enabled", True)
        if not isinstance(result_id, str) or not result_id:
            result_id = f"kuji_{index:03d}"
            changed = True
        if not isinstance(name, str):
            name = ""
            changed = True
        if not isinstance(message, str):
            message = ""
            changed = True
        if image_path_changed:
            changed = True
        if weight_changed:
            changed = True
        if not isinstance(enabled, bool):
            enabled = True
            changed = True

        normalized_results.append(
            {
                "id": result_id,
                "name": name,
                "message": message,
                "image_path": image_path,
                "weight": weight,
                "enabled": enabled,
            }
        )

    normalized_data = {"results": normalized_results}
    return normalized_data, changed or data != normalized_data


def load_kuji_data() -> dict:
    data = load_json_file(KUJI_FILE, {"results": []})
    normalized_data, changed = normalize_kuji_data(data)
    if changed:
        save_kuji_data(normalized_data)
    return normalized_data


def build_next_id(items: List[Dict], prefix: str) -> str:
    max_number = 0
    for item in items:
        match = re.fullmatch(rf"{prefix}_(\d+)", item.get("id", ""))
        if match:
            max_number = max(max_number, int(match.group(1)))
    return f"{prefix}_{max_number + 1:03d}"


@app.get("/")
async def index(request: Request):
    if request.session.get("discord_user"):
        return RedirectResponse(url="/servers", status_code=303)
    return RedirectResponse(url="/login", status_code=303)


@app.get("/quotes")
async def quotes_page(request: Request):
    data = load_quotes_data()
    return templates.TemplateResponse(
        request,
        "quotes.html",
        {"quotes": data["quotes"]},
    )


@app.post("/quotes")
async def create_quote(
    request: Request,
    text: str = Form(""),
    enabled: Optional[str] = Form(None),
    image: Optional[UploadFile] = File(None),
):
    data = load_quotes_data()
    text = text.strip()
    quote_id = build_next_id(data["quotes"], "quote")
    image_path, error = await save_uploaded_image(image, quote_id, "quotes")
    if error is not None:
        return templates.TemplateResponse(
            request,
            "quotes.html",
            {"quotes": data["quotes"], "error": error},
        )
    if text or image_path:
        data["quotes"].append(
            {
                "id": quote_id,
                "text": text,
                "image_path": image_path or "",
                "enabled": enabled == "on",
            }
        )
        save_quotes_data(data)
    return RedirectResponse(url="/quotes", status_code=303)


@app.post("/quotes/{quote_id}/edit")
async def update_quote(
    request: Request,
    quote_id: str,
    text: str = Form(""),
    enabled: Optional[str] = Form(None),
    image: Optional[UploadFile] = File(None),
    delete_image: Optional[str] = Form(None),
):
    data = load_quotes_data()
    for quote in data["quotes"]:
        if quote["id"] == quote_id:
            old_image_path = quote.get("image_path", "")
            new_image_path, error = await save_uploaded_image(image, quote_id, "quotes")
            if error is not None:
                return templates.TemplateResponse(
                    request,
                    "quotes.html",
                    {"quotes": data["quotes"], "error": error},
                )

            quote["text"] = text.strip()
            if delete_image == "on":
                quote["image_path"] = ""
            if new_image_path is not None:
                quote["image_path"] = new_image_path
            quote["enabled"] = enabled == "on"
            save_quotes_data(data)
            if delete_image == "on" or new_image_path is not None:
                delete_image_if_unreferenced(old_image_path, quote_id)
            break
    return RedirectResponse(url="/quotes", status_code=303)


@app.post("/quotes/{quote_id}/delete")
async def delete_quote(quote_id: str):
    data = load_quotes_data()
    old_image_path = ""
    for quote in data["quotes"]:
        if quote["id"] == quote_id:
            old_image_path = quote.get("image_path", "")
            break
    next_quotes = [quote for quote in data["quotes"] if quote["id"] != quote_id]
    if len(next_quotes) != len(data["quotes"]):
        data["quotes"] = next_quotes
        save_quotes_data(data)
        delete_image_if_unreferenced(old_image_path, quote_id)
    return RedirectResponse(url="/quotes", status_code=303)


@app.get("/reactions")
async def reactions_page(request: Request):
    data = load_reactions_data()
    return templates.TemplateResponse(
        request,
        "reactions.html",
        {"reactions": data["reactions"]},
    )


@app.post("/reactions")
async def create_reaction(
    request: Request,
    trigger: str = Form(...),
    response: str = Form(""),
    priority: str = Form("1"),
    match_type: str = Form("contains"),
    enabled: Optional[str] = Form(None),
    image: Optional[UploadFile] = File(None),
):
    data = load_reactions_data()
    trigger = trigger.strip()
    response = response.strip()
    priority_value, _ = normalize_priority(priority)
    reaction_id = build_next_id(data["reactions"], "reaction")
    image_path, error = await save_uploaded_image(image, reaction_id, "reactions")
    if error is not None:
        return templates.TemplateResponse(
            request,
            "reactions.html",
            {"reactions": data["reactions"], "error": error},
        )
    if trigger and (response or image_path):
        data["reactions"].append(
            {
                "id": reaction_id,
                "trigger": trigger,
                "response": response,
                "image_path": image_path or "",
                "priority": priority_value,
                "match_type": "contains" if match_type != "contains" else match_type,
                "enabled": enabled == "on",
            }
        )
        save_reactions_data(data)
    return RedirectResponse(url="/reactions", status_code=303)


@app.post("/reactions/{reaction_id}/edit")
async def update_reaction(
    request: Request,
    reaction_id: str,
    trigger: str = Form(...),
    response: str = Form(""),
    priority: str = Form("1"),
    match_type: str = Form("contains"),
    enabled: Optional[str] = Form(None),
    image: Optional[UploadFile] = File(None),
    delete_image: Optional[str] = Form(None),
):
    data = load_reactions_data()
    priority_value, _ = normalize_priority(priority)
    for reaction in data["reactions"]:
        if reaction["id"] == reaction_id:
            old_image_path = reaction.get("image_path", "")
            new_image_path, error = await save_uploaded_image(
                image,
                reaction_id,
                "reactions",
            )
            if error is not None:
                return templates.TemplateResponse(
                    request,
                    "reactions.html",
                    {"reactions": data["reactions"], "error": error},
                )

            reaction["trigger"] = trigger.strip()
            reaction["response"] = response.strip()
            if delete_image == "on":
                reaction["image_path"] = ""
            if new_image_path is not None:
                reaction["image_path"] = new_image_path
            reaction["priority"] = priority_value
            reaction["match_type"] = "contains" if match_type != "contains" else match_type
            reaction["enabled"] = enabled == "on"
            save_reactions_data(data)
            if delete_image == "on" or new_image_path is not None:
                delete_image_if_unreferenced(old_image_path, reaction_id)
            break
    return RedirectResponse(url="/reactions", status_code=303)


@app.post("/reactions/{reaction_id}/delete")
async def delete_reaction(reaction_id: str):
    data = load_reactions_data()
    old_image_path = ""
    for reaction in data["reactions"]:
        if reaction["id"] == reaction_id:
            old_image_path = reaction.get("image_path", "")
            break
    next_reactions = [
        reaction for reaction in data["reactions"] if reaction["id"] != reaction_id
    ]
    if len(next_reactions) != len(data["reactions"]):
        data["reactions"] = next_reactions
        save_reactions_data(data)
        delete_image_if_unreferenced(old_image_path, reaction_id)
    return RedirectResponse(url="/reactions", status_code=303)


@app.get("/ng-words")
async def ng_words_page(request: Request):
    data = load_ng_words_data()
    return templates.TemplateResponse(
        request,
        "ng_words.html",
        {"words": data["words"]},
    )


@app.post("/ng-words")
async def create_ng_word(word: str = Form(...), enabled: Optional[str] = Form(None)):
    data = load_ng_words_data()
    word = word.strip()
    if word:
        data["words"].append(
            {
                "id": build_next_id(data["words"], "ng"),
                "word": word,
                "enabled": enabled == "on",
            }
        )
        save_ng_words_data(data)
    return RedirectResponse(url="/ng-words", status_code=303)


@app.post("/ng-words/{word_id}/edit")
async def update_ng_word(
    word_id: str,
    word: str = Form(...),
    enabled: Optional[str] = Form(None),
):
    data = load_ng_words_data()
    for word_item in data["words"]:
        if word_item["id"] == word_id:
            word_item["word"] = word.strip()
            word_item["enabled"] = enabled == "on"
            save_ng_words_data(data)
            break
    return RedirectResponse(url="/ng-words", status_code=303)


@app.post("/ng-words/{word_id}/delete")
async def delete_ng_word(word_id: str):
    data = load_ng_words_data()
    next_words = [word_item for word_item in data["words"] if word_item["id"] != word_id]
    if len(next_words) != len(data["words"]):
        data["words"] = next_words
        save_ng_words_data(data)
    return RedirectResponse(url="/ng-words", status_code=303)


@app.get("/kuji")
async def kuji_page(request: Request):
    data = load_kuji_data()
    return templates.TemplateResponse(
        request,
        "kuji.html",
        {"results": data["results"]},
    )


@app.post("/kuji")
async def create_kuji_result(
    request: Request,
    name: str = Form(""),
    message: str = Form(""),
    weight: str = Form("1"),
    enabled: Optional[str] = Form(None),
    image: Optional[UploadFile] = File(None),
):
    data = load_kuji_data()
    name = name.strip()
    message = message.strip()
    weight, _ = normalize_weight(weight)
    result_id = build_next_id(data["results"], "kuji")
    image_path, error = await save_uploaded_image(image, result_id, "kuji")
    if error is not None:
        return templates.TemplateResponse(
            request,
            "kuji.html",
            {"results": data["results"], "error": error},
        )
    if name or message or image_path:
        data["results"].append(
            {
                "id": result_id,
                "name": name,
                "message": message,
                "image_path": image_path or "",
                "weight": weight,
                "enabled": enabled == "on",
            }
        )
        save_kuji_data(data)
    return RedirectResponse(url="/kuji", status_code=303)


@app.post("/kuji/{result_id}/edit")
async def update_kuji_result(
    request: Request,
    result_id: str,
    name: str = Form(""),
    message: str = Form(""),
    weight: str = Form("1"),
    enabled: Optional[str] = Form(None),
    image: Optional[UploadFile] = File(None),
    delete_image: Optional[str] = Form(None),
):
    data = load_kuji_data()
    weight, _ = normalize_weight(weight)
    for result in data["results"]:
        if result["id"] == result_id:
            old_image_path = result.get("image_path", "")
            new_image_path, error = await save_uploaded_image(image, result_id, "kuji")
            if error is not None:
                return templates.TemplateResponse(
                    request,
                    "kuji.html",
                    {"results": data["results"], "error": error},
                )

            result["name"] = name.strip()
            result["message"] = message.strip()
            if delete_image == "on":
                result["image_path"] = ""
            if new_image_path is not None:
                result["image_path"] = new_image_path
            result["weight"] = weight
            result["enabled"] = enabled == "on"
            save_kuji_data(data)
            if delete_image == "on" or new_image_path is not None:
                delete_image_if_unreferenced(old_image_path, result_id)
            break
    return RedirectResponse(url="/kuji", status_code=303)


@app.post("/kuji/{result_id}/delete")
async def delete_kuji_result(result_id: str):
    data = load_kuji_data()
    old_image_path = ""
    for result in data["results"]:
        if result["id"] == result_id:
            old_image_path = result.get("image_path", "")
            break
    next_results = [result for result in data["results"] if result["id"] != result_id]
    if len(next_results) != len(data["results"]):
        data["results"] = next_results
        save_kuji_data(data)
        delete_image_if_unreferenced(old_image_path, result_id)
    return RedirectResponse(url="/kuji", status_code=303)
