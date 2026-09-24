"""PCM source with bounded, value-free diagnostics for discord.py 2.7.1."""

import os
import re
import subprocess
import threading

import discord
from discord.player import CREATE_NO_WINDOW


class MusicFFmpegPCMAudio(discord.FFmpegPCMAudio):
    def __init__(self, track, diagnostic, **kwargs):
        self.track = track
        self.diagnostic = diagnostic
        self.pcm_frames = 0
        self.pcm_nonzero = False
        self.finished = False
        self.cleaned = False
        track.playback_http_403 = False
        track.playback_ffmpeg_returncode = None
        read_fd, write_fd = os.pipe()
        self.error_reader = os.fdopen(read_fd, "rb")
        # A real file descriptor bypasses discord.py's ignored PIPE sentinel and
        # its internal stderr reader. Only our reader owns the read end.
        try:
            with os.fdopen(write_fd, "wb") as error_writer:
                super().__init__(track.stream_url, stderr=error_writer, **kwargs)
        except BaseException:
            self.error_reader.close()
            raise
        self.reader_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        track.playback_ffmpeg_stderr_thread = self.reader_thread
        self.reader_thread.start()
        self.diagnostic("ffmpeg_start", pcm_frames=0)

    def _spawn_process(self, args, **kwargs):
        # discord.py logs the entire argv at DEBUG, including signed URLs and
        # cookies. Keep its spawn semantics, but never log command arguments.
        env = {key: value for key, value in os.environ.items()
               if key.lower() not in {"http_proxy", "https_proxy", "all_proxy", "no_proxy", "ffreport"}}
        try:
            return subprocess.Popen(args, creationflags=CREATE_NO_WINDOW, env=env, **kwargs)
        except (OSError, subprocess.SubprocessError) as exc:
            raise discord.ClientException("FFmpeg start failed: " + type(exc).__name__) from None

    def _drain_stderr(self):
        try:
            with self.error_reader as stream:
                # Bounded reads even when FFmpeg writes a huge line. Keep only a
                # small overlap to recognise status markers across read chunks.
                tail = b""
                while True:
                    chunk = stream.read1(4096)
                    if not chunk:
                        break
                    text = (tail + chunk).lower()
                    if re.search(rb"(?:http error|server returned)[^\r\n]{0,50}\b403\b|\b403 forbidden\b", text):
                        self.track.playback_http_403 = True
                    tail = text[-32:]
        except (OSError, ValueError):
            self.diagnostic("ffmpeg_stderr_unavailable")

    def read(self):
        data = super().read()
        if data:
            self.pcm_frames += 1
            nonzero = any(data)
            if self.pcm_frames == 1 or (nonzero and not self.pcm_nonzero):
                self.diagnostic("ffmpeg_pcm", pcm_frames=self.pcm_frames, pcm_nonzero=nonzero)
            self.pcm_nonzero = self.pcm_nonzero or nonzero
            return data
        self._finish(False)
        if self.track.playback_ffmpeg_returncode not in (None, 0):
            raise discord.FFmpegProcessError("FFmpeg exited abnormally")
        return b""

    def _finish(self, stopped):
        if self.finished:
            return
        self.finished = True
        process = getattr(self, "_process", None)
        if process:
            try:
                self.track.playback_ffmpeg_returncode = process.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                self.track.playback_ffmpeg_returncode = process.poll()
        self.reader_thread.join(timeout=0.2)
        self.diagnostic(
            "ffmpeg_stop" if stopped else "ffmpeg_exit",
            pcm_frames=self.pcm_frames,
            pcm_nonzero=self.pcm_nonzero,
            returncode=self.track.playback_ffmpeg_returncode,
            http_403=self.track.playback_http_403,
            abnormal=not stopped and self.track.playback_ffmpeg_returncode not in (None, 0),
        )

    def cleanup(self):
        if self.cleaned:
            return
        self.cleaned = True
        process = getattr(self, "_process", None)
        try:
            if process:
                # Capture natural exit before discord.py clears the process.
                if process.poll() is not None:
                    self._finish(False)
                super().cleanup()
                if not self.finished:
                    self.track.playback_ffmpeg_returncode = process.poll()
                    self._finish(True)
                if process.stdout:
                    process.stdout.close()
        finally:
            thread = getattr(self, "reader_thread", None)
            if thread:
                thread.join(timeout=0.5)
