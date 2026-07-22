import importlib
import importlib.metadata
import shutil
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
REQUIREMENTS_PATH = ROOT_DIR / "requirements.txt"
DOCKERFILE_PATH = ROOT_DIR / "Dockerfile"
VOICE_MUSIC_PATH = ROOT_DIR / "bot" / "services" / "voice_music.py"
COOKIE_MONITOR_PATH = ROOT_DIR / "bot" / "services" / "youtube_cookie_monitor.py"


def check(name: str, ok: bool, detail: str = "") -> bool:
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


def warn(name: str, detail: str) -> None:
    print("[WARN] {0} - {1}".format(name, detail))


def import_optional(module_name: str) -> bool:
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        warn("{0} import".format(module_name), str(exc))
        return False
    version = getattr(module, "__version__", "")
    print("[OK] {0} import{1}".format(module_name, " - {0}".format(version) if version else ""))
    return True


def distribution_optional(distribution_name: str) -> bool:
    try:
        package_version = importlib.metadata.version(distribution_name)
    except importlib.metadata.PackageNotFoundError as exc:
        warn("{0} distribution".format(distribution_name), str(exc))
        return False
    print("[OK] {0} distribution - {1}".format(distribution_name, package_version))
    return True


def main() -> int:
    results = []
    requirements = REQUIREMENTS_PATH.read_text(encoding="utf-8")
    dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")
    voice_music = VOICE_MUSIC_PATH.read_text(encoding="utf-8")
    cookie_monitor = COOKIE_MONITOR_PATH.read_text(encoding="utf-8")

    results.append(check("Dockerfile uses Python 3.11 slim", "FROM python:3.11-slim" in dockerfile))
    results.append(check("Dockerfile installs ffmpeg", "ffmpeg" in dockerfile))
    results.append(check("Dockerfile installs Deno prerequisites", "curl" in dockerfile and "unzip" in dockerfile))
    results.append(check("Dockerfile installs Deno", "deno.land/install.sh" in dockerfile and "deno --version" in dockerfile))
    results.append(check("Dockerfile does not rely on nodejs for yt-dlp JS runtime", "nodejs" not in dockerfile.lower()))
    results.append(check("discord.py voice extra is pinned", "discord.py[voice]==2.7.1" in requirements))
    results.append(check("PyNaCl is declared", "PyNaCl" in requirements))
    results.append(check("davey is declared", "davey" in requirements))
    results.append(check("yt-dlp default extra is pinned", "yt-dlp[default]==2026.7.4" in requirements))
    results.append(check("yt-dlp-ejs is supplied by yt-dlp default extra", "yt-dlp-ejs" not in requirements))
    results.append(check("voice yt-dlp keeps Deno JS runtime", '"js_runtimes": {"deno": {}}' in voice_music))
    results.append(check("voice yt-dlp does not use remote ejs component", "remote_components" not in voice_music and "ejs:github" not in voice_music))
    results.append(check("cookie monitor keeps Deno JS runtime", '"js_runtimes": {"deno": {}}' in cookie_monitor))
    results.append(check("cookie monitor does not use remote ejs component", "remote_components" not in cookie_monitor and "ejs:github" not in cookie_monitor))
    results.append(check("local Python is 3.9 or newer", sys.version_info >= (3, 9), sys.version.split()[0]))

    # Runtime imports are reported for the current environment, but Docker build is the source of truth.
    import_optional("discord")
    import_optional("nacl")
    import_optional("yt_dlp")
    import_optional("davey")
    distribution_optional("yt-dlp-ejs")
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        print("[OK] ffmpeg found - {0}".format(ffmpeg_path))
    else:
        warn("ffmpeg found", "not available in current PATH; Dockerfile installs it")

    ok_count = sum(1 for item in results if item)
    print("summary: {0}/{1} required checks OK".format(ok_count, len(results)))
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
