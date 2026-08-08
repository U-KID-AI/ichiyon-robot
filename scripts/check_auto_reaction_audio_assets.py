import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from admin import auto_reactions


def check(name: str, ok: bool, detail: str = "") -> bool:
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


def main() -> int:
    repo_source = (ROOT_DIR / "bot" / "repositories" / "auto_reactions.py").read_text(encoding="utf-8")
    admin_source = (ROOT_DIR / "admin" / "auto_reactions.py").read_text(encoding="utf-8")
    template_source = (ROOT_DIR / "admin" / "templates" / "auto_reaction_form.html").read_text(encoding="utf-8")
    runtime_source = (ROOT_DIR / "bot" / "services" / "runtime_db.py").read_text(encoding="utf-8")
    voice_source = (ROOT_DIR / "bot" / "services" / "voice_audio.py").read_text(encoding="utf-8")
    migration_033 = (ROOT_DIR / "migrations" / "033_add_reaction_audio_config.sql").read_text(encoding="utf-8")

    form = auto_reactions.build_form(
        "trigger",
        "response",
        "",
        "",
        "contains",
        "10",
        "on",
        "42",
        "75",
    )[0]
    blank_form = auto_reactions.build_form("trigger", "", "", "", "contains", "0", "on", "", "")[0]

    results = []
    results.append(check("migration 033 provides reactions.audio_config_json", "ADD COLUMN IF NOT EXISTS audio_config_json" in migration_033))
    results.append(check("repository create stores audio_config_json", "audio_config_json" in repo_source and "COALESCE(%s::JSONB, '{}'::JSONB)" in repo_source))
    results.append(check("repository update stores audio_config_json", "SET trigger_text = %s" in repo_source and "audio_config_json = COALESCE" in repo_source))
    results.append(check("repository keeps bot/guild scope", "WHERE bot_id = %s AND guild_id = %s AND id = %s" in repo_source))
    results.append(check("admin loads scoped audio assets", "AudioAssetRepository(connection, bot_id=current_selected_bot_id()).list_assets(guild_id, enabled=True)" in admin_source))
    results.append(check("admin validates selected scoped audio asset", "get_asset(guild_id, int(asset_id), enabled=True)" in admin_source))
    results.append(check("admin template has audio asset select", 'name="audio_asset_id"' in template_source))
    results.append(check("admin template has volume override", 'name="audio_volume_percent"' in template_source))
    results.append(check("form builds audio asset config", form["audio_config_json"] == {"audio_asset_id": 42, "volume_percent": 75}, str(form["audio_config_json"])))
    results.append(check("blank form keeps audio config empty", blank_form["audio_config_json"] == {}, str(blank_form["audio_config_json"])))
    results.append(check("runtime prefers audio asset playback", "extract_reaction_audio_asset_id" in runtime_source and "play_audio_asset_by_id" in runtime_source))
    results.append(check("voice audio extracts nested audio_asset_id", "extract_audio_asset_id_from_config" in voice_source and "voice.get(\"audio_asset_id\")" in voice_source))
    results.append(check("voice audio uses existing foreground queue", "enqueue_foreground_audio" in voice_source and "DEFAULT_FOREGROUND_VOLUME_PERCENT" in voice_source))

    ok_count = sum(1 for item in results if item)
    print("summary: {0}/{1} OK".format(ok_count, len(results)))
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
