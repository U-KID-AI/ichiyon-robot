from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def check(name, condition, detail=""):
    status = "OK" if condition else "FAIL"
    print("[{0}] {1}{2}".format(status, name, " - {0}".format(detail) if detail else ""))
    return bool(condition)


def service_block(compose: str, service: str) -> str:
    marker = "  {0}:\n".format(service)
    start = compose.index(marker)
    next_markers = []
    for other in ("db", "admin", "youtube-vpn-proxy", "voicevox-engine", "bot", "bot-irsia"):
        if other == service:
            continue
        needle = "\n  {0}:\n".format(other)
        index = compose.find(needle, start + len(marker))
        if index >= 0:
            next_markers.append(index)
    end = min(next_markers) if next_markers else len(compose)
    return compose[start:end]


def main():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    matrix = (ROOT / "docs" / "operations" / "service-environment-matrix.md").read_text(encoding="utf-8")
    admin = service_block(compose, "admin")
    bot = service_block(compose, "bot")
    irsia = service_block(compose, "bot-irsia")
    vpn = service_block(compose, "youtube-vpn-proxy")
    results = []
    results.append(check("admin receives oauth env", "DISCORD_OAUTH_CLIENT_SECRET" in admin))
    results.append(check("admin does not receive bot tokens", "ICHIYON_DISCORD_TOKEN" not in admin and "IRSIA_DISCORD_TOKEN" not in admin))
    results.append(check("ichiyon bot receives only ichiyon token", "ICHIYON_DISCORD_TOKEN" in bot and "IRSIA_DISCORD_TOKEN" not in bot))
    results.append(check("irsia bot receives only irsia token", "IRSIA_DISCORD_TOKEN" in irsia and "ICHIYON_DISCORD_TOKEN" not in irsia))
    results.append(check("vpn service receives openvpn env only", "OPENVPN_CONFIG" in vpn and "DISCORD" not in vpn and "POSTGRES" not in vpn))
    results.append(check("spotify client credentials removed", "SPOTIFY_CLIENT_ID" not in compose and "SPOTIFY_CLIENT_SECRET" not in compose))
    results.append(check("matrix documents token scoping", "ICHIYON_DISCORD_TOKEN" in matrix and "IRSIA_DISCORD_TOKEN" in matrix))
    results.append(check("matrix documents spotify removal", "Spotify Premium / Developer App / Web API credentials は撤去済み" in matrix))
    ok = sum(1 for item in results if item)
    print("summary: {0}/{1} OK".format(ok, len(results)))
    if ok != len(results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
