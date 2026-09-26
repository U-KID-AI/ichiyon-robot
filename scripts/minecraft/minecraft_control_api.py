from __future__ import annotations

import filecmp
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, Header, HTTPException, Request, status
from starlette.concurrency import run_in_threadpool
from minecraft_cosmetics_apply import PackApplications, MAX_ARCHIVE, deployment_lock


PROJECT_DIR = Path(os.getenv("MINECRAFT_CONTROL_PROJECT_DIR", "/home/ubuntu/minecraft-bedrock-creative"))
DATA_DIR = PROJECT_DIR / "data"
COMPOSE_SERVICE = os.getenv("MINECRAFT_CONTROL_COMPOSE_SERVICE", "bedrock-creative")
CONTAINER_NAME = os.getenv("MINECRAFT_CONTROL_CONTAINER_NAME", "minecraft-bedrock-creative")
PACK_SOURCE_DIR_RAW = os.getenv("MINECRAFT_CONTROL_PACK_SOURCE_DIR", "").strip()
PACK_SOURCE_DIR = Path(PACK_SOURCE_DIR_RAW).expanduser() if PACK_SOURCE_DIR_RAW else None
CONTROL_SECRET = os.getenv("MINECRAFT_CONTROL_SECRET", "")
BACKUP_DIR = PROJECT_DIR / "backups"
WORLD_NAME = os.getenv("MINECRAFT_CONTROL_WORLD_NAME", "ichiyon-creative-flat")
BEDROCK_PORT = os.getenv("MINECRAFT_CONTROL_BEDROCK_PORT", "19134")
RESTART_WAIT_SECONDS = int(os.getenv("MINECRAFT_CONTROL_RESTART_WAIT_SECONDS", "180"))
BACKUP_RETENTION = int(os.getenv("MINECRAFT_CONTROL_BACKUP_RETENTION", "20"))
USE_SAVE_HOLD = os.getenv("MINECRAFT_CONTROL_USE_SAVE_HOLD", "").strip().lower() in ("1", "true", "yes", "on")

PACK_SPECS = (
    {
        "name": "import_structures",
        "source": Path("behavior_packs/import_structures"),
        "destination": DATA_DIR / "behavior_packs" / "import_structures",
        "world_pack_file": DATA_DIR / "worlds" / WORLD_NAME / "world_behavior_packs.json",
    },
    {
        "name": "ichiyon_avatar_bp",
        "source": Path("behavior_packs/ichiyon_avatar_bp"),
        "destination": DATA_DIR / "behavior_packs" / "ichiyon_avatar_bp",
        "world_pack_file": DATA_DIR / "worlds" / WORLD_NAME / "world_behavior_packs.json",
    },
    {
        "name": "ichiyon_avatar_rp",
        "source": Path("resource_packs/ichiyon_avatar_rp"),
        "destination": DATA_DIR / "resource_packs" / "ichiyon_avatar_rp",
        "world_pack_file": DATA_DIR / "worlds" / WORLD_NAME / "world_resource_packs.json",
    },
)

app = FastAPI(title="Ichiyon Minecraft Control API")
cosmetic_applications = PackApplications(sys.modules[__name__])


@app.on_event('startup')
def recover_cosmetic_application():
    cosmetic_applications.recover()


@app.get('/cosmetics')
def cosmetics_status(x_minecraft_control_secret: Optional[str] = Header(default=None)):
    require_secret(x_minecraft_control_secret)
    return cosmetic_applications.status()


@app.post('/cosmetics/{operation_id}', status_code=202)
async def apply_cosmetics(operation_id: str, request: Request, x_minecraft_control_secret: Optional[str] = Header(default=None)):
    require_secret(x_minecraft_control_secret)
    length = request.headers.get('content-length')
    if length is not None:
        try:
            length = int(length)
            if length < 0:
                raise ValueError
        except ValueError:
            raise HTTPException(400, 'invalid content length') from None
        if length > MAX_ARCHIVE:
            raise HTTPException(413, 'archive too large')
    try:
        # Bound disk usage without retaining bytearray + bytes + BytesIO copies.
        with tempfile.TemporaryFile() as data:
            size = 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_ARCHIVE:
                    raise HTTPException(413, 'archive too large')
                await run_in_threadpool(data.write, chunk)
            data.seek(0)
            return await run_in_threadpool(cosmetic_applications.submit, operation_id, data)
    except ValueError:
        raise HTTPException(400, 'invalid pack archive') from None
    except RuntimeError:
        raise HTTPException(409, 'application already active') from None


def require_secret(x_minecraft_control_secret: Optional[str]) -> None:
    if not CONTROL_SECRET:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="control disabled")
    if x_minecraft_control_secret is None or not hmac.compare_digest(x_minecraft_control_secret, CONTROL_SECRET):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")


def run_fixed(command: List[str], *, cwd: Optional[Path] = None, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=str(cwd or PROJECT_DIR),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            command,
            124,
            stdout=(exc.stdout or "") if isinstance(exc.stdout, str) else "",
            stderr=(exc.stderr or "") if isinstance(exc.stderr, str) else "timeout",
        )


def docker_inspect() -> Optional[Dict[str, Any]]:
    result = run_fixed(["docker", "inspect", CONTAINER_NAME], timeout=15)
    if result.returncode != 0:
        return None
    payload = json.loads(result.stdout or "[]")
    return payload[0] if payload else None


def docker_stats() -> Dict[str, Any]:
    result = run_fixed(
        ["docker", "stats", CONTAINER_NAME, "--no-stream", "--format", "{{json .}}"],
        timeout=15,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return {}
    return json.loads(result.stdout)


def host_cpu_percent() -> Optional[str]:
    first = read_proc_stat()
    time.sleep(0.2)
    second = read_proc_stat()
    if first is None or second is None:
        return None
    idle_delta = second["idle"] - first["idle"]
    total_delta = second["total"] - first["total"]
    if total_delta <= 0:
        return None
    return "{0:.1f}%".format((1.0 - idle_delta / total_delta) * 100.0)


def read_proc_stat() -> Optional[Dict[str, int]]:
    try:
        fields = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0].split()[1:]
        values = [int(value) for value in fields]
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        return {"idle": idle, "total": sum(values)}
    except (OSError, ValueError, IndexError):
        return None


def host_memory() -> Optional[str]:
    try:
        values: Dict[str, int] = {}
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, raw_value = line.split(":", 1)
            values[key] = int(raw_value.strip().split()[0])
        total = values.get("MemTotal")
        available = values.get("MemAvailable")
        if not total or available is None:
            return None
        used = total - available
        return "{0:.1f}GiB / {1:.1f}GiB".format(used / 1024 / 1024, total / 1024 / 1024)
    except (OSError, ValueError, IndexError):
        return None


def parse_started_at(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_bedrock_probe(output: str, returncode: int) -> Dict[str, Any]:
    """Parse only mc-monitor's successful loopback response, never configuration.

    A RakNet status reply is not proof of client login or NetherNet connectivity.
    Unknown output formats deliberately leave the runtime version unknown.
    """
    result: Dict[str, Any] = {
        "responding": False,
        "player_count": None,
        "player_names": [],
        "version": None,
        "probe_scope": "container_loopback",
    }
    if returncode != 0:
        return result
    match = re.fullmatch(
        r"127\.0\.0\.1:" + re.escape(BEDROCK_PORT)
        + r" : version=([0-9]{1,5}(?:\.[0-9]{1,5}){2,3})"
        + r" online=([0-9]{1,9}) max=([0-9]{1,9})",
        output.strip(),
    )
    if match and 0 <= int(match[2]) <= int(match[3]) and int(match[3]) > 0:
        result.update(responding=True, version=match[1], player_count=int(match[2]))
    return result


def bridge_status_from_mc_monitor() -> Dict[str, Any]:
    result = run_fixed(
        ["docker", "exec", CONTAINER_NAME, "mc-monitor", "status-bedrock", "--host", "127.0.0.1", "--port", BEDROCK_PORT],
        timeout=10,
    )
    return parse_bedrock_probe(result.stdout, result.returncode)


def status_payload() -> Dict[str, Any]:
    inspect_data = docker_inspect()
    stats = docker_stats() if inspect_data else {}
    state = (inspect_data or {}).get("State", {})
    started_at = str(state.get("StartedAt") or "")
    started = parse_started_at(started_at)
    uptime_seconds = None
    if started and state.get("Status") == "running":
        uptime_seconds = int((datetime.now(timezone.utc) - started).total_seconds())
    bridge = bridge_status_from_mc_monitor() if state.get("Status") == "running" else parse_bedrock_probe("", 1)
    if state.get("Status") != "running":
        server_status = "OFFLINE"
    elif bridge.get("responding"):
        server_status = "ONLINE"
    elif state.get("Restarting") or (state.get("Health") or {}).get("Status") == "starting":
        server_status = "STARTING"
    else:
        server_status = "RUNNING / Bridge応答なし"
    return {
        "server_status": server_status,
        "container": {
            "state": state.get("Status"),
            "health": (state.get("Health") or {}).get("Status"),
            "restart_count": (inspect_data or {}).get("RestartCount"),
            "started_at": started_at,
            "uptime_seconds": uptime_seconds,
            "cpu_percent": stats.get("CPUPerc"),
            "memory": stats.get("MemUsage"),
            "memory_percent": stats.get("MemPerc"),
        },
        "host": {
            "cpu_percent": host_cpu_percent(),
            "memory": host_memory(),
        },
        "bds": {
            "version": bridge.get("version"),
            "version_source": "loopback_status" if bridge.get("version") else None,
        },
        "connectivity": {
            "container_loopback": bridge.get("responding", False),
            "direct_ip_login": "not_tested",
            "friend_join": "not_tested",
        },
        "bridge": bridge,
    }


def manifest_path(pack_dir: Path) -> Path:
    return pack_dir / "manifest.json"


def read_manifest(pack_dir: Path) -> Dict[str, Any]:
    return json.loads(manifest_path(pack_dir).read_text(encoding="utf-8"))


def write_manifest(pack_dir: Path, manifest: Dict[str, Any]) -> None:
    manifest_path(pack_dir).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalized_tree_changed(source: Path, destination: Path) -> bool:
    if not destination.exists():
        return True
    source_files = sorted(path.relative_to(source) for path in source.rglob("*") if path.is_file())
    dest_files = sorted(path.relative_to(destination) for path in destination.rglob("*") if path.is_file())
    if source_files != dest_files:
        return True
    for relative in source_files:
        if relative.as_posix() == "manifest.json":
            source_manifest = read_manifest(source)
            dest_manifest = read_manifest(destination)
            source_manifest.get("header", {}).pop("version", None)
            dest_manifest.get("header", {}).pop("version", None)
            for module in source_manifest.get("modules", []) or []:
                module.pop("version", None)
            for module in dest_manifest.get("modules", []) or []:
                module.pop("version", None)
            if source_manifest != dest_manifest:
                return True
            continue
        if not filecmp.cmp(source / relative, destination / relative, shallow=False):
            return True
    return False


def bump_patch(version: List[int]) -> List[int]:
    values = [int(part) for part in (version or [1, 0, 0])]
    while len(values) < 3:
        values.append(0)
    values[2] += 1
    return values[:3]


def update_pack_versions(pack_dir: Path, live_pack_dir: Path) -> List[int]:
    manifest = read_manifest(pack_dir)
    live_manifest = read_manifest(live_pack_dir) if manifest_path(live_pack_dir).exists() else {}
    current_version = live_manifest.get("header", {}).get("version") or manifest.get("header", {}).get("version") or [1, 0, 0]
    new_version = bump_patch(current_version)
    manifest.setdefault("header", {})["version"] = new_version
    for module in manifest.get("modules", []) or []:
        module["version"] = new_version
    write_manifest(pack_dir, manifest)
    return new_version


def update_world_pack_reference(world_pack_file: Path, pack_uuid: str, version: List[int]) -> None:
    references = []
    if world_pack_file.exists():
        references = json.loads(world_pack_file.read_text(encoding="utf-8"))
    updated = False
    for reference in references:
        if reference.get("pack_id") == pack_uuid:
            reference["version"] = version
            updated = True
    if not updated:
        references.append({"pack_id": pack_uuid, "version": version})
    world_pack_file.write_text(json.dumps(references, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sync_packs() -> Dict[str, Any]:
    if cosmetic_applications.managed():
        return {"status": "managed_by_cosmetics", "changed_packs": []}
    if PACK_SOURCE_DIR is None or not PACK_SOURCE_DIR.exists():
        return {"status": "skipped", "reason": "pack_source_missing", "changed_packs": []}
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    changed: List[str] = []
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    with tempfile.TemporaryDirectory(prefix="minecraft-pack-sync-") as tmp_name:
        tmp_root = Path(tmp_name)
        for spec in PACK_SPECS:
            source = PACK_SOURCE_DIR / spec["source"]
            destination = spec["destination"]
            if not source.exists():
                continue
            if not normalized_tree_changed(source, destination):
                continue
            staged = tmp_root / spec["name"]
            shutil.copytree(source, staged)
            version = update_pack_versions(staged, destination)
            manifest = read_manifest(staged)
            pack_uuid = manifest.get("header", {}).get("uuid")
            if not pack_uuid:
                raise RuntimeError("missing pack uuid: {0}".format(spec["name"]))
            backup = BACKUP_DIR / "pack-{0}-{1}".format(spec["name"], stamp)
            if destination.exists():
                shutil.move(str(destination), str(backup))
            shutil.move(str(staged), str(destination))
            update_world_pack_reference(spec["world_pack_file"], pack_uuid, version)
            changed.append(spec["name"])
    return {"status": "updated" if changed else "unchanged", "changed_packs": changed}


def create_backup() -> str:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup_file = BACKUP_DIR / "creative-before-restart-{0}.tar.gz".format(
        datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    )
    with tarfile.open(backup_file, "w:gz") as archive:
        archive.add(PROJECT_DIR / "docker-compose.yml", arcname="docker-compose.yml")
        archive.add(DATA_DIR / "worlds" / WORLD_NAME, arcname="data/worlds/{0}".format(WORLD_NAME))
        for kind in ("behavior_packs", "resource_packs"):
            for manifest in sorted((DATA_DIR / kind).glob("*/manifest.json")):
                pack = manifest.parent
                archive.add(pack, arcname=str(pack.relative_to(PROJECT_DIR)))
        active = PROJECT_DIR / "cosmetics-applications" / "active.json"
        if active.is_file():
            archive.add(active, arcname=str(active.relative_to(PROJECT_DIR)))
        for pack_ref in (
            DATA_DIR / "worlds" / WORLD_NAME / "world_behavior_packs.json",
            DATA_DIR / "worlds" / WORLD_NAME / "world_resource_packs.json",
        ):
            if pack_ref.exists():
                archive.add(pack_ref, arcname=str(pack_ref.relative_to(PROJECT_DIR)))
    return str(backup_file)


def prune_old_backups() -> List[str]:
    if BACKUP_RETENTION <= 0 or not BACKUP_DIR.exists():
        return []
    backups = sorted(
        BACKUP_DIR.glob("creative-before-restart-*.tar.gz"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    removed = []
    for path in backups[BACKUP_RETENTION:]:
        try:
            path.unlink()
            removed.append(str(path))
        except OSError:
            continue
    return removed


def send_save_command(command: str) -> Dict[str, Any]:
    result = run_fixed(["docker", "exec", "-u", "root", CONTAINER_NAME, "send-command", command], timeout=20)
    return {"command": command, "returncode": result.returncode, "stderr": result.stderr[-500:], "stdout": result.stdout[-500:]}


def graceful_save() -> List[Dict[str, Any]]:
    results = []
    for command in ("save hold", "save query", "save resume"):
        results.append(send_save_command(command))
    return results


def wait_for_container_stopped(timeout_seconds: int) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        state = (docker_inspect() or {}).get("State", {})
        if state.get("Status") != "running":
            return True
        time.sleep(2)
    return False


def wait_for_ready(timeout_seconds: int, *, require_healthy: bool = False) -> Dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    latest = status_payload()
    while True:
        container = latest.get("container") or {}
        # UDP can respond before Docker finishes its first health check. Cosmetic
        # apply/rollback must wait for BOTH, rather than rejecting "starting".
        # Existing restart callers retain their UDP-based readiness contract.
        if (container.get("state") == "running" and latest.get("bridge", {}).get("responding")
                and (not require_healthy or container.get("health") == "healthy")):
            return latest
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return latest
        time.sleep(min(2, remaining))
        latest = status_payload()


@app.get("/status")
def get_status(x_minecraft_control_secret: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    require_secret(x_minecraft_control_secret)
    return status_payload()


@app.post("/restart")
def restart_server(x_minecraft_control_secret: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    require_secret(x_minecraft_control_secret)
    with deployment_lock():
        return restart_server_locked()


def restart_server_locked() -> Dict[str, Any]:
    before = status_payload()
    save_results = []
    stop_result = None
    if before.get("container", {}).get("state") == "running":
        if USE_SAVE_HOLD:
            save_results = graceful_save()
            if any(result["returncode"] != 0 for result in save_results):
                raise HTTPException(status_code=500, detail="save hold failed")
        stop_result = run_fixed(["docker", "compose", "stop", "-t", "60", COMPOSE_SERVICE], timeout=90)
        if stop_result.returncode != 0:
            raise HTTPException(status_code=500, detail="docker compose stop failed")
        if not wait_for_container_stopped(30):
            raise HTTPException(status_code=500, detail="container did not stop")
    backup_file = create_backup()
    pack_sync = sync_packs()
    up = run_fixed(["docker", "compose", "up", "-d", COMPOSE_SERVICE], timeout=120)
    if up.returncode != 0:
        raise HTTPException(status_code=500, detail="docker compose up failed")
    latest = wait_for_ready(RESTART_WAIT_SECONDS)
    latest["backup_file"] = backup_file
    latest["pack_sync"] = pack_sync
    latest["graceful_save"] = save_results
    latest["docker_stop"] = {
        "returncode": stop_result.returncode if stop_result else 0,
        "stderr": (stop_result.stderr if stop_result else "")[-500:],
    }
    latest["removed_backups"] = prune_old_backups()
    return latest
