"""Offline status regression checks; no Docker, network, dotenv or credentials."""

import ast
import re
import subprocess
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, Optional
from unittest.mock import patch

# Extract the actual status functions without importing FastAPI or evaluating
# module-level process settings (including credentials). All I/O is replaced.
source = Path(__file__).parent / "minecraft" / "minecraft_control_api.py"
tree = ast.parse(source.read_text(encoding="utf-8"))
names = {"parse_started_at", "parse_bedrock_probe", "bridge_status_from_mc_monitor", "status_payload"}
tree.body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
assert len(tree.body) == len(names)
api = ModuleType("offline_control_status")
api.__dict__.update(
    re=re, datetime=datetime, timezone=timezone, Path=Path,
    Any=Any, Dict=Dict, Optional=Optional,
    BEDROCK_PORT="19134", CONTAINER_NAME="offline-test-container",
)


def forbidden_io(*args, **kwargs):
    raise AssertionError("unmocked external operation")


for name in ("run_fixed", "docker_inspect", "docker_stats", "host_cpu_percent", "host_memory"):
    api.__dict__[name] = forbidden_io
exec(compile(tree, str(source), "exec"), api.__dict__)


class StatusChecks(unittest.TestCase):
    def setUp(self):
        self.port = patch.object(api, "BEDROCK_PORT", "19134")
        self.port.start()
        self.addCleanup(self.port.stop)
        self.reply = "127.0.0.1:19134 : version=1.26.51 online=2 max=10"

    def test_runtime_version_and_players(self):
        result = api.parse_bedrock_probe(self.reply + "\n", 0)
        self.assertEqual(result["version"], "1.26.51")
        self.assertEqual(result["player_count"], 2)
        self.assertTrue(result["responding"])
        self.assertEqual(result["probe_scope"], "container_loopback")
        self.assertEqual(
            api.parse_bedrock_probe(self.reply.replace("1.26.51", "1.26.51.01"), 0)["version"],
            "1.26.51.01",
        )

    def test_failure_cannot_reuse_version(self):
        for code in (1, 124, -9):
            result = api.parse_bedrock_probe(self.reply, code)
            self.assertFalse(result["responding"])
            self.assertIsNone(result["version"])

    def test_malformed_or_untrusted_output_is_unknown(self):
        for output in (
            "", "LATEST", self.reply.replace("1.26.51", "LATEST"),
            self.reply.replace("19134", "19132"),
            self.reply.replace("online=2", "online=-1"),
            self.reply.replace("online=2", "online=11"),
            self.reply.replace("max=10", "max=0"),
            self.reply + "\nunexpected diagnostic text",
        ):
            with self.subTest(output=output):
                result = api.parse_bedrock_probe(output, 0)
                self.assertFalse(result["responding"])
                self.assertIsNone(result["version"])
                self.assertNotIn("raw_status", result)

    def test_fixed_probe_does_not_parse_or_return_stderr(self):
        completed = subprocess.CompletedProcess([], 0, self.reply, "private diagnostic")
        with patch.object(api, "run_fixed", return_value=completed) as run:
            result = api.bridge_status_from_mc_monitor()
        run.assert_called_once_with([
            "docker", "exec", api.CONTAINER_NAME, "mc-monitor", "status-bedrock",
            "--host", "127.0.0.1", "--port", "19134",
        ], timeout=10)
        self.assertNotIn("private diagnostic", str(result))
        completed.stdout, completed.stderr = "", self.reply
        with patch.object(api, "run_fixed", return_value=completed):
            self.assertFalse(api.bridge_status_from_mc_monitor()["responding"])

    def payload(self, running, probe):
        inspect = {
            "State": {"Status": "running" if running else "exited"},
            "Config": {"Env": ["VERSION=LATEST"]},
        }
        patches = (
            patch.object(api, "docker_inspect", return_value=inspect),
            patch.object(api, "docker_stats", return_value={}),
            patch.object(api, "host_cpu_percent", return_value=None),
            patch.object(api, "host_memory", return_value=None),
            patch.object(api.Path, "glob", side_effect=AssertionError("must not infer installed versions")),
        )
        with ExitStack() as stack:
            for mocked in patches:
                stack.enter_context(mocked)
            monitor = stack.enter_context(patch.object(api, "bridge_status_from_mc_monitor", return_value=probe))
            payload = api.status_payload()
        if not running:
            monitor.assert_not_called()
        return payload

    def test_online_does_not_claim_external_connectivity(self):
        payload = self.payload(True, api.parse_bedrock_probe(self.reply, 0))
        self.assertEqual(payload["bds"], {"version": "1.26.51", "version_source": "loopback_status"})
        self.assertEqual(payload["connectivity"], {
            "container_loopback": True,
            "direct_ip_login": "not_tested",
            "friend_join": "not_tested",
        })

    def test_failed_probe_does_not_report_configured_version(self):
        payload = self.payload(True, api.parse_bedrock_probe("", 124))
        self.assertIsNone(payload["bds"]["version"])
        self.assertNotEqual(payload["server_status"], "ONLINE")

    def test_stopped_container_cannot_report_stale_version(self):
        payload = self.payload(False, api.parse_bedrock_probe(self.reply, 0))
        self.assertEqual(payload["server_status"], "OFFLINE")
        self.assertIsNone(payload["bds"]["version"])


if __name__ == "__main__":
    unittest.main()
