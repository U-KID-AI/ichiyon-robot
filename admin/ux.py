import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from uuid import uuid4

from fastapi import UploadFile


BASE_DIR = Path(__file__).resolve().parent.parent
IMAGE_ROOT = BASE_DIR / "assets" / "images"
ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
MAX_IMAGE_SIZE = 8 * 1024 * 1024

REACTION_KIND_LABELS = {
    "random": "ランダム抽選",
    "random_draw": "ランダム抽選",
    "search": "検索",
}

MATCH_TYPE_LABELS = {
    "exact": "完全一致",
    "prefix": "前方一致",
    "contains": "含む",
    "regex": "正規表現（上級者向け）",
}

STATUS_LABELS = {
    True: "有効",
    False: "無効",
}

BEHAVIOR_LABELS = {
    "reply": "返信する",
    "offline": "反応しない",
}

EFFECT_TYPE_LABELS = {
    "probability_message": "確率で追加投稿",
    "message": "メッセージ投稿",
    "reaction": "リアクション",
    "counter_delta": "カウント加算",
    "counter_set": "カウント設定",
    "probability_multiplier": "確率倍率",
    "next_action_count": "次の動作回数",
    "mode_roll": "モード抽選",
    "mode_enter": "モード突入",
    "temporary_state": "一時状態",
    "ng_behavior": "NGワード動作",
    "extra_choice": "候補追加",
}

SCHEDULE_TYPE_LABELS = {
    "once": "一度だけ",
    "yearly": "毎年",
    "monthly": "毎月",
    "weekly": "毎週",
    "daily": "毎日",
}


def label_value(mapping: Dict[Any, str], value: Any) -> str:
    return mapping.get(value, str(value or ""))


def is_test_data(value: Any) -> bool:
    text = str(value or "").lower()
    return text.startswith("integration_check") or text.startswith("000 integration")


def row_is_test_data(row: Dict[str, Any]) -> bool:
    keys = (
        "reaction_key",
        "mode_key",
        "name",
        "trigger_text",
        "word",
        "body",
        "description",
    )
    return any(is_test_data(row.get(key)) for key in keys)


def parse_show_test_data(value: str) -> bool:
    return value == "true"


def safe_filename_stem(filename: str) -> str:
    stem = Path(filename).stem
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("._")
    return stem or "image"


async def save_uploaded_image(
    upload: Optional[UploadFile],
    category: str,
) -> Tuple[Optional[str], Optional[str]]:
    if upload is None or not upload.filename:
        return None, None

    suffix = Path(upload.filename).suffix.lower()
    if suffix not in ALLOWED_IMAGE_EXTENSIONS:
        return None, "対応していない画像形式です。png, jpg, jpeg, gif, webp を選んでください。"

    content = await upload.read()
    if not content:
        return None, "画像ファイルが空です。"
    if len(content) > MAX_IMAGE_SIZE:
        return None, "画像サイズは8MB以下にしてください。"

    target_dir = IMAGE_ROOT / category
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = "{0}_{1}{2}".format(uuid4().hex, safe_filename_stem(upload.filename), suffix)
    target_path = target_dir / filename
    target_path.write_bytes(content)
    return target_path.relative_to(BASE_DIR).as_posix(), None
