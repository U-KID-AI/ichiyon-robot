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
    "regex": "正規表現",
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
    "destroy": "破壊",
    "mention_suffix_guard": "さん付け確認",
    "custom": "カスタム",
}

TARGET_TYPE_LABELS = {
    "mention_reaction_choice": "抽選候補",
    "auto_reaction": "自動反応",
    "ng_word": "NGワード",
}

TRIGGER_TIMING_LABELS = {
    "choice_selected": "候補が選ばれた時",
    "auto_reaction_triggered": "自動反応が動いた時",
    "ng_word_detected": "NGワード検知時",
}

ADDITIONAL_POST_TIMING_LABELS = {
    "none": "投稿なし",
    "tag_triggered": "タグ発動時",
    "effect_success": "効果成功時",
    "effect_end": "効果終了時",
}

EXPIRES_TYPE_LABELS = {
    "immediate": "その場だけ",
    "next_bot_action": "次のBot動作まで",
    "next_special_roll": "次の特殊抽選まで",
    "seconds": "秒数指定",
    "count": "回数指定",
    "permanent": "期限なし",
}

COOLDOWN_SCOPE_LABELS = {
    "none": "なし",
    "guild": "サーバー単位",
    "channel": "チャンネル単位",
    "user": "ユーザー単位",
    "assigned_event": "付与先ごと",
}

COOLDOWN_TYPE_LABELS = {
    "none": "なし",
    "duration": "時間指定",
    "once_per_period": "期間内1回",
}

COOLDOWN_PERIOD_LABELS = {
    "none": "なし",
    "monthly": "月ごと",
}

COOLDOWN_RESET_LABELS = {
    "none": "なし",
    "month_start": "月初",
    "day": "指定日",
}

CONDITION_TYPE_LABELS = {
    "probability": "確率抽選",
    "counter_threshold": "カウント到達",
    "period_not_triggered": "期間内未発動",
    "manual": "手動",
    "schedule": "日時指定",
    "duration": "時間経過",
    "duration_elapsed": "時間経過",
}

RESET_TYPE_LABELS = {
    "none": "なし",
    "daily": "毎日",
    "monthly": "毎月",
    "monthly_day": "毎月指定日",
    "manual": "手動",
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
        return None, "対応外の画像形式。png, jpg, jpeg, gif, webp のみ。"

    content = await upload.read()
    if not content:
        return None, "画像ファイルが空。"
    if len(content) > MAX_IMAGE_SIZE:
        return None, "画像サイズは8MB以下。"

    target_dir = IMAGE_ROOT / category
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = "{0}_{1}{2}".format(uuid4().hex, safe_filename_stem(upload.filename), suffix)
    target_path = target_dir / filename
    target_path.write_bytes(content)
    return target_path.relative_to(BASE_DIR).as_posix(), None
