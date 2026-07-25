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

    code_findings = marker_absent(code_paths, FORBIDDEN_CODE_MARKERS)
    config_findings = marker_absent(config_paths, FORBIDDEN_CONFIG_MARKERS)
    results = [
        check("official spotify api module is removed", not deleted_paths[0].exists()),
        check("spotify api fallback resolver module is removed", not deleted_paths[1].exists()),
        check("runtime code has no spotify web api/token markers", not code_findings, ", ".join(code_findings)),
        check("compose/env/docs have no spotify client credentials", not config_findings, ", ".join(config_findings)),
        check("public resolver is still present", "class SpotifyPublicResolver" in read("bot/services/spotify_public.py")),
        check("voice music uses public resolver", "get_spotify_public_resolver" in read("bot/services/voice_music.py")),
        check("spotify metadata models remain", "class SpotifyTrackMetadata" in read("bot/services/spotify_client.py")),
    ]
    print("spotify web api removal checks: {0}/{1}".format(sum(1 for item in results if item), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
