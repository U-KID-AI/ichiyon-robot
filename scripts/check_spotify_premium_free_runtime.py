import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def check(name: str, ok: bool, detail: str = "") -> bool:
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


def main() -> int:
    voice_music = (ROOT_DIR / "bot" / "services" / "voice_music.py").read_text(encoding="utf-8")
    spotify_client = (ROOT_DIR / "bot" / "services" / "spotify_client.py").read_text(encoding="utf-8")
    spotify_public = (ROOT_DIR / "bot" / "services" / "spotify_public.py").read_text(encoding="utf-8")
    results = [
        check("voice music uses public spotify resolver", "get_spotify_public_resolver" in voice_music),
        check("voice music does not create SpotifyClient", "get_spotify_client()" not in voice_music),
        check("spotify client module is metadata only", "class SpotifyClient" not in spotify_client and "accounts.spotify.com" not in spotify_client and "api.spotify.com" not in spotify_client),
        check("public resolver targets open.spotify.com", "open.spotify.com" in spotify_public),
        check("public resolver does not target token endpoint", "accounts.spotify.com" not in spotify_public),
        check("public resolver does not target Web API", "api.spotify.com" not in spotify_public),
        check("public resolver has no Authorization header", "Authorization" not in spotify_public),
        check("artist links are supported", '"artist"' in (ROOT_DIR / "bot" / "services" / "spotify_link.py").read_text(encoding="utf-8")),
    ]
    print("spotify premium-free runtime checks: {0}/{1}".format(sum(1 for item in results if item), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
