from pathlib import Path
import subprocess
import sys


ROOT_DIR = Path(__file__).resolve().parent.parent


def check(name: str, ok: bool, detail: str = "") -> bool:
    line = "[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else "")
    print(line.encode("cp932", errors="backslashreplace").decode("cp932"))
    return ok


def read(path: str) -> str:
    return (ROOT_DIR / path).read_text(encoding="utf-8")


def main() -> int:
    results = []
    dockerfile = read("Dockerfile")
    requirements = read("requirements.txt")
    compose = read("docker-compose.yml")
    service_source = read("bot/services/youtube_n_pull.py")
    admin_source = read("admin/youtube_n_pull.py")

    admin_block = compose.split("  admin:", 1)[1].split("\n  bot:", 1)[0]
    flat_options = service_source.split("YTDL_FLAT_OPTIONS = {", 1)[1].split("}", 1)[0]
    refresh_block = admin_source.split('async def refresh_youtube_n_pull', 1)[1].split("\ndef require_editor", 1)[0]

    results.append(check("Dockerfile uses Python 3.11 runtime", "FROM python:3.11-slim" in dockerfile))
    results.append(check("requirements includes yt-dlp", "yt-dlp" in requirements))
    results.append(check("admin compose passes YTDLP_COOKIES_FILE", "YTDLP_COOKIES_FILE: ${YTDLP_COOKIES_FILE:-}" in admin_block))
    results.append(check("admin compose mounts secrets read-only", "./secrets:/app/secrets:ro" in admin_block))
    results.append(check("admin compose does not expose cookie values", "youtube-cookies.txt" not in admin_block))

    results.append(check("list yt-dlp options omit noplaylist", '"noplaylist"' not in flat_options, flat_options))
    results.append(check("list yt-dlp options keep extract_flat", '"extract_flat": True' in flat_options, flat_options))
    results.append(check("list yt-dlp options keep skip_download", '"skip_download": True' in flat_options, flat_options))
    results.append(check("list yt-dlp options keep ignoreerrors", '"ignoreerrors": True' in flat_options, flat_options))
    results.append(check("list yt-dlp options keep playlistend limit", '"playlistend": 500' in flat_options, flat_options))
    results.append(check("list yt-dlp options do not select audio format", '"format"' not in flat_options, flat_options))
    results.append(check("list yt-dlp options do not require deno JS runtime", '"js_runtimes"' not in flat_options and '"remote_components"' not in flat_options, flat_options))

    import_probe = """
import importlib.abc
import sys

class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in ("bot.services.voice_music", "bot.services.youtube_cookie_monitor"):
            raise ImportError("blocked " + fullname)
        return None

sys.meta_path.insert(0, Blocker())
import admin.youtube_n_pull as module
assert "bot.services.voice_music" not in sys.modules
assert "bot.services.youtube_cookie_monitor" not in sys.modules
assert callable(module.is_youtube_source_url)
assert callable(module.fetch_source_videos)
print("admin import ok")
"""
    probe = subprocess.run([sys.executable, "-c", import_probe], cwd=str(ROOT_DIR), text=True, capture_output=True)
    results.append(check("admin import keeps voice dependencies lazy", probe.returncode == 0, (probe.stdout + probe.stderr).strip()))

    results.append(check("admin refresh rejects zero-video success", "if not dedup:" in refresh_block and "no valid youtube videos found" in refresh_block))
    results.append(check("admin refresh replaces cache only after successful dedup", refresh_block.find("if not dedup:") < refresh_block.find("replace_cache_videos")))
    results.append(check("admin refresh logs traceback on failure", "logger.exception(" in refresh_block and "youtube_n_pull admin refresh failed" in refresh_block))
    results.append(check("admin refresh records error type", "mark_cache_refresh(preset_id, type(exc).__name__)" in refresh_block))
    results.append(check("admin refresh UI uses safe message", "ADMIN_REFRESH_ERROR_MESSAGE" in refresh_block and "format(type(exc).__name__)" not in refresh_block))
    results.append(check("admin refresh keeps secrets out of UI", "cookie" not in admin_source.lower() and "token" not in admin_source.lower()))

    ok_count = sum(1 for result in results if result)
    print("summary: {0}/{1} OK".format(ok_count, len(results)))
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
