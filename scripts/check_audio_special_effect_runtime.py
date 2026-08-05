import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from admin import special_effects


def check(name, ok, detail=""):
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


def main() -> int:
    results = []
    results.append(check("audio_asset is selectable effect type", "audio_asset" in special_effects.EFFECT_TYPES))
    config, errors = special_effects.build_audio_asset_effect_config({"keep": "value"}, "123", "45")
    results.append(check("audio config accepts asset id", config.get("audio_asset_id") == 123, config))
    results.append(check("audio config accepts volume", config.get("volume_percent") == 45, config))
    results.append(check("audio config preserves unknown keys", config.get("keep") == "value", config))
    results.append(check("audio config defaults foreground", config.get("foreground") is True, config))
    results.append(check("audio config has no errors", not errors, errors))
    invalid_config, invalid_errors = special_effects.build_audio_asset_effect_config({}, "", "200")
    results.append(check("audio config rejects missing asset", any("音声" in err for err in invalid_errors), invalid_errors))
    results.append(check("audio config rejects bad volume", any("音量" in err for err in invalid_errors), invalid_errors))
    runtime_source = Path("bot/services/runtime_db.py").read_text(encoding="utf-8")
    results.append(check("runtime executes audio_asset effects", 'effect_type == "audio_asset"' in runtime_source))
    results.append(check("runtime uses play_audio_asset_by_id", "play_audio_asset_by_id" in runtime_source))
    template = Path("admin/templates/special_effect_form.html").read_text(encoding="utf-8")
    results.append(check("special effect form has audio section", 'data-effect-section="audio_asset"' in template))
    migration = Path("migrations/039_add_audio_assets.sql").read_text(encoding="utf-8")
    results.append(check("migration allows audio_asset effect type", "'audio_asset'" in migration))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
