import ast
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
MAIN_PATH = ROOT_DIR / "main.py"


def check(name, ok, detail=""):
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


def find_function(tree, name):
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def first_line(function_node, needle):
    for node in ast.walk(function_node):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == needle:
                return getattr(node, "lineno", 0)
            if isinstance(func, ast.Attribute) and func.attr == needle:
                return getattr(node, "lineno", 0)
    return 0


def main() -> int:
    source = MAIN_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    on_message = find_function(tree, "on_message")
    empty_handler = find_function(tree, "handle_empty_mention_message")

    results = []
    results.append(check("empty mention handler exists", empty_handler is not None))
    results.append(check("on_message exists", on_message is not None))

    if empty_handler is not None:
        db_line = first_line(empty_handler, "handle_db_runtime_message")
        panel_line = first_line(empty_handler, "send_main_panel")
        results.append(check("empty mention tries DB runtime first", db_line > 0, db_line))
        results.append(check("empty mention no longer sends general panel", panel_line == 0, panel_line))
        results.append(check("empty mention handler checks mention text", first_line(empty_handler, "mention_text_is_empty") > 0))

    if on_message is not None:
        empty_line = first_line(on_message, "handle_empty_mention_message")
        music_line = first_line(on_message, "handle_mention_music_links")
        youtube_line = first_line(on_message, "handle_youtube_n_pull_command")
        voice_line = first_line(on_message, "handle_voice_command")
        shortcut_line = first_line(on_message, "handle_mention_shortcut_command")
        panel_line = first_line(on_message, "handle_context_panel_command")
        runtime_line = first_line(on_message, "handle_db_runtime_message")
        results.append(check("empty mention branch runs before music link handling", 0 < empty_line < music_line, (empty_line, music_line)))
        results.append(check("text mention commands remain after empty mention branch", empty_line < youtube_line < voice_line, (empty_line, youtube_line, voice_line)))
        results.append(check("mention shortcuts run before contextual panels", voice_line < shortcut_line < panel_line, (voice_line, shortcut_line, panel_line)))
        results.append(check("general DB runtime remains after command handlers", voice_line < runtime_line, (voice_line, runtime_line)))

    results.append(check("panel handler no longer preempts DB in on_message", "handle_interaction_panel_mention(message, command_text)" not in source))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
