import os

from dotenv import load_dotenv

load_dotenv()


def get_env_int(name: str, default: int = 0) -> int:
    value = os.getenv(name)
    if not value:
        return default

    try:
        return int(value)
    except ValueError:
        print(f"[WARN] {name} must be an integer")
        return default


def get_env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default

    normalized = value.strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False

    print(f"[WARN] {name} must be true or false")
    return default


def get_app_env() -> str:
    value = os.getenv("APP_ENV", "production").strip().lower()
    if value in ("production", "development"):
        return value

    print("[WARN] APP_ENV must be production or development")
    return "production"


def get_data_backend() -> str:
    value = os.getenv("ICHIYON_DATA_BACKEND", "json").strip().lower()
    if value in ("json", "db"):
        return value

    print("[WARN] ICHIYON_DATA_BACKEND must be json or db")
    return "json"


def get_env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip()


def get_default_normal_bot_nickname(app_env: str) -> str:
    if app_env == "development":
        return "いちよんロボ-dev"
    return "いちよんロボ"


def get_default_bot_role_name(app_env: str) -> str:
    if app_env == "development":
        return "いちよんロボ-dev-role"
    return "いちよんロボ-role"


def get_default_hayusu_bot_nickname(app_env: str) -> str:
    if app_env == "development":
        return "はゆすロボ-dev"
    return "はゆすロボ"


TOKEN = os.getenv("DISCORD_TOKEN")
APP_ENV = get_app_env()
DATA_BACKEND = get_data_backend()
ENABLE_DEV_COMMANDS = get_env_bool("ENABLE_DEV_COMMANDS", False)
DEVELOPER_USER_ID = get_env_int("DEVELOPER_USER_ID")
X_BEARER_TOKEN = get_env_str("X_BEARER_TOKEN", "")
X_SEARCH_ENABLED = get_env_bool("X_SEARCH_ENABLED", False)
X_SEARCH_MAX_RESULTS = get_env_int("X_SEARCH_MAX_RESULTS", 10)
NORMAL_BOT_NICKNAME = get_env_str(
    "NORMAL_BOT_NICKNAME",
    get_default_normal_bot_nickname(APP_ENV),
)
BOT_ROLE_NAME = get_env_str("BOT_ROLE_NAME", get_default_bot_role_name(APP_ENV))
HAYUSU_BOT_NICKNAME = get_env_str(
    "HAYUSU_BOT_NICKNAME",
    get_default_hayusu_bot_nickname(APP_ENV),
)
STARTUP_CHANNEL_ID = get_env_int("STARTUP_CHANNEL_ID")
SCHEDULE_CHANNEL_ID = get_env_int("SCHEDULE_CHANNEL_ID")

STATE_FILE = "data/state.json"
HAYUSU_ENTER_GIF = "assets/transitions/hayusu_enter.gif"
HAYUSU_EXIT_GIF = "assets/transitions/hayusu_exit.gif"
HAYUSU_AVATAR = "assets/avatar_hayusu.png"
NORMAL_AVATAR = "assets/avatar_normal.png"
HAYUSU_MODE_SECONDS = 180
HAYUSU_TRIGGER_RATE = 112
HAYUSU_RESPONSE = "チェルさんこれギャバいっすよ"
HAYUSU_ENTER_MESSAGE = "# はゆすモード\n\n# 突入"
HAYUSU_EXIT_MESSAGE = "# はゆすモード\n\n# 終了"
END_OF_SERVICE_MESSAGE = "サ終やめませんか？"

DEV_COMMAND_KEYWORDS = (
    "はゆすテスト",
    "はゆす終了テスト",
    "年次テスト",
    "6/30テスト",
    "破壊テスト",
    "状態リセット",
    "強制モード変更",
    "debug",
    "test",
    "テスト",
    "デバッグ",
    "強制実行",
)
