import importlib
import shutil
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
REQUIREMENTS_PATH = ROOT_DIR / "requirements.txt"
DOCKERFILE_PATH = ROOT_DIR / "Dockerfile"


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


def main() -> int:
    results = []
    requirements = REQUIREMENTS_PATH.read_text(encoding="utf-8")
    dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")

    results.append(check("Dockerfile uses Python 3.11 slim", "FROM python:3.11-slim" in dockerfile))
    results.append(check("Dockerfile installs ffmpeg", "ffmpeg" in dockerfile))
    results.append(check("discord.py voice extra is pinned", "discord.py[voice]==2.4.0" in requirements))
    results.append(check("PyNaCl is declared", "PyNaCl" in requirements))
    results.append(check("davey is declared", "davey" in requirements))
    results.append(check("yt-dlp is declared", "yt-dlp" in requirements))
    results.append(check("local Python is 3.9 or newer", sys.version_info >= (3, 9), sys.version.split()[0]))

    # Runtime imports are reported for the current environment, but Docker build is the source of truth.
    import_optional("discord")
    import_optional("nacl")
    import_optional("yt_dlp")
    import_optional("davey")
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
