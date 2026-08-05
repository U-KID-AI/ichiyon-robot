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
    results.append(check("mention-only empty command opens panel", interaction_panel.mention_text_is_empty("")))
    results.append(check("mention-only whitespace opens panel", interaction_panel.mention_text_is_empty(" \t")))
    results.append(check("non-empty mention does not open panel", not interaction_panel.mention_text_is_empty("歌え https://youtu.be/x")))
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
