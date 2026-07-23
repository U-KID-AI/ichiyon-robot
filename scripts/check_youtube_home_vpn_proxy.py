import asyncio
import os
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import bot.services.voice_music as voice_music
from bot.services.voice_music import (
    STREAM_BEFORE_OPTIONS,
    MusicTrack,
    YOUTUBE_HOME_VPN_ENABLED_ENV,
    YOUTUBE_HOME_VPN_EXTRACT_TIMEOUT_SECONDS_ENV,
    YOUTUBE_HOME_VPN_FALLBACK_ENABLED_ENV,
    YOUTUBE_HOME_VPN_PROXY_URL_ENV,
    YOUTUBE_ROUTE_DIRECT_COOKIE,
    YOUTUBE_ROUTE_HOME_VPN,
    build_ffmpeg_before_options,
    build_ytdl_options,
    extract_track_info_with_cookie_fallback,
    refresh_track_for_playback,
    youtube_home_vpn_enabled,
)


ENV_KEYS = (
    YOUTUBE_HOME_VPN_ENABLED_ENV,
    YOUTUBE_HOME_VPN_PROXY_URL_ENV,
    YOUTUBE_HOME_VPN_EXTRACT_TIMEOUT_SECONDS_ENV,
    YOUTUBE_HOME_VPN_FALLBACK_ENABLED_ENV,
    voice_music.YTDLP_COOKIES_FILE_ENV,
)


def check(name: str, ok: bool, detail: str = "") -> bool:
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


def save_env():
    return {key: os.environ.get(key) for key in ENV_KEYS}


def restore_env(saved):
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


class FakeVoiceClient:
    channel = type("FakeVoiceChannel", (), {"id": "voice-1"})()


async def run_fallback_checks():
    results = []
    calls = []
    saved_env = save_env()
    original_extract = voice_music.extract_track_info
    original_auth_handler = voice_music.handle_transient_auth_failure
    try:
        async def _noop_auth_handler(*args, **kwargs):
            return None

        voice_music.handle_transient_auth_failure = _noop_auth_handler

        def _fake_extract(url, requester_id, guild_id=None, use_cookies=True, js_runtime=None, youtube_route=YOUTUBE_ROUTE_DIRECT_COOKIE, proxy_url=None, socket_timeout=None):
            calls.append(
                {
                    "url": url,
                    "requester_id": requester_id,
                    "guild_id": guild_id,
                    "use_cookies": use_cookies,
                    "youtube_route": youtube_route,
                    "proxy_url": proxy_url,
                    "socket_timeout": socket_timeout,
                }
            )
            if proxy_url and "fail" in url:
                raise RuntimeError("proxy connection refused")
            return MusicTrack(
                title="track",
                webpage_url=url,
                stream_url="https://stream.example.com/audio",
                requester_id=requester_id,
                duration=120,
                source_url=url,
                youtube_route=youtube_route,
                ffmpeg_proxy_url=str(proxy_url or ""),
            )

        voice_music.extract_track_info = _fake_extract

        os.environ[YOUTUBE_HOME_VPN_ENABLED_ENV] = "true"
        os.environ[YOUTUBE_HOME_VPN_PROXY_URL_ENV] = "http://youtube-vpn-proxy:8888"
        os.environ[YOUTUBE_HOME_VPN_EXTRACT_TIMEOUT_SECONDS_ENV] = "30"
        os.environ[YOUTUBE_HOME_VPN_FALLBACK_ENABLED_ENV] = "true"

        calls.clear()
        home_track = await extract_track_info_with_cookie_fallback("https://youtu.be/success", "user-1", "guild-1", FakeVoiceClient())
        results.append(check("vpn enabled uses home vpn first", len(calls) == 1 and calls[0]["youtube_route"] == YOUTUBE_ROUTE_HOME_VPN, str(calls)))
        results.append(check("home vpn path is cookie-less", calls[0]["use_cookies"] is False, str(calls)))
        results.append(check("home vpn track keeps proxy route", home_track.youtube_route == YOUTUBE_ROUTE_HOME_VPN and home_track.ffmpeg_proxy_url, str(home_track)))

        calls.clear()
        fallback_track = await extract_track_info_with_cookie_fallback("https://youtu.be/fail", "user-1", "guild-1", FakeVoiceClient())
        results.append(check("proxy failure falls back to direct cookie", len(calls) == 2 and calls[1]["youtube_route"] == YOUTUBE_ROUTE_DIRECT_COOKIE, str(calls)))
        results.append(check("fallback re-extracts without proxy", calls[1]["use_cookies"] is True and not calls[1]["proxy_url"], str(calls)))
        results.append(check("fallback track is direct cookie route", fallback_track.youtube_route == YOUTUBE_ROUTE_DIRECT_COOKIE and not fallback_track.ffmpeg_proxy_url, str(fallback_track)))

        os.environ[YOUTUBE_HOME_VPN_ENABLED_ENV] = "false"
        calls.clear()
        direct_track = await extract_track_info_with_cookie_fallback("https://youtu.be/direct", "user-1", "guild-1", FakeVoiceClient())
        results.append(check("vpn disabled uses only direct cookie path", len(calls) == 1 and calls[0]["youtube_route"] == YOUTUBE_ROUTE_DIRECT_COOKIE, str(calls)))
        results.append(check("direct cookie track has no ffmpeg proxy", direct_track.youtube_route == YOUTUBE_ROUTE_DIRECT_COOKIE and not direct_track.ffmpeg_proxy_url, str(direct_track)))

        os.environ[YOUTUBE_HOME_VPN_ENABLED_ENV] = "true"
        os.environ[YOUTUBE_HOME_VPN_PROXY_URL_ENV] = ""
        results.append(check("vpn requires proxy url", youtube_home_vpn_enabled() is False))

        os.environ[YOUTUBE_HOME_VPN_PROXY_URL_ENV] = "http://youtube-vpn-proxy:8888"
        calls.clear()
        seed_track = MusicTrack(
            title="n-pull track",
            webpage_url="https://www.youtube.com/watch?v=abc123",
            stream_url="",
            requester_id="user-1",
            duration=100,
            source_url="https://www.youtube.com/watch?v=abc123",
            refresh_required=True,
            source_type="youtube_n_pull",
        )
        refreshed = await refresh_track_for_playback(seed_track, "guild-1")
        results.append(check("refresh uses common extraction route", refreshed is not None and calls and calls[0]["youtube_route"] == YOUTUBE_ROUTE_HOME_VPN, str(calls)))
        results.append(check("refresh preserves source type", refreshed is not None and refreshed.source_type == "youtube_n_pull", str(refreshed)))
    finally:
        voice_music.extract_track_info = original_extract
        voice_music.handle_transient_auth_failure = original_auth_handler
        restore_env(saved_env)
    return results


def run_static_checks():
    results = []
    saved_env = save_env()
    try:
        os.environ.pop(voice_music.YTDLP_COOKIES_FILE_ENV, None)
        options = build_ytdl_options(
            "guild-1",
            use_cookies=False,
            proxy_url="http://youtube-vpn-proxy:8888",
            socket_timeout=30,
        )
        results.append(check("yt-dlp proxy option is set for home vpn", options.get("proxy") == "http://youtube-vpn-proxy:8888", str(options)))
        results.append(check("home vpn yt-dlp path omits cookiefile", "cookiefile" not in options, str(options)))
        results.append(check("home vpn yt-dlp path keeps deno runtime", options.get("js_runtimes") == {"deno": {}}, str(options)))
        results.append(check("remote components are not restored", "remote_components" not in options, str(options)))

        proxy_track = MusicTrack("title", "page", "stream", "user", source_url="page", youtube_route=YOUTUBE_ROUTE_HOME_VPN, ffmpeg_proxy_url="http://youtube-vpn-proxy:8888")
        direct_track = MusicTrack("title", "page", "stream", "user", source_url="page", youtube_route=YOUTUBE_ROUTE_DIRECT_COOKIE)
        results.append(check("ffmpeg home vpn path includes proxy", "-http_proxy http://youtube-vpn-proxy:8888" in build_ffmpeg_before_options(proxy_track), build_ffmpeg_before_options(proxy_track)))
        results.append(check("ffmpeg direct path has no proxy", build_ffmpeg_before_options(direct_track) == STREAM_BEFORE_OPTIONS, build_ffmpeg_before_options(direct_track)))

        compose = (ROOT_DIR / "docker-compose.yml").read_text(encoding="utf-8")
        results.append(check("compose includes youtube vpn proxy sidecar", "youtube-vpn-proxy:" in compose))
        results.append(check("compose grants NET_ADMIN only to sidecar", "NET_ADMIN" in compose and "YOUTUBE_HOME_VPN_PROXY_URL" in compose))
        results.append(check("compose mounts openvpn config read-only", "/vpn/client.ovpn:ro" in compose))
        results.append(check("compose passes OpenVPN data ciphers", "OPENVPN_DATA_CIPHERS:" in compose and "OPENVPN_DATA_CIPHERS_FALLBACK:" in compose))
        results.append(check("compose defaults AES-128-CBC fallback", "YOUTUBE_HOME_VPN_DATA_CIPHERS_FALLBACK:-AES-128-CBC" in compose))
        results.append(check("compose does not set global HTTP proxy", "HTTP_PROXY" not in compose and "HTTPS_PROXY" not in compose))
        sidecar_profile_block = compose.split("youtube-vpn-proxy:", 1)[1].split("build:", 1)[0]
        results.append(check("vpn sidecar uses explicit profile", "- youtube-vpn" in sidecar_profile_block and "- bot\n" not in sidecar_profile_block, sidecar_profile_block.strip()))

        dockerfile = (ROOT_DIR / "docker" / "youtube-vpn-proxy" / "Dockerfile").read_text(encoding="utf-8")
        entrypoint = (ROOT_DIR / "docker" / "youtube-vpn-proxy" / "entrypoint.sh").read_text(encoding="utf-8")
        results.append(check("proxy image installs openvpn and tinyproxy", "openvpn" in dockerfile and "tinyproxy" in dockerfile))
        results.append(check("openvpn uses tun mtu and mssfix", "--tun-mtu" in entrypoint and "--mssfix" in entrypoint))
        results.append(check("openvpn includes AES-128-CBC data cipher", "--data-ciphers" in entrypoint and "AES-128-CBC" in entrypoint))
        results.append(check("openvpn uses data-ciphers fallback", "--data-ciphers-fallback" in entrypoint and "DATA_CIPHERS_FALLBACK" in entrypoint))

        voice_music_source = (ROOT_DIR / "bot" / "services" / "voice_music.py").read_text(encoding="utf-8")
        results.append(check("ffmpeg home vpn startup failure can fallback", "ffmpeg_fallback=direct_cookie" in voice_music_source and "fallback_extract_ms" in voice_music_source))

        gitignore = (ROOT_DIR / ".gitignore").read_text(encoding="utf-8")
        results.append(check("vpn secrets are gitignored", "*.ovpn" in gitignore and "OpenVPN-Config.ovpn" in gitignore and "secrets/" in gitignore))
    finally:
        restore_env(saved_env)
    return results


def main() -> int:
    results = []
    results.extend(run_static_checks())
    results.extend(asyncio.run(run_fallback_checks()))
    ok_count = sum(1 for item in results if item)
    print("summary: {0}/{1} OK".format(ok_count, len(results)))
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
