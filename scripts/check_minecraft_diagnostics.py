"""Offline tests: all host processes and configuration are synthetic."""
import asyncio
import importlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent / "minecraft"))
import minecraft_diagnostics as diagnostics

try:
    with patch("os.getenv", side_effect=lambda key, default=None: default):
        api = importlib.import_module("minecraft_control_api")
except ModuleNotFoundError as error:
    if error.name != "fastapi":
        raise
    api = None

STATE = '\n'.join(json.dumps(value) for value in (
    "running", True, "healthy", "2026-01-01T00:00:00.123456789Z", 2))


class DiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    def test_configuration(self):
        for name in ("--help", "bds;restart", "../bds", "a b", "a\nb"):
            with self.assertRaises(ValueError):
                diagnostics.Diagnostics(name, "broadcast", "19132")
        for port in ("0", "65536", "19132 --help", "-1"):
            with self.assertRaises(ValueError):
                diagnostics.Diagnostics("bds", "broadcast", port)

    def test_state_and_versions(self):
        self.assertEqual(diagnostics.container_state(STATE)["restart_count"], 2)
        for raw in ("secret", STATE.replace('"healthy"', '"secret"'),
                    STATE.replace("\n2", "\ntrue"), STATE.replace("2026-01", "2026-99")):
            self.assertEqual(diagnostics.container_state(raw)["status"], "unknown")
        self.assertEqual(diagnostics.runtime_version("[INFO] Version: 1.21.100.1", "bds"), "1.21.100.1")
        self.assertEqual(diagnostics.runtime_version("MCXboxBroadcast version: 3.0.1", "broadcast"), "3.0.1")
        for raw in ("VERSION=LATEST", "Version: secret", "Version: 1.2.3 cookie=secret",
                    "Version: 1.2.3\nVersion: 2.3.4"):
            self.assertIsNone(diagnostics.runtime_version(raw, "bds"))

    def test_log_redaction(self):
        raw = "CONNECTREQUEST success cookie=SECRET 192.0.2.1\nICE failed payload=SECRET\nsignaling timeout"
        summary = diagnostics.log_summary(raw, True)
        encoded = json.dumps(summary)
        self.assertNotIn("SECRET", encoded)
        self.assertNotIn("192.0.2.1", encoded)
        self.assertEqual(summary["events"]["ICE"]["failure_hint"], 1)
        self.assertEqual(summary["events"]["CONNECTREQUEST"]["success_hint"], 1)
        self.assertEqual(diagnostics.log_summary(raw, False)["events"]["ICE"]["observed"], 0)

    async def test_fixed_snapshot(self):
        calls = []

        async def fake(argv, **kwargs):
            calls.append(argv)
            if argv[1] == "inspect":
                self.assertIn("--format", argv)
                self.assertNotIn("Env", argv[5])
                return "ok", STATE
            if argv[1] == "logs":
                return "ok", "Version: 1.2.3\nCONNECTRESPONSE failed SECRET 192.0.2.1"
            return "ok", "SECRET"

        with patch.object(diagnostics, "bounded_output", side_effect=fake):
            result = await diagnostics.Diagnostics("bds", "broadcast", "19132").snapshot()
        self.assertNotIn("SECRET", json.dumps(result))
        self.assertEqual(result["bds"]["version"], "1.2.3")
        self.assertEqual(result["udp"]["status"], "responding")
        self.assertEqual(result["udp"]["external_reachability"], "not_tested")
        self.assertEqual(len(calls), 6)
        self.assertEqual(calls[-1], ("/usr/bin/docker", "exec", "bds", "mc-monitor",
                                     "status-bedrock", "--host", "127.0.0.1", "--port", "19132"))

    async def test_failed_collection_is_unknown(self):
        with patch.object(diagnostics, "bounded_output", AsyncMock(return_value=("unavailable", ""))):
            result = await diagnostics.Diagnostics("bds", "broadcast", "19132").snapshot()
        self.assertIsNone(result["bds"]["version"])
        self.assertEqual(result["udp"]["status"], "unconfirmed")
        self.assertEqual(result["broadcast"]["logs"]["availability"], "unavailable")

    @unittest.skipIf(api is None, "FastAPI dependency unavailable")
    async def test_authentication(self):
        from fastapi import HTTPException
        with patch.object(api, "CONTROL_SECRET", "write-fixture"), \
             patch.object(api, "DIAGNOSTICS_SECRET", "read-fixture"), \
             patch.object(api, "DIAGNOSTICS_BROADCAST_CONTAINER", "broadcast"), \
             patch.object(diagnostics.Diagnostics, "snapshot", AsyncMock(return_value={"schema_version": 1})) as collect:
            for credential in (None, "wrong", "write-fixture"):
                with self.assertRaises(HTTPException) as error:
                    await api.get_diagnostics(credential)
                self.assertEqual(error.exception.status_code, 401)
            collect.assert_not_called()
            self.assertEqual(await api.get_diagnostics("read-fixture"), {"schema_version": 1})
            with self.assertRaises(HTTPException):
                api.require_secret("read-fixture")
            async with api.DIAGNOSTICS_LOCK:
                with self.assertRaises(HTTPException) as error:
                    await api.get_diagnostics("read-fixture")
                self.assertEqual(error.exception.status_code, 429)
        for value in ("", "write-fixture"):
            with patch.object(api, "CONTROL_SECRET", "write-fixture"), patch.object(api, "DIAGNOSTICS_SECRET", value):
                with self.assertRaises(HTTPException) as error:
                    await api.get_diagnostics(value)
                self.assertEqual(error.exception.status_code, 503)

    async def test_bounded_process(self):
        class Process:
            def __init__(self, output, stderr=b"", code=0):
                self.stdout, self.stderr = asyncio.StreamReader(), asyncio.StreamReader()
                for pipe, data in ((self.stdout, output), (self.stderr, stderr)):
                    pipe.feed_data(data)
                    pipe.feed_eof()
                self.returncode = code
                self.killed = False

            def kill(self):
                self.killed = True
                self.returncode = -9

            async def wait(self):
                return self.returncode

        for output, code, expected in ((b"safe", 0, "ok"), (b"SECRET", 1, "unavailable"),
                                        (b"X" * (diagnostics.MAX_BYTES + 1), None, "unavailable")):
            process = Process(output, code=code)
            with patch.object(asyncio, "create_subprocess_exec", AsyncMock(return_value=process)):
                status, raw = await diagnostics.bounded_output(("/usr/bin/docker",))
            self.assertEqual(status, expected)
            if expected == "unavailable":
                self.assertEqual(raw, "")
            if code is None:
                self.assertTrue(process.killed)
        process = Process(b"stdout", stderr=b"stderr")
        with patch.object(asyncio, "create_subprocess_exec", AsyncMock(return_value=process)):
            self.assertEqual(await diagnostics.bounded_output(("/usr/bin/docker",), logs=True),
                             ("ok", "stdout\nstderr"))
        process = Process(b"Version: ", stderr=b"1.2.3")
        with patch.object(asyncio, "create_subprocess_exec", AsyncMock(return_value=process)):
            status, raw = await diagnostics.bounded_output(("/usr/bin/docker",), logs=True)
        self.assertEqual(status, "ok")
        self.assertIsNone(diagnostics.runtime_version(raw, "bds"))
        for exception in (asyncio.TimeoutError, asyncio.CancelledError):
            process = Process(b"", code=None)

            async def interrupted(coroutine, timeout):
                self.assertEqual(timeout, 10)
                coroutine.close()
                raise exception()

            with patch.object(asyncio, "create_subprocess_exec", AsyncMock(return_value=process)), \
                 patch.object(asyncio, "wait_for", side_effect=interrupted):
                if exception is asyncio.CancelledError:
                    with self.assertRaises(asyncio.CancelledError):
                        await diagnostics.bounded_output(("/usr/bin/docker",))
                else:
                    self.assertEqual(await diagnostics.bounded_output(("/usr/bin/docker",)), ("unavailable", ""))
            self.assertTrue(process.killed)


if __name__ == "__main__":
    unittest.main()
