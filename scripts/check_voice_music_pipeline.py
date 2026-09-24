"""Offline route tests and real FFmpeg/discord.py integration against loopback HTTP."""

import asyncio
import contextlib
import io
import logging
import os
from pathlib import Path
import shlex
import struct
import sys
import threading
import unittest
from http.cookiejar import Cookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit
import wave

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import discord
import yt_dlp
from yt_dlp.cookies import YoutubeDLCookieJar
from bot.services import voice_music as music
from bot.services.voice.ffmpeg import MusicFFmpegPCMAudio
from bot.services.voice.mixer import VoiceMixerAudioSource
from bot.services.voice.models import MusicTrack


def track(**fields):
    return MusicTrack("test", "https://www.youtube.com/watch?v=test", fields.pop("stream_url", "https://media.example/audio?signature=hidden"), "user", **fields)


class RouteTests(unittest.TestCase):
    def test_library_contract(self):
        self.assertEqual(discord.__version__, "2.7.1")
        self.assertTrue(issubclass(discord.opus.OpusNotLoaded, Exception))
        self.assertFalse(hasattr(discord, "OpusNotLoaded"))

    def test_options_and_quoted_headers(self):
        with patch.dict(os.environ, {"HTTP_PROXY": "http://wrong:8888"}, clear=True):
            direct = music.build_ytdl_options(use_cookies=False)
            self.assertEqual(direct["proxy"], "")
            self.assertGreater(direct["socket_timeout"], 0)
            home = music.build_ytdl_options(use_cookies=False, proxy_url="http://youtube-vpn-proxy:8888", socket_timeout=7)
            self.assertEqual(home["socket_timeout"], 7)
            self.assertNotIn("cookiefile", home)
        item = track(ffmpeg_headers={"User-Agent": "A 'quoted' agent", "Referer": "https://example.org/a b"}, ffmpeg_cookies="sid=hidden")
        args = shlex.split(music.build_ffmpeg_before_options(item))
        self.assertEqual(args[args.index("-headers") + 1], "User-Agent: A 'quoted' agent\r\nReferer: https://example.org/a b\r\nCookie: sid=hidden\r\n")
        self.assertNotIn("-http_proxy", args)
        item.youtube_route = music.YOUTUBE_ROUTE_HOME_VPN
        item.ffmpeg_proxy_url = "http://youtube-vpn-proxy:8888"
        item.ffmpeg_headers["Cookie"] = "sid=hidden"
        args = shlex.split(music.build_ffmpeg_before_options(item))
        self.assertNotIn("-cookies", args)
        self.assertNotIn("Cookie:", args[args.index("-headers") + 1])
        self.assertEqual(args[args.index("-http_proxy") + 1], item.ffmpeg_proxy_url)

    def test_header_injection_rejected(self):
        for headers in ({"User-Agent": "ok\r\nCookie: stolen"}, {"Bad\nName": "value"}, {"X-Test": "bad\x00value"}):
            with self.assertRaises(ValueError):
                music.build_ffmpeg_before_options(track(ffmpeg_headers=headers))

    def test_extract_keeps_only_url_scoped_cookies_and_route_headers(self):
        jar = YoutubeDLCookieJar()
        for domain, value in [("media.example", "media-value"), (".youtube.com", "login-value")]:
            jar.set_cookie(Cookie(0, "sid", value, None, False, domain, True, domain.startswith("."), "/", True, True, None, True, None, None, {}))
        info = {"url": "https://media.example/audio", "title": "test", "http_headers": {"User-Agent": "agent", "Cookie": "scoped=value"}}
        class Extractor:
            cookiejar = jar
            def __init__(self, options):
                self.options = options
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def extract_info(self, *args, **kwargs):
                return {"entries": [None, info]}
        with patch.object(yt_dlp, "YoutubeDL", Extractor), patch.dict(os.environ, {}, clear=True):
            direct = music.extract_track_info("https://youtu.be/test", "user")
            home = music.extract_track_info("https://youtu.be/test", "user", use_cookies=False, youtube_route=music.YOUTUBE_ROUTE_HOME_VPN, proxy_url="http://youtube-vpn-proxy:8888", socket_timeout=9)
        self.assertIn("media-value", direct.ffmpeg_cookies)
        self.assertNotIn("login-value", direct.ffmpeg_cookies)
        self.assertEqual(direct.ffmpeg_headers, info["http_headers"])
        self.assertEqual(home.ffmpeg_cookies, "")
        self.assertNotIn("Cookie", home.ffmpeg_headers)
        self.assertEqual(home.ffmpeg_headers["User-Agent"], "agent")
        self.assertEqual(home.ffmpeg_socket_timeout, 9)
        self.assertNotIn("media-value", repr(direct))
        self.assertNotIn("signature=", repr(track()))

    def test_refresh_replaces_stale_route_data(self):
        seed = track(source_url="https://youtu.be/test", refresh_required=True, source_type="youtube_n_pull", ffmpeg_headers={"Old": "value"}, ffmpeg_cookies="old-cookie", ffmpeg_proxy_url="http://old-proxy:8888", youtube_route=music.YOUTUBE_ROUTE_HOME_VPN)
        fresh = track(ffmpeg_headers={"User-Agent": "new"}, ffmpeg_cookies="new-cookie")
        async def extract(*args):
            return fresh
        with patch.object(music, "extract_track_info_with_cookie_fallback", extract):
            result = asyncio.run(music.refresh_track_for_playback(seed, "guild"))
        self.assertEqual(result.ffmpeg_headers, {"User-Agent": "new"})
        self.assertEqual(result.ffmpeg_cookies, "new-cookie")
        self.assertEqual(result.ffmpeg_proxy_url, "")
        self.assertEqual(result.youtube_route, music.YOUTUBE_ROUTE_DIRECT_COOKIE)
        self.assertEqual(result.source_type, "youtube_n_pull")
        loop = music.make_loop_track(result)
        self.assertEqual(loop.ffmpeg_headers, result.ffmpeg_headers)
        self.assertTrue(loop.refresh_required)

    def test_log_redaction(self):
        for value in ["Cookie: sid=private", "Authorization: Bearer private", "https://user:private@proxy.example/secret", "access_token=private", RuntimeError("arbitrary-private-value")]:
            self.assertNotIn("private", music.sanitize_playback_log_message(value))
        self.assertEqual(music.sanitize_playback_log_message("direct_cookie"), "direct_cookie")
        self.assertEqual(music.format_music_timing_fields({"frames": 0, "connected": False}), "connected=False frames=0")

    def test_start_failure_is_safe(self):
        with self.assertRaises(discord.ClientException) as raised:
            MusicFFmpegPCMAudio(track(), lambda *a, **k: None, executable="not-a-real-ffmpeg-executable")
        self.assertNotIn("signature", str(raised.exception))

    def test_opus_start_failure_cleans_source(self):
        item = track()
        state = music.get_music_state("pipeline-opus-test")
        state.queue.clear()
        state.queue.append(item)
        cleaned = []
        source = SimpleNamespace(cleanup=lambda: cleaned.append(True))
        client = SimpleNamespace(is_connected=lambda: True, channel=SimpleNamespace(id="channel"))
        with patch.object(music, "create_music_source", return_value=source), patch.object(music, "load_music_volume_percent", return_value=40), patch.object(music, "ensure_mixer_playing", side_effect=discord.opus.OpusNotLoaded()):
            self.assertFalse(asyncio.run(music.play_next_track(client, "pipeline-opus-test")))
        self.assertTrue(cleaned)
        self.assertIsNone(state.current)
        self.assertIsNone(music.get_mixer("pipeline-opus-test").music_source)

    def test_startup_fallback_uses_fresh_direct_headers_and_cookies(self):
        item = track(source_url="https://youtu.be/test", youtube_route=music.YOUTUBE_ROUTE_HOME_VPN, ffmpeg_proxy_url="http://youtube-vpn-proxy:8888", ffmpeg_headers={"User-Agent": "vpn-agent"})
        fresh = track(ffmpeg_headers={"User-Agent": "direct-agent"}, ffmpeg_cookies="sid=direct-only")
        state = music.get_music_state("pipeline-startup-fallback")
        state.queue.clear()
        state.queue.append(item)
        sources = []
        def create(selected, *args):
            sources.append(selected)
            if len(sources) == 1:
                raise discord.ClientException("do-not-log-cookie")
            return SimpleNamespace(cleanup=lambda: None)
        client = SimpleNamespace(is_connected=lambda: True, channel=SimpleNamespace(id="channel"))
        with patch.object(music, "create_music_source", create), patch.object(music, "extract_track_info", return_value=fresh) as extract, patch.object(music, "load_music_volume_percent", return_value=40), patch.object(music, "ensure_mixer_playing"), patch.object(music, "schedule_spotify_playlist_prefetch"), patch.dict(os.environ, {music.YOUTUBE_HOME_VPN_FALLBACK_ENABLED_ENV: "true"}):
            self.assertTrue(asyncio.run(music.play_next_track(client, "pipeline-startup-fallback")))
        self.assertEqual(extract.call_args.args[3:], (True, None, music.YOUTUBE_ROUTE_DIRECT_COOKIE, None, None))
        self.assertEqual(len(sources), 2)
        self.assertEqual(sources[1].ffmpeg_proxy_url, "")
        self.assertEqual(sources[1].ffmpeg_cookies, "sid=direct-only")
        self.assertEqual(sources[1].ffmpeg_headers, {"User-Agent": "direct-agent"})
        music.get_mixer("pipeline-startup-fallback").cleanup()

    def test_disabled_fallback_does_not_reextract(self):
        item = track(source_url="https://youtu.be/test", youtube_route=music.YOUTUBE_ROUTE_HOME_VPN)
        with patch.dict(os.environ, {music.YOUTUBE_HOME_VPN_FALLBACK_ENABLED_ENV: "false"}), patch.object(music, "extract_track_info") as extract:
            self.assertFalse(asyncio.run(music.retry_track_after_http_403(SimpleNamespace(channel=None), "guild", item)))
        extract.assert_not_called()


class FFmpegTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data = io.BytesIO()
        with wave.open(data, "wb") as wav:
            wav.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
            wav.writeframes(struct.pack("<h", 1500) * 2 * 4800)
        cls.wav = data.getvalue()

    @contextlib.contextmanager
    def server(self, status=200):
        requests = []
        wav = self.wav
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                requests.append((self.path, dict(self.headers)))
                self.send_response(status)
                self.send_header("Content-Length", str(len(wav) if status == 200 else 0))
                self.send_header("Content-Type", "audio/wav")
                self.end_headers()
                if status == 200:
                    self.wfile.write(wav)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield "http://127.0.0.1:{0}".format(server.server_port), requests
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_real_direct_cookie_pcm_and_debug_logs(self):
        with self.server() as (url, requests):
            item = track(stream_url=url + "/audio?signature=hidden", ffmpeg_headers={"User-Agent": "test 'agent'", "Authorization": "Bearer private-auth"}, ffmpeg_cookies="sid=private-cookie")
            events, log = [], io.StringIO()
            logger = logging.getLogger("discord.player")
            handler = logging.StreamHandler(log)
            old_level = logger.level
            logger.addHandler(handler)
            logger.setLevel(logging.DEBUG)
            try:
                with patch.dict(os.environ, {"http_proxy": "http://127.0.0.1:1", "HTTP_PROXY": "http://127.0.0.1:1"}):
                    source = MusicFFmpegPCMAudio(item, lambda action, **values: events.append((action, values)), before_options=music.build_ffmpeg_before_options(item), options=music.STREAM_OPTIONS)
                mixer = VoiceMixerAudioSource("test")
                errors = []
                mixer.set_music_source(source, errors.append)
                try:
                    frames = []
                    while frame := mixer.read():
                        frames.append(frame)
                finally:
                    mixer.cleanup()
            finally:
                logger.removeHandler(handler)
                logger.setLevel(old_level)
            self.assertTrue(frames)
            self.assertTrue(any(frames[0]))
            self.assertEqual(errors, [None])
            self.assertEqual(requests[0][1]["User-Agent"], "test 'agent'")
            self.assertIn("sid=private-cookie", requests[0][1]["Cookie"])
            self.assertEqual(requests[0][1]["Authorization"], "Bearer private-auth")
            self.assertTrue(mixer.diagnostics()["mixer_music_nonzero"])
            self.assertEqual(item.playback_ffmpeg_returncode, 0)
            self.assertFalse(source.reader_thread.is_alive())
            self.assertTrue(any(action == "ffmpeg_pcm" for action, values in events))
            self.assertNotIn("private", log.getvalue())
            self.assertNotIn("signature", log.getvalue())

    def test_real_proxy_receives_headers_without_cookies(self):
        with self.server() as (proxy, requests):
            item = track(stream_url="http://unresolvable.invalid/audio", youtube_route=music.YOUTUBE_ROUTE_HOME_VPN, ffmpeg_proxy_url=proxy, ffmpeg_headers={"User-Agent": "vpn-agent", "Cookie": "private-cookie"}, ffmpeg_cookies="sid=private-cookie")
            source = MusicFFmpegPCMAudio(item, lambda *a, **k: None, before_options=music.build_ffmpeg_before_options(item), options=music.STREAM_OPTIONS)
            try:
                self.assertEqual(len(source.read()), 3840)
                while source.read():
                    pass
            finally:
                source.cleanup()
            self.assertEqual(urlsplit(requests[0][0]).hostname, "unresolvable.invalid")
            self.assertEqual(urlsplit(requests[0][0]).path, "/audio")
            self.assertEqual(requests[0][1]["User-Agent"], "vpn-agent")
            self.assertNotIn("Cookie", requests[0][1])

    def test_real_url_scoped_cookie_handoff(self):
        # Cookies are scoped by yt-dlp before being serialized for FFmpeg.
        with self.server() as (proxy, requests):
            jar = YoutubeDLCookieJar()
            for domain, value in [("media.example", "scoped-cookie"), ("other.example", "do-not-send")]:
                jar.set_cookie(Cookie(0, "sid", value, None, False, domain, True, False, "/", True, False, None, True, None, None, {}))
            item = track(stream_url="http://media.example/audio")
            item.ffmpeg_cookies = jar.get_cookie_header(item.stream_url)
            options = music.build_ffmpeg_before_options(item) + " -http_proxy " + shlex.quote(proxy)
            source = MusicFFmpegPCMAudio(item, lambda *a, **k: None, before_options=options, options=music.STREAM_OPTIONS)
            try:
                while source.read():
                    pass
            finally:
                source.cleanup()
            self.assertIn("sid=scoped-cookie", requests[0][1]["Cookie"])
            self.assertNotIn("do-not-send", requests[0][1]["Cookie"])

    def test_real_403_reports_no_pcm_and_abnormal_exit(self):
        with self.server(status=403) as (url, requests):
            item = track(stream_url=url + "/denied?token=private")
            events = []
            source = MusicFFmpegPCMAudio(item, lambda action, **values: events.append((action, values)), before_options=music.build_ffmpeg_before_options(item), options=music.STREAM_OPTIONS)
            try:
                with self.assertRaises(discord.FFmpegProcessError):
                    source.read()
            finally:
                source.cleanup()
            self.assertTrue(item.playback_http_403)
            self.assertNotEqual(item.playback_ffmpeg_returncode, 0)
            self.assertTrue(music.is_retryable_ffmpeg_playback_failure(item, None))
            self.assertEqual(source.pcm_frames, 0)
            self.assertTrue(events[-1][1]["abnormal"])
            self.assertNotIn("private", repr(events))
            self.assertFalse(source.reader_thread.is_alive())

    def test_intentional_stop_closes_stderr_thread(self):
        item = track(stream_url="sine=frequency=440")
        events = []
        source = MusicFFmpegPCMAudio(item, lambda action, **values: events.append((action, values)), before_options="-f lavfi")
        self.assertEqual(len(source.read()), 3840)
        source.cleanup()
        source.cleanup()
        self.assertFalse(source.reader_thread.is_alive())
        self.assertEqual(events[-1][0], "ffmpeg_stop")
        self.assertFalse(events[-1][1]["abnormal"])


if __name__ == "__main__":
    unittest.main()
