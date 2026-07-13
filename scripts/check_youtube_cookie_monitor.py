import asyncio
import os
import sys
import tempfile
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot.services import youtube_cookie_monitor as monitor


def check(name: str, ok: bool, detail: str = "") -> bool:
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


async def main_async() -> int:
    results = []
    results.append(check("login required is classified", monitor.classify_ytdlp_error(RuntimeError("Please sign in")) == monitor.COOKIE_STATUS_LOGIN_REQUIRED))
    results.append(check("bot check is classified", monitor.classify_ytdlp_error(RuntimeError("Sign in to confirm you're not a bot")) == monitor.COOKIE_STATUS_BOT_CHECK))
    results.append(check("captcha is classified", monitor.classify_ytdlp_error(RuntimeError("captcha required")) == monitor.COOKIE_STATUS_CAPTCHA_REQUIRED))
    results.append(check("network is classified", monitor.classify_ytdlp_error(RuntimeError("connection timed out")) == monitor.COOKIE_STATUS_NETWORK_ERROR))

    original_env = {key: os.environ.get(key) for key in (monitor.YTDLP_COOKIE_CHECK_URL, monitor.YTDLP_COOKIES_FILE_ENV)}
    try:
        os.environ.pop(monitor.YTDLP_COOKIE_CHECK_URL, None)
        os.environ.pop(monitor.YTDLP_COOKIES_FILE_ENV, None)
        result = await monitor.check_youtube_cookie_status("check-script")
        results.append(check("missing URL skips safely", result.status == monitor.COOKIE_STATUS_NOT_CONFIGURED, result.status))

        os.environ[monitor.YTDLP_COOKIE_CHECK_URL] = "https://www.youtube.com/watch?v=dummy"
        os.environ[monitor.YTDLP_COOKIES_FILE_ENV] = str(ROOT_DIR / "missing-youtube-cookies.txt")
        result = await monitor.check_youtube_cookie_status("check-script")
        results.append(check("missing cookie file is classified", result.status == monitor.COOKIE_STATUS_FILE_MISSING, result.status))

        with tempfile.TemporaryDirectory() as tmp_dir:
            cookie_path = Path(tmp_dir) / "youtube-cookies.txt"
            cookie_path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            os.environ[monitor.YTDLP_COOKIES_FILE_ENV] = str(cookie_path)

            def ok_check(url, cookie_file):
                assert url
                assert cookie_file is not None
                assert str(cookie_file).startswith(str(Path(tempfile.gettempdir())))

            result = await monitor.check_youtube_cookie_status("check-script", ok_check)
            results.append(check("valid cookie check succeeds with tmp copy", result.status == monitor.COOKIE_STATUS_OK and result.ok, result.status))

            def bot_check(_url, _cookie_file):
                raise RuntimeError("Sign in to confirm you're not a bot")

            result = await monitor.check_youtube_cookie_status("check-script", bot_check)
            results.append(check("bot check failure is classified", result.status in monitor.AUTH_FAILURE_STATUSES, result.status))

        update = await monitor.try_update_youtube_cookie()
        results.append(check("auto update is safely unconfigured", update.status == monitor.COOKIE_STATUS_UPDATE_NOT_CONFIGURED, update.status))
    finally:
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    ok_count = sum(1 for item in results if item)
    print("summary: {0}/{1} OK".format(ok_count, len(results)))
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
