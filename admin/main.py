import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates


BASE_DIR = Path(__file__).resolve().parent.parent
QUOTES_FILE = BASE_DIR / "data" / "quotes.json"
REACTIONS_FILE = BASE_DIR / "data" / "reactions.json"
NG_WORDS_FILE = BASE_DIR / "data" / "ng_words.json"
KUJI_FILE = BASE_DIR / "data" / "kuji.json"
BACKUP_DIR = BASE_DIR / "data" / "backups"

app = FastAPI(title="いちよんロボ 管理画面")
app.mount("/static", StaticFiles(directory=Path(__file__).resolve().parent / "static"), name="static")
templates = Jinja2Templates(directory=Path(__file__).resolve().parent / "templates")


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


def normalize_quotes_data(data) -> tuple[dict, bool]:
    if isinstance(data, list):
        quotes = [
            {
                "id": f"quote_{index:03d}",
                "text": quote,
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
        enabled = quote.get("enabled", True)
        if not isinstance(quote_id, str) or not quote_id:
            quote_id = f"quote_{index:03d}"
            changed = True
        if not isinstance(text, str):
            changed = True
            continue
        if not isinstance(enabled, bool):
            enabled = True
            changed = True

        normalized_quotes.append({"id": quote_id, "text": text, "enabled": enabled})

    normalized_data = {"quotes": normalized_quotes}
    return normalized_data, changed or data != normalized_data


def load_quotes_data() -> dict:
    data = load_json_file(QUOTES_FILE, {"quotes": []})
    normalized_data, changed = normalize_quotes_data(data)
    if changed:
        save_quotes_data(normalized_data)
    return normalized_data


def normalize_reactions_data(data) -> tuple[dict, bool]:
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
        match_type = reaction.get("match_type", "contains")
        enabled = reaction.get("enabled", True)
        if not isinstance(reaction_id, str) or not reaction_id:
            reaction_id = f"reaction_{index:03d}"
            changed = True
        if not isinstance(trigger, str) or not isinstance(response, str):
            changed = True
            continue
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


def normalize_ng_words_data(data) -> tuple[dict, bool]:
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


def normalize_weight(value) -> tuple[int, bool]:
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


def normalize_kuji_data(data) -> tuple[dict, bool]:
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
        weight, weight_changed = normalize_weight(result.get("weight", 1))
        enabled = result.get("enabled", True)
        if not isinstance(result_id, str) or not result_id:
            result_id = f"kuji_{index:03d}"
            changed = True
        if not isinstance(name, str) or not isinstance(message, str):
            changed = True
            continue
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


def build_next_id(items: list[dict], prefix: str) -> str:
    max_number = 0
    for item in items:
        match = re.fullmatch(rf"{prefix}_(\d+)", item.get("id", ""))
        if match:
            max_number = max(max_number, int(match.group(1)))
    return f"{prefix}_{max_number + 1:03d}"


@app.get("/")
async def index():
    return RedirectResponse(url="/quotes", status_code=303)


@app.get("/quotes")
async def quotes_page(request: Request):
    data = load_quotes_data()
    return templates.TemplateResponse(
        request,
        "quotes.html",
        {"quotes": data["quotes"]},
    )


@app.post("/quotes")
async def create_quote(text: str = Form(...), enabled: Optional[str] = Form(None)):
    data = load_quotes_data()
    text = text.strip()
    if text:
        data["quotes"].append(
            {
                "id": build_next_id(data["quotes"], "quote"),
                "text": text,
                "enabled": enabled == "on",
            }
        )
        save_quotes_data(data)
    return RedirectResponse(url="/quotes", status_code=303)


@app.post("/quotes/{quote_id}/edit")
async def update_quote(
    quote_id: str,
    text: str = Form(...),
    enabled: Optional[str] = Form(None),
):
    data = load_quotes_data()
    for quote in data["quotes"]:
        if quote["id"] == quote_id:
            quote["text"] = text.strip()
            quote["enabled"] = enabled == "on"
            save_quotes_data(data)
            break
    return RedirectResponse(url="/quotes", status_code=303)


@app.post("/quotes/{quote_id}/delete")
async def delete_quote(quote_id: str):
    data = load_quotes_data()
    next_quotes = [quote for quote in data["quotes"] if quote["id"] != quote_id]
    if len(next_quotes) != len(data["quotes"]):
        data["quotes"] = next_quotes
        save_quotes_data(data)
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
    trigger: str = Form(...),
    response: str = Form(...),
    match_type: str = Form("contains"),
    enabled: Optional[str] = Form(None),
):
    data = load_reactions_data()
    trigger = trigger.strip()
    response = response.strip()
    if trigger and response:
        data["reactions"].append(
            {
                "id": build_next_id(data["reactions"], "reaction"),
                "trigger": trigger,
                "response": response,
                "match_type": "contains" if match_type != "contains" else match_type,
                "enabled": enabled == "on",
            }
        )
        save_reactions_data(data)
    return RedirectResponse(url="/reactions", status_code=303)


@app.post("/reactions/{reaction_id}/edit")
async def update_reaction(
    reaction_id: str,
    trigger: str = Form(...),
    response: str = Form(...),
    match_type: str = Form("contains"),
    enabled: Optional[str] = Form(None),
):
    data = load_reactions_data()
    for reaction in data["reactions"]:
        if reaction["id"] == reaction_id:
            reaction["trigger"] = trigger.strip()
            reaction["response"] = response.strip()
            reaction["match_type"] = "contains" if match_type != "contains" else match_type
            reaction["enabled"] = enabled == "on"
            save_reactions_data(data)
            break
    return RedirectResponse(url="/reactions", status_code=303)


@app.post("/reactions/{reaction_id}/delete")
async def delete_reaction(reaction_id: str):
    data = load_reactions_data()
    next_reactions = [
        reaction for reaction in data["reactions"] if reaction["id"] != reaction_id
    ]
    if len(next_reactions) != len(data["reactions"]):
        data["reactions"] = next_reactions
        save_reactions_data(data)
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
    name: str = Form(...),
    message: str = Form(...),
    weight: str = Form("1"),
    enabled: Optional[str] = Form(None),
):
    data = load_kuji_data()
    name = name.strip()
    message = message.strip()
    weight, _ = normalize_weight(weight)
    if name and message:
        data["results"].append(
            {
                "id": build_next_id(data["results"], "kuji"),
                "name": name,
                "message": message,
                "weight": weight,
                "enabled": enabled == "on",
            }
        )
        save_kuji_data(data)
    return RedirectResponse(url="/kuji", status_code=303)


@app.post("/kuji/{result_id}/edit")
async def update_kuji_result(
    result_id: str,
    name: str = Form(...),
    message: str = Form(...),
    weight: str = Form("1"),
    enabled: Optional[str] = Form(None),
):
    data = load_kuji_data()
    weight, _ = normalize_weight(weight)
    for result in data["results"]:
        if result["id"] == result_id:
            result["name"] = name.strip()
            result["message"] = message.strip()
            result["weight"] = weight
            result["enabled"] = enabled == "on"
            save_kuji_data(data)
            break
    return RedirectResponse(url="/kuji", status_code=303)


@app.post("/kuji/{result_id}/delete")
async def delete_kuji_result(result_id: str):
    data = load_kuji_data()
    next_results = [result for result in data["results"] if result["id"] != result_id]
    if len(next_results) != len(data["results"]):
        data["results"] = next_results
        save_kuji_data(data)
    return RedirectResponse(url="/kuji", status_code=303)
