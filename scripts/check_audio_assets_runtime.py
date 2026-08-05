import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot.repositories.audio_assets import AudioAssetRepository
from bot.services import voice_audio


def check(name, ok, detail=""):
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


def main() -> int:
    results = []
    results.append(check("managed root is data audio-assets", "audio-assets" in str(voice_audio.MANAGED_AUDIO_ROOT)))
    results.append(check("m4a supported", ".m4a" in voice_audio.SUPPORTED_AUDIO_EXTENSIONS))
    results.append(check("mp3 supported", ".mp3" in voice_audio.SUPPORTED_AUDIO_EXTENSIONS))
    results.append(check("path traversal rejected", voice_audio.resolve_audio_asset_storage_path("../secret.mp3") is None))
    results.append(check("absolute path rejected", voice_audio.resolve_audio_asset_storage_path("/tmp/a.mp3") is None))
    safe = voice_audio.resolve_audio_asset_storage_path("ichiyon/guild/file.mp3")
    results.append(check("scoped relative path accepted", safe is not None and "audio-assets" in str(safe)))
    storage = voice_audio.build_audio_asset_storage_path("ichiyon", "guild", "../../bad name.wav")
    results.append(check("storage path strips directories", ".." not in storage and storage.startswith("ichiyon/guild/")))
    repo_source = Path("bot/repositories/audio_assets.py").read_text(encoding="utf-8")
    results.append(check("repository filters by bot_id and guild_id", "bot_id = %s" in repo_source and "guild_id = %s" in repo_source))
    migration = Path("migrations/039_add_audio_assets.sql").read_text(encoding="utf-8")
    results.append(check("migration creates audio_assets", "CREATE TABLE IF NOT EXISTS audio_assets" in migration))
    results.append(check("migration volume constraint", "audio_assets_default_volume_range" in migration))
    results.append(check("special effect allows audio_asset", "'audio_asset'" in migration))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
