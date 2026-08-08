import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot.services import interaction_panel


def check(name, ok, detail=""):
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


def main() -> int:
    results = []
    results.append(check("mention-only empty command is detected", interaction_panel.mention_text_is_empty("")))
    results.append(check("mention-only whitespace is detected", interaction_panel.mention_text_is_empty(" \t")))
    results.append(check("non-empty mention is not empty", not interaction_panel.mention_text_is_empty("歌え https://youtu.be/x")))
    results.append(check("empty mention is not panel command", interaction_panel.panel_command_kind("") is None))
    results.append(check("game command opens game panel", interaction_panel.panel_command_kind("ゲーム") == "game"))
    results.append(check("audio command opens audio panel", interaction_panel.panel_command_kind("SE") == "audio"))
    results.append(check("music command opens music panel", interaction_panel.panel_command_kind("音楽") == "music"))
    results.append(check("shortcut text is not panel command", interaction_panel.panel_command_kind("ニコロデオン") is None))
    view = interaction_panel.MainPanelView()
    button_ids = [item.custom_id for item in view.children if hasattr(item, "custom_id")]
    results.append(check("main panel has music button", any("main:music" in item for item in button_ids)))
    results.append(check("main panel has audio button", any("main:audio" in item for item in button_ids)))
    results.append(check("main panel has game button", any("main:game" in item for item in button_ids)))
    results.append(check("main panel has status button", any("main:status" in item for item in button_ids)))
    results.append(check("main panel has close button", any("main:close" in item for item in button_ids)))
    results.append(check("custom ids are persistent scoped", all(item.startswith("ichiyon_panel:") for item in button_ids)))
    music_view = interaction_panel.MusicPanelView()
    music_ids = [item.custom_id for item in music_view.children if hasattr(item, "custom_id")]
    for required in ("join", "pause", "resume", "skip", "stop", "now", "queue", "loop", "shuffle", "volume", "add", "back"):
        results.append(check("music button {0}".format(required), any("music:{0}".format(required) in item for item in music_ids)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
