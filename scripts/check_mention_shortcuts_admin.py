import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def check(name, ok, detail=""):
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


def main():
    results = []
    admin_source = (ROOT_DIR / "admin" / "mention_shortcuts.py").read_text(encoding="utf-8")
    main_source = (ROOT_DIR / "admin" / "main.py").read_text(encoding="utf-8")
    servers_source = (ROOT_DIR / "admin" / "servers.py").read_text(encoding="utf-8")
    list_template = (ROOT_DIR / "admin" / "templates" / "mention_shortcuts.html").read_text(encoding="utf-8")
    form_template = (ROOT_DIR / "admin" / "templates" / "mention_shortcut_form.html").read_text(encoding="utf-8")

    results.append(check("admin route registered", "register_mention_shortcut_routes" in main_source and "mention_shortcut_router" in main_source))
    results.append(check("feature list includes mention shortcuts", '"key": "mention_shortcuts"' in servers_source))
    results.append(check("admin access checks selected bot guild", "can_access_guild(guild_id, user[\"user_id\"], bot_id)" in admin_source))
    results.append(check("repository uses current selected bot", "current_selected_bot_id()" in admin_source))
    results.append(check("form has Steam provider", 'value="steam"' in form_template))
    results.append(check("form has ITAD provider", 'value="itad"' in form_template))
    results.append(check("form has Nintendo Switch / NTPrices provider", 'value="ntprices"' in form_template and "Nintendo Switch / NTPrices" in form_template))
    results.append(check("form has audio asset dropdown", "audio_assets" in form_template and "audio_asset_id" in form_template))
    results.append(check("list has toggle and delete", "/toggle" in list_template and "/delete" in list_template))
    results.append(check("admin status mentions Nintendo official price", "Nintendo公式価格" in admin_source or "Nintendo公式価格" in servers_source))
    results.append(check("provider status hides key values", "ITAD_API_KEY" not in list_template and "NTPRICES_API_KEY" not in list_template))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
