import ast
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent


FORBIDDEN_CODE_MARKERS = (
    "accounts.spotify.com",
    "api.spotify.com",
    "client_credentials",
    "grant_type",
    "access_token",
    "SpotifyClient",
    "SpotifyToken",
    "get_spotify_client",
    "Authorization",
)

FORBIDDEN_CONFIG_MARKERS = (
    "SPOTIFY_CLIENT_ID",
    "SPOTIFY_CLIENT_SECRET",
    "SPOTIFY_MARKET",
)

REGEX_METHODS = {"search", "match", "fullmatch", "compile", "sub", "subn", "findall", "finditer", "split"}
REDACTION_PATTERN_MARKERS = {"access_token", "Authorization"}


def check(name: str, ok: bool, detail: str = "") -> bool:
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


def read(path: str) -> str:
    return (ROOT_DIR / path).read_text(encoding="utf-8")


def marker_absent(paths, markers):
    findings = []
    for path in paths:
        text = read(path)
        for marker in markers:
            if marker in text:
                findings.append("{0}:{1}".format(path, marker))
    return findings


def code_marker_findings(source: str):
    tree = ast.parse(source)
    regex_modules = set()
    regex_functions = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            regex_modules.update(alias.asname or alias.name for alias in node.names if alias.name == "re")
        elif isinstance(node, ast.ImportFrom) and node.module == "re":
            regex_functions.update(alias.asname or alias.name for alias in node.names if alias.name in REGEX_METHODS)

    pattern_nodes = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        is_regex = (
            isinstance(function, ast.Attribute) and isinstance(function.value, ast.Name)
            and function.value.id in regex_modules and function.attr in REGEX_METHODS
        ) or (isinstance(function, ast.Name) and function.id in regex_functions)
        if is_regex:
            pattern = node.args[0] if node.args else next(
                (keyword.value for keyword in node.keywords if keyword.arg == "pattern"), None
            )
            if isinstance(pattern, ast.Constant) and isinstance(pattern.value, str):
                pattern_nodes.add(id(pattern))

    findings = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
        elif isinstance(node, ast.Name):
            value = node.id
        elif isinstance(node, (ast.Attribute, ast.keyword, ast.arg)):
            value = getattr(node, "attr", None) or getattr(node, "arg", None) or ""
        elif isinstance(node, ast.alias):
            value = node.name + " " + (node.asname or "")
        elif isinstance(node, ast.ImportFrom):
            value = node.module or ""
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            value = node.name
        else:
            continue
        for marker in FORBIDDEN_CODE_MARKERS:
            # Matching a sensitive word is not credential use. Exempt only the
            # regex pattern argument, never the function body or other arguments.
            if id(node) in pattern_nodes and marker in REDACTION_PATTERN_MARKERS:
                continue
            if marker in value:
                findings.add((node.lineno, marker))
    return sorted(findings)


def check_semantic_regressions():
    cases = [
        ("redaction search pattern", 'import re\nif re.search(r"access_token|Authorization", text):\n    text = "[redacted]"', set()),
        ("redaction substitution pattern", 'import re\ntext = re.sub(r"access_token=[^ ]+", "[redacted]", text)', set()),
        ("aliased regex pattern", 'import re as regex\nregex.compile(pattern=r"access_token|Authorization")', set()),
        ("imported regex pattern", 'from re import search as matches\nmatches("access_token", text)', set()),
        ("comment is not authentication", '# access_token is redacted\nvalue = 1', set()),
        ("token response lookup", 'value = response.json()["access_token"]', {"access_token"}),
        ("token variable", 'access_token = response.text', {"access_token"}),
        ("token keyword", 'authenticate(access_token=value)', {"access_token"}),
        ("authorization header", 'headers = {"Authorization": "Bearer " + value}', {"Authorization"}),
        ("credentials grant", 'data = {"grant_type": "client_credentials"}', {"grant_type", "client_credentials"}),
        ("Spotify token endpoint", 'post("https://accounts.spotify.com/api/token")', {"accounts.spotify.com"}),
        ("Spotify API endpoint", 'get("https://api.spotify.com/v1/tracks")', {"api.spotify.com"}),
        ("Spotify client import", 'from legacy import SpotifyClient, SpotifyToken, get_spotify_client', {"SpotifyClient", "SpotifyToken", "get_spotify_client"}),
        ("Spotify client alias", 'from legacy import Client as SpotifyClient', {"SpotifyClient"}),
        ("Spotify client module", 'from SpotifyClient import Client', {"SpotifyClient"}),
        ("non-regex search is still checked", 'client.search("access_token")', {"access_token"}),
        ("regex replacement is still checked", 'import re\nre.sub("pattern", "access_token", text)', {"access_token"}),
        ("regex input token lookup is still checked", 'import re\nre.search("access_token", response["access_token"])', {"access_token"}),
        ("redactor body is still checked", 'import re\ndef sanitize_playback_log_message(text):\n    re.search("access_token", text)\n    return response["access_token"]', {"access_token"}),
        ("Spotify endpoint in regex is still checked", 'import re\nre.search("accounts.spotify.com", text)', {"accounts.spotify.com"}),
    ]
    results = [check("semantic checker: " + name, {marker for _, marker in code_marker_findings(source)} == expected)
               for name, source, expected in cases]
    try:
        code_marker_findings("def broken(")
    except SyntaxError:
        results.append(check("semantic checker rejects invalid Python", True))
    else:
        results.append(check("semantic checker rejects invalid Python", False))
    return results


def main() -> int:
    code_paths = [
        "bot/services/spotify_client.py",
        "bot/services/spotify_public.py",
        "bot/services/voice_music.py",
        "bot/services/spotify_playlist/public_embed.py",
        "bot/services/spotify_playlist/__init__.py",
    ]
    config_paths = [
        "docker-compose.yml",
        ".env.example",
        ".env.stg.example",
        "docs/voice-vc-commands.md",
    ]
    deleted_paths = [
        ROOT_DIR / "bot/services/spotify_playlist/official_api.py",
        ROOT_DIR / "bot/services/spotify_playlist/resolver.py",
    ]

    code_findings = ["{0}:{1}:{2}".format(path, line, marker)
                     for path in code_paths for line, marker in code_marker_findings(read(path))]
    config_findings = marker_absent(config_paths, FORBIDDEN_CONFIG_MARKERS)
    results = [
        check("official spotify api module is removed", not deleted_paths[0].exists()),
        check("spotify api fallback resolver module is removed", not deleted_paths[1].exists()),
        check("runtime code has no spotify web api/credential use", not code_findings, ", ".join(code_findings)),
        check("compose/env/docs have no spotify client credentials", not config_findings, ", ".join(config_findings)),
        check("public resolver is still present", "class SpotifyPublicResolver" in read("bot/services/spotify_public.py")),
        check("voice music uses public resolver", "get_spotify_public_resolver" in read("bot/services/voice_music.py")),
        check("spotify metadata models remain", "class SpotifyTrackMetadata" in read("bot/services/spotify_client.py")),
    ]
    results.extend(check_semantic_regressions())
    print("spotify web api removal checks: {0}/{1}".format(sum(1 for item in results if item), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
