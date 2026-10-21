"""Offline upload/FFmpeg regression checks; generated media and loopback only."""
import asyncio
from contextlib import ExitStack
import io
import os
from pathlib import Path
import shlex
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("ADMIN_SESSION_SECRET", "offline-test-session-secret-32-characters")
from fastapi import UploadFile
with patch("dotenv.load_dotenv", return_value=False):
    from admin import audio_assets, ux
    from bot.services import voice_audio, voice_music


class MediaSecurityChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.wav = self.root / "valid.wav"
        with wave.open(str(self.wav), "wb") as output:
            output.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            output.writeframes(b"\x01\x00" * 800)

    def test_supported_audio_formats_still_decode(self):
        for suffix in (".wav", ".mp3", ".ogg", ".m4a"):
            path = self.root / ("valid" + suffix)
            if suffix != ".wav":
                subprocess.run(["ffmpeg", "-v", "error", "-i", str(self.wav), str(path)], check=True, capture_output=True, timeout=15)
            duration, _ = voice_audio.validate_audio_asset_file(path)
            self.assertGreater(duration, 0)
            result = subprocess.run(["ffmpeg", "-v", "error", *shlex.split(voice_audio.LOCAL_AUDIO_BEFORE_OPTIONS), "-i", str(path), "-f", "null", "-"], capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, suffix)

    def test_playlist_disguised_as_wav_is_rejected(self):
        path = self.root / "playlist.wav"
        path.write_text("ffconcat version 1.0\nfile 'valid.wav'\n", encoding="ascii")
        with self.assertRaisesRegex(ValueError, "ffprobe rejected"):
            voice_audio.validate_audio_asset_file(path)
        result = subprocess.run(["ffmpeg", "-v", "error", *shlex.split(voice_audio.LOCAL_AUDIO_BEFORE_OPTIONS), "-i", str(path), "-f", "null", "-"], capture_output=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"whitelist", result.stderr)

    def test_oversized_upload_stops_reading_and_removes_partial_file(self):
        class BoundedInput(io.BytesIO):
            def read(self, size=-1):
                assert 0 < size <= 65536
                return super().read(size)
        stream = BoundedInput(b"x" * (3 * 65536))
        upload = UploadFile(filename="audio.wav", file=stream)
        destination = self.root / "partial.wav"
        with ExitStack() as stack:
            stack.enter_context(patch.object(audio_assets, "current_selected_bot_id", return_value="test"))
            stack.enter_context(patch.object(audio_assets, "resolve_audio_asset_storage_path", return_value=destination))
            stack.enter_context(patch.object(audio_assets, "audio_asset_max_bytes", return_value=65536))
            with self.assertRaises(ValueError):
                audio_assets.save_uploaded_audio("test", upload)
        self.assertFalse(destination.exists())
        self.assertEqual(stream.tell(), 2 * 65536)

    def test_image_upload_reads_only_limit_plus_one(self):
        class ImageUpload:
            filename = "test.png"
            async def read(self, size=-1):
                self.size = size
                return b"x" * size
        upload = ImageUpload()
        with patch.object(ux, "MAX_IMAGE_SIZE", 16):
            path, error = asyncio.run(ux.save_uploaded_image(upload, "quotes"))
        self.assertEqual(upload.size, 17)
        self.assertIsNone(path)
        self.assertTrue(error)

    def test_remote_stream_cannot_open_local_files(self):
        result = subprocess.run(["ffmpeg", "-v", "error", *shlex.split(voice_music.STREAM_BEFORE_OPTIONS), "-i", str(self.wav), "-f", "null", "-"], capture_output=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"whitelist", result.stderr)

    def test_raw_transport_inputs_cannot_bypass_http_proxy(self):
        from types import SimpleNamespace
        for url in ('tcp://127.0.0.1:5432', 'file:///etc/passwd', 'data:audio/wav,abcd', 'udp://127.0.0.1:53'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                voice_music.build_ffmpeg_before_options(SimpleNamespace(stream_url=url))

    def test_https_rejects_untrusted_certificate_before_sending_headers(self):
        openssl = shutil.which("openssl")
        if os.name == "nt" and Path("C:/Program Files/Git/usr/bin/openssl.exe").is_file():
            openssl = "C:/Program Files/Git/usr/bin/openssl.exe"
        if not openssl:
            self.skipTest("openssl required to generate a disposable test certificate")
        key, cert = self.root / "test.key", self.root / "test.crt"
        subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=localhost", "-keyout", str(key), "-out", str(cert)], check=True, capture_output=True, timeout=20)
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                requests.append(self.path)
                self.send_response(200)
                self.end_headers()
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"https://127.0.0.1:{server.server_port}/audio"
            track = voice_music.MusicTrack("test", url, url, "offline-user")
            args = shlex.split(voice_music.build_ffmpeg_before_options(track))
            from check_media_egress import allow_fixture
            with allow_fixture('127.0.0.1', server.server_port):
                result = subprocess.run(["ffmpeg", "-v", "error", *args, "-i", url, "-f", "null", "-"], capture_output=True, timeout=15)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(requests, [])
            # 0x80090325 is Schannel's SEC_E_UNTRUSTED_ROOT on Windows.
            self.assertRegex(result.stderr.decode(errors="replace").lower(), "certificate verification failed|certificate verify failed|peer certificate failed verification|cert_trust|self.signed|untrusted|unable to verify|0x80090325")
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == "__main__":
    unittest.main()
