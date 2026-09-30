"""Host-side fixed, read-only diagnostics. Never return raw subprocess output.

This module is deployed alongside minecraft_control_api.py by an operator.
It does not grant the generic task executor production access.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone


MAX_BYTES = 65536
EVENTS = ("CONNECTREQUEST", "CONNECTRESPONSE", "CANDIDATEADD", "ICE", "signaling")
STATE_FORMAT = (
    '{{json .State.Status}}\n{{json .State.Running}}\n'
    '{{if .State.Health}}{{json .State.Health.Status}}{{else}}null{{end}}\n'
    '{{json .State.StartedAt}}\n{{json .RestartCount}}'
)


async def bounded_output(argv: tuple[str, ...], *, logs: bool = False) -> tuple[str, str]:
    """Bound both pipes and wall time, discard stderr and all failed output."""
    process = None
    readers = []
    try:
        process = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )

        async def read(pipe):
            output = await pipe.read(MAX_BYTES + 1)
            # read(n) may return short without EOF; accumulate with a hard cap.
            while len(output) <= MAX_BYTES:
                chunk = await pipe.read(MAX_BYTES + 1 - len(output))
                if not chunk:
                    return output
                output += chunk
            raise ValueError("output limit")

        async def collect():
            readers.extend(asyncio.create_task(read(pipe)) for pipe in (process.stdout, process.stderr))
            stdout, stderr = await asyncio.gather(*readers)
            await process.wait()
            # Pipes have no shared ordering. Keep their boundary separate so a
            # partial stdout line cannot turn stderr into a version/event banner.
            output = stdout + b"\n" + stderr if logs else stdout
            if len(output) > MAX_BYTES:
                raise ValueError("output limit")
            return output

        raw = await asyncio.wait_for(collect(), timeout=10)
        if process.returncode != 0:
            return "unavailable", ""
        return "ok", raw.decode("utf-8", errors="replace")
    except (OSError, ValueError, asyncio.TimeoutError):
        return "unavailable", ""
    finally:
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        if process is not None and process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        if process is not None:
            async def discard(pipe):
                while await pipe.read(4096):
                    pass
            await asyncio.gather(discard(process.stdout), discard(process.stderr), process.wait())


def container_state(raw: str) -> dict:
    unknown = {"status": "unknown", "running": None, "health": "unknown",
               "started_at": None, "restart_count": None}
    try:
        state, running, health, started, restarts = [json.loads(s) for s in raw.splitlines()]
        if state not in ("created", "running", "paused", "restarting", "removing", "exited", "dead"):
            return unknown
        if type(running) is not bool or health not in (None, "starting", "healthy", "unhealthy"):
            return unknown
        if type(restarts) is not int or not 0 <= restarts <= 2147483647:
            return unknown
        if not isinstance(started, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z", started
        ):
            return unknown
        datetime.fromisoformat(started.replace("Z", "+00:00"))
        return {"status": state, "running": running, "health": health,
                "started_at": started, "restart_count": restarts}
    except (ValueError, TypeError):
        return unknown


def log_summary(raw: str, available: bool) -> dict:
    """Lexical evidence only; no event occurrence implies connection success."""
    counts = {event: {"observed": 0, "success_hint": 0, "failure_hint": 0} for event in EVENTS}
    for line in raw.splitlines() if available else ():
        for event in EVENTS:
            if re.search(r"\b" + event + r"\b", line, re.IGNORECASE):
                count = counts[event]
                count["observed"] += 1
                count["success_hint"] += bool(re.search(r"\b(success|succeeded|connected)\b", line, re.I))
                count["failure_hint"] += bool(re.search(r"\b(failed|failure|error|timeout|disconnected)\b", line, re.I))
    return {"availability": "ok" if available else "unavailable", "window_seconds": 900,
            "tail_lines": 200, "interpretation": "lexical_hints_only", "events": counts}


def runtime_version(raw: str, product: str) -> str | None:
    # Accept only numeric startup banners, never image tags, env, or filenames.
    # Unknown banner formats and ambiguous versions remain unknown.
    label = "Version" if product == "bds" else "MCXboxBroadcast version"
    matches = re.findall(r"^(?:\[[^\]\r\n]{1,80}\]\s*)?" + label
                         + r": (\d{1,5}(?:\.\d{1,5}){2,3})\s*$", raw, re.MULTILINE)
    return matches[0] if matches and len(set(matches)) == 1 else None


class Diagnostics:
    def __init__(self, bds: str, broadcast: str, port: str):
        # Values may come only from trusted host process configuration.
        if any(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", v) is None
               for v in (bds, broadcast)) or bds == broadcast:
            raise ValueError("diagnostics configuration rejected")
        if not re.fullmatch(r"[0-9]{1,5}", port) or not 1 <= int(port) <= 65535:
            raise ValueError("diagnostics configuration rejected")
        self.bds, self.broadcast, self.port = bds, broadcast, port

    async def snapshot(self) -> dict:
        result = {"schema_version": 1, "observed_at": datetime.now(timezone.utc).isoformat(),
                  "control_api": "responding"}
        for product, name in (("bds", self.bds), ("broadcast", self.broadcast)):
            status, raw = await bounded_output(("/usr/bin/docker", "inspect", "--type", "container",
                                                "--format", STATE_FORMAT, name))
            state = container_state(raw if status == "ok" else "")
            version = None
            if state["running"] is True:
                status, raw = await bounded_output(("/usr/bin/docker", "logs", "--since",
                                                    state["started_at"], "--tail", "200", name), logs=True)
                if status == "ok":
                    version = runtime_version(raw, product)
            result[product] = {"container": state, "version": version,
                               "version_evidence": "current_startup_banner" if version else "unknown"}
        status, raw = await bounded_output(("/usr/bin/docker", "logs", "--since", "15m",
                                            "--tail", "200", self.broadcast), logs=True)
        result["broadcast"]["logs"] = log_summary(raw, status == "ok")
        # Reuse the existing Control API's fixed mc-monitor Bedrock query.
        # This tests the actual configured port inside the BDS network namespace.
        status, _ = await bounded_output(("/usr/bin/docker", "exec", self.bds,
                                         "mc-monitor", "status-bedrock", "--host", "127.0.0.1",
                                         "--port", self.port))
        result["udp"] = {"scope": "bds_container_loopback", "port": int(self.port),
                         "status": "responding" if status == "ok" else "unconfirmed",
                         "external_reachability": "not_tested"}
        return result
