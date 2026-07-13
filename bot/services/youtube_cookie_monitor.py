import asyncio
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from bot import config


COOKIE_STATUS_OK = "OK"
COOKIE_STATUS_NOT_CONFIGURED = "COOKIE_NOT_CONFIGURED"
COOKIE_STATUS_FILE_MISSING = "COOKIE_FILE_MISSING"
COOKIE_STATUS_FILE_UNREADABLE = "COOKIE_FILE_UNREADABLE"
COOKIE_STATUS_INVALID = "COOKIE_INVALID"
COOKIE_STATUS_LOGIN_REQUIRED = "LOGIN_REQUIRED"
COOKIE_STATUS_BOT_CHECK = "BOT_CHECK"
COOKIE_STATUS_CAPTCHA_REQUIRED = "CAPTCHA_REQUIRED"
COOKIE_STATUS_ACCOUNT_RESTRICTED = "ACCOUNT_RESTRICTED"
COOKIE_STATUS_VIDEO_UNAVAILABLE = "VIDEO_UNAVAILABLE"
COOKIE_STATUS_NETWORK_ERROR = "NETWORK_ERROR"
COOKIE_STATUS_UNKNOWN_ERROR = "UNKNOWN_ERROR"
COOKIE_STATUS_UPDATE_NOT_CONFIGURED = "UPDATE_NOT_CONFIGURED"

AUTH_FAILURE_STATUSES = {
    COOKIE_STATUS_INVALID,
    COOKIE_STATUS_LOGIN_REQUIRED,
    COOKIE_STATUS_BOT_CHECK,
    COOKIE_STATUS_CAPTCHA_REQUIRED,
    COOKIE_STATUS_ACCOUNT_RESTRICTED,
}

YTDLP_COOKIE_CHECK_ENABLED = "YTDLP_COOKIE_CHECK_ENABLED"
YTDLP_COOKIE_CHECK_TIME = "YTDLP_COOKIE_CHECK_TIME"
YTDLP_COOKIE_CHECK_TIMEZONE = "YTDLP_COOKIE_CHECK_TIMEZONE"
YTDLP_COOKIE_CHECK_URL = "YTDLP_COOKIE_CHECK_URL"
YTDLP_COOKIE_RETRY_COOLDOWN_SECONDS = "YTDLP_COOKIE_RETRY_COOLDOWN_SECONDS"
YTDLP_ALERT_CHANNEL_ID = "YTDLP_ALERT_CHANNEL_ID"
YTDLP_COOKIE_CHECK_OWNER_BOT_ID = "YTDLP_COOKIE_CHECK_OWNER_BOT_ID"
YTDLP_COOKIES_FILE_ENV = "YTDLP_COOKIES_FILE"


@dataclass
class CookieMonitorState:
    status: str = COOKIE_STATUS_NOT_CONFIGURED
    last_checked_at: Optional[datetime] = None
    last_success_at: Optional[datetime] = None
    last_error_status: str = ""
    last_update_attempt_at: Optional[datetime] = None
    last_update_success_at: Optional[datetime] = None
    last_scheduled_date: str = ""
    auto_update_configured: bool = False


@dataclass
class CookieCheckResult:
    status: str
    ok: bool = False
    message: str = ""


COOKIE_MONITOR_STATE = CookieMonitorState()
_CHECK_LOCK = asyncio.Lock()
_UPDATE_LOCK = asyncio.Lock()
_LAST_TRANSIENT_CHECK_AT: Optional[datetime] = None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def cookie_file_path() -> str:
    return str(os.getenv(YTDLP_COOKIES_FILE_ENV) or "").strip()


def cookie_check_enabled() -> bool:
    return str(os.getenv(YTDLP_COOKIE_CHECK_ENABLED, "false")).strip().lower() in ("1", "true", "yes", "on")


def cookie_check_url() -> str:
    return str(os.getenv(YTDLP_COOKIE_CHECK_URL) or "").strip()


def alert_channel_id() -> str:
    return str(os.getenv(YTDLP_ALERT_CHANNEL_ID) or "").strip()


def cookie_check_owner_bot_id() -> str:
    return str(os.getenv(YTDLP_COOKIE_CHECK_OWNER_BOT_ID, "ichiyon") or "ichiyon").strip() or "ichiyon"


def is_cookie_check_owner_bot() -> bool:
    return config.BOT_INSTANCE_ID == cookie_check_owner_bot_id()


def retry_cooldown_seconds() -> int:
    try:
        return max(0, int(os.getenv(YTDLP_COOKIE_RETRY_COOLDOWN_SECONDS, "1800")))
    except ValueError:
        return 1800


def check_timezone() -> ZoneInfo:
    name = str(os.getenv(YTDLP_COOKIE_CHECK_TIMEZONE, "Asia/Tokyo") or "Asia/Tokyo").strip()
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("Asia/Tokyo")


def parse_check_time() -> time:
    raw = str(os.getenv(YTDLP_COOKIE_CHECK_TIME, "04:30") or "04:30").strip()
    try:
        hour_text, minute_text = raw.split(":", 1)
        return time(hour=max(0, min(23, int(hour_text))), minute=max(0, min(59, int(minute_text))))
    except Exception:
        return time(hour=4, minute=30)


def classify_ytdlp_error(error: Exception) -> str:
    message = str(error or "").lower()
    if "not a bot" in message or "cookies-from-browser" in message or "use --cookies" in message:
        return COOKIE_STATUS_BOT_CHECK
    if "sign in to confirm" in message or "login required" in message or "please sign in" in message:
        return COOKIE_STATUS_LOGIN_REQUIRED
    if "captcha" in message:
        return COOKIE_STATUS_CAPTCHA_REQUIRED
    if "account" in message and ("restricted" in message or "disabled" in message or "suspended" in message):
        return COOKIE_STATUS_ACCOUNT_RESTRICTED
    if "cookie" in message and ("invalid" in message or "expired" in message):
        return COOKIE_STATUS_INVALID
    if "private video" in message or "video unavailable" in message or "removed" in message:
        return COOKIE_STATUS_VIDEO_UNAVAILABLE
    if "timed out" in message or "network" in message or "connection" in message or "temporary failure" in message:
        return COOKIE_STATUS_NETWORK_ERROR
    return COOKIE_STATUS_UNKNOWN_ERROR


def _copy_cookie_to_tmp(source: Path, suffix: str = "check") -> Path:
    target = Path(tempfile.gettempdir()) / "ichiyon-ytdlp-cookie-{0}.txt".format(suffix)
    shutil.copyfile(source, target)
    return target


def _build_check_options(cookie_path: Optional[Path]) -> dict:
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "format": "bestaudio/best",
        "js_runtimes": {"deno": {}},
        "remote_components": ["ejs:github"],
    }
    if cookie_path is not None:
        options["cookiefile"] = str(cookie_path)
    return options


def _default_ytdlp_check(url: str, cookie_path: Optional[Path]) -> None:
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError("yt-dlp is not installed") from exc
    with yt_dlp.YoutubeDL(_build_check_options(cookie_path)) as ydl:
        ydl.extract_info(url, download=False)


async def check_youtube_cookie_status(
    reason: str = "manual",
    check_func: Optional[Callable[[str, Optional[Path]], None]] = None,
) -> CookieCheckResult:
    async with _CHECK_LOCK:
        now = utc_now()
        COOKIE_MONITOR_STATE.last_checked_at = now
        url = cookie_check_url()
        if not url:
            COOKIE_MONITOR_STATE.status = COOKIE_STATUS_NOT_CONFIGURED
            COOKIE_MONITOR_STATE.last_error_status = COOKIE_STATUS_NOT_CONFIGURED
            print("[WARN] youtube cookie check skipped: reason={0} status={1}".format(reason, COOKIE_STATUS_NOT_CONFIGURED))
            return CookieCheckResult(COOKIE_STATUS_NOT_CONFIGURED, False, "検査URLが未設定です。")

        configured_cookie = cookie_file_path()
        if not configured_cookie:
            COOKIE_MONITOR_STATE.status = COOKIE_STATUS_NOT_CONFIGURED
            COOKIE_MONITOR_STATE.last_error_status = COOKIE_STATUS_NOT_CONFIGURED
            return CookieCheckResult(COOKIE_STATUS_NOT_CONFIGURED, False, "Cookieファイルが未設定です。")

        source = Path(configured_cookie)
        if not source.exists():
            COOKIE_MONITOR_STATE.status = COOKIE_STATUS_FILE_MISSING
            COOKIE_MONITOR_STATE.last_error_status = COOKIE_STATUS_FILE_MISSING
            return CookieCheckResult(COOKIE_STATUS_FILE_MISSING, False, "Cookieファイルが見つかりません。")
        if not source.is_file():
            COOKIE_MONITOR_STATE.status = COOKIE_STATUS_FILE_UNREADABLE
            COOKIE_MONITOR_STATE.last_error_status = COOKIE_STATUS_FILE_UNREADABLE
            return CookieCheckResult(COOKIE_STATUS_FILE_UNREADABLE, False, "Cookieファイルを読めません。")

        tmp_cookie = None
        try:
            tmp_cookie = _copy_cookie_to_tmp(source, "check")
            runner = check_func or _default_ytdlp_check
            await asyncio.to_thread(runner, url, tmp_cookie)
            COOKIE_MONITOR_STATE.status = COOKIE_STATUS_OK
            COOKIE_MONITOR_STATE.last_success_at = now
            COOKIE_MONITOR_STATE.last_error_status = ""
            print("[INFO] youtube cookie check ok: reason={0}".format(reason))
            return CookieCheckResult(COOKIE_STATUS_OK, True, "Cookie認証は正常です。")
        except PermissionError:
            COOKIE_MONITOR_STATE.status = COOKIE_STATUS_FILE_UNREADABLE
            COOKIE_MONITOR_STATE.last_error_status = COOKIE_STATUS_FILE_UNREADABLE
            return CookieCheckResult(COOKIE_STATUS_FILE_UNREADABLE, False, "Cookieファイルを読めません。")
        except Exception as exc:
            status = classify_ytdlp_error(exc)
            COOKIE_MONITOR_STATE.status = status
            COOKIE_MONITOR_STATE.last_error_status = status
            print("[WARN] youtube cookie check failed: reason={0} status={1}".format(reason, status))
            return CookieCheckResult(status, False, "Cookie検査に失敗しました。")
        finally:
            if tmp_cookie is not None:
                try:
                    tmp_cookie.unlink(missing_ok=True)
                except Exception:
                    pass


async def maybe_run_scheduled_cookie_check(bot) -> None:
    if not cookie_check_enabled():
        return
    if not is_cookie_check_owner_bot():
        return
    tz = check_timezone()
    now_local = datetime.now(tz)
    scheduled_time = parse_check_time()
    today = now_local.date().isoformat()
    if COOKIE_MONITOR_STATE.last_scheduled_date == today:
        return
    if now_local.time() < scheduled_time:
        return
    COOKIE_MONITOR_STATE.last_scheduled_date = today
    result = await check_youtube_cookie_status("scheduled")
    if not result.ok and result.status != COOKIE_STATUS_NOT_CONFIGURED:
        await notify_youtube_cookie_status(bot, result.status)


def transient_retry_allowed() -> bool:
    global _LAST_TRANSIENT_CHECK_AT
    now = utc_now()
    if _LAST_TRANSIENT_CHECK_AT is not None and now - _LAST_TRANSIENT_CHECK_AT < timedelta(seconds=retry_cooldown_seconds()):
        return False
    _LAST_TRANSIENT_CHECK_AT = now
    return True


async def handle_transient_auth_failure(bot, status: str) -> CookieCheckResult:
    if status not in AUTH_FAILURE_STATUSES:
        return CookieCheckResult(status, False, "")
    if not transient_retry_allowed():
        return CookieCheckResult(status, False, "認証確認のクールダウン中です。")
    result = await check_youtube_cookie_status("transient_auth_failure")
    if result.status in AUTH_FAILURE_STATUSES:
        update = await try_update_youtube_cookie()
        if not update.ok:
            await notify_youtube_cookie_status(bot, update.status)
    return result


async def try_update_youtube_cookie() -> CookieCheckResult:
    async with _UPDATE_LOCK:
        COOKIE_MONITOR_STATE.last_update_attempt_at = utc_now()
        COOKIE_MONITOR_STATE.auto_update_configured = False
        print("[WARN] youtube cookie auto update is not configured")
        return CookieCheckResult(COOKIE_STATUS_UPDATE_NOT_CONFIGURED, False, "自動更新は未設定です。")


async def notify_youtube_cookie_status(bot, status: str) -> None:
    if bot is None:
        return
    channel_id = alert_channel_id()
    if not channel_id:
        return
    try:
        channel = bot.get_channel(int(channel_id))
    except (TypeError, ValueError):
        channel = None
    if channel is None or not hasattr(channel, "send"):
        return
    await channel.send("YouTube Cookie状態: {0}".format(status))


def format_cookie_monitor_status() -> str:
    path = cookie_file_path()
    configured = "あり" if path else "なし"
    if not path:
        file_status = "未設定"
    else:
        source = Path(path)
        if source.is_file():
            file_status = "利用可能"
        elif source.exists():
            file_status = "読み取り不可"
        else:
            file_status = "見つかりません"
    return "\n".join(
        [
            "YouTube状態",
            "- 通常抽出: {0}".format("利用可能" if COOKIE_MONITOR_STATE.status == COOKIE_STATUS_OK else "未確認または要確認"),
            "- Cookie設定: {0}".format(configured),
            "- Cookieファイル: {0}".format(file_status),
            "- Cookie認証状態: {0}".format(COOKIE_MONITOR_STATE.status),
            "- 最終検査日時: {0}".format(COOKIE_MONITOR_STATE.last_checked_at.isoformat() if COOKIE_MONITOR_STATE.last_checked_at else "未実行"),
            "- 最終成功日時: {0}".format(COOKIE_MONITOR_STATE.last_success_at.isoformat() if COOKIE_MONITOR_STATE.last_success_at else "未実行"),
            "- 最後のエラー分類: {0}".format(COOKIE_MONITOR_STATE.last_error_status or "なし"),
            "- 自動更新: {0}".format("設定済み" if COOKIE_MONITOR_STATE.auto_update_configured else "未設定"),
            "- 定期チェック担当Bot: {0}".format(cookie_check_owner_bot_id()),
        ]
    )
