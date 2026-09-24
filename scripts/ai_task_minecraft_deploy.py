"""Offline Minecraft deployment preparation and trusted adapter composition.

No SSH or environment discovery occurs in this module. Runtime transport and
trusted environment handling live in ai_task_minecraft_runtime.py.
Only the reviewed merge SHA crosses the deployment boundary.
"""

import hashlib
import io
import json
import logging
import re
import tarfile
from dataclasses import dataclass
from uuid import UUID

from ai_task_deploy import DeploymentResult as AppDeploymentResult
from ai_task_deploy_config import DeploymentError, exception_detail
from ai_task_diagnostics import redact_secrets


KINDS = ("behavior_packs", "resource_packs")


@dataclass(frozen=True)
class DeploymentResult(AppDeploymentResult):
    verified_targets: frozenset[str] = frozenset({"apps"})


def required_targets(changed_files):
    if not isinstance(changed_files, (list, tuple)):
        reject()
    targets = {"apps"}
    for path in changed_files:
        if (not isinstance(path, str) or not path or "\0" in path
                or any(part in ("", ".", "..") for part in path.split("/"))):
            reject()
        if (path.startswith("minecraft/")
                or path.startswith("bot/services/minecraft_cosmetics")
                or path in {"admin/minecraft_cosmetics.py", "admin/minecraft_release.py",
                            "bot/repositories/minecraft_cosmetics.py"}):
            targets.add("minecraft")
    return frozenset(targets)


def verify_deployment(proof, sha, targets):
    validate_sha(sha)
    if (not isinstance(proof, AppDeploymentResult)
            or proof.deployed_commit_sha != sha
            or not isinstance(proof.summary, str) or not proof.summary.strip()):
        reject()
    # The existing fixed app adapter predates target-specific proof. Its result
    # proves apps only; it must never satisfy a Minecraft requirement.
    verified = (proof.verified_targets if isinstance(proof, DeploymentResult)
                else frozenset({"apps"}))
    if (not isinstance(verified, frozenset)
            or not verified <= frozenset({"apps", "minecraft"})
            or not targets <= verified):
        reject()


def reject():
    raise DeploymentError("Minecraft deployment input rejected")


def validate_sha(sha):
    if not isinstance(sha, str) or re.fullmatch(r"[0-9a-f]{40}", sha) is None:
        reject()


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                reject()
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: reject())
    except (ValueError, TypeError, UnicodeError, RecursionError):
        reject()


def identity(entry, uuid_key):
    if not isinstance(entry, dict):
        reject()
    uuid = entry.get(uuid_key)
    version = entry.get("version")
    try:
        if not isinstance(uuid, str) or str(UUID(uuid)) != uuid.lower():
            reject()
    except ValueError:
        reject()
    if (not isinstance(version, list) or len(version) != 3
            or any(type(part) is not int or not 0 <= part <= 2147483647 for part in version)):
        reject()
    return uuid.lower(), version


def sync_world_json(raw, manifests):
    """Validate the entire existing document before replacing matching versions."""
    entries = strict_json(raw)
    if not isinstance(entries, list):
        reject()
    positions = {}
    for index, entry in enumerate(entries):
        uuid, _ = identity(entry, "pack_id")
        if uuid in positions:
            reject()
        positions[uuid] = index
    seen = set()
    for manifest in manifests:
        parsed = strict_json(manifest)
        if not isinstance(parsed, dict):
            reject()
        uuid, version = identity(parsed.get("header"), "uuid")
        if uuid in seen:
            reject()
        seen.add(uuid)
        if uuid in positions:
            entries[positions[uuid]] = {**entries[positions[uuid]], "version": version}
        else:
            entries.append({"pack_id": uuid, "version": version})
    return json.dumps(entries, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"


def prepare_pack_archive(raw):
    """Validate a packs-only exact-commit archive without extracting any paths.

    Unsupported Minecraft artifacts must be handled explicitly by the
    deployment transport; they must never be silently counted as deployed.
    """
    if not isinstance(raw, bytes):
        reject()
    files, names = {}, set()
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            for member in archive:
                name = member.name.rstrip("/") if member.isdir() else member.name
                parts = name.split("/")
                if (name in names or "\0" in name
                        or any(p in ("", ".", "..") for p in parts)
                        or parts[0] != "minecraft"
                        or (len(parts) > 1 and parts[1] not in KINDS)
                        or not (member.isfile() or member.isdir())
                        or (member.isfile() and len(parts) < 4)):
                    reject()
                names.add(name)
                if member.isfile():
                    if member.size < 0:
                        reject()
                    stream = archive.extractfile(member)
                    if stream is None:
                        reject()
                    files[name] = stream.read()
                    if len(files[name]) != member.size:
                        reject()
        for name in files:
            if any("/".join(name.split("/")[:i]) in files for i in range(1, len(name.split("/")))):
                reject()
        packs = {"/".join(name.split("/")[:3]) for name in files}
        if not packs:
            reject()
        for kind in KINDS:
            manifests = [files[pack + "/manifest.json"] for pack in sorted(packs)
                         if pack.split("/")[1] == kind]
            sync_world_json(b"[]", manifests)
    except (tarfile.TarError, OSError, KeyError, ValueError) as exc:
        raise DeploymentError("Minecraft pack archive invalid: " + exception_detail(exc)) from None
    digest = hashlib.sha256()
    for name, content in sorted(files.items()):
        digest.update(name.encode("utf-8") + b"\0")
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return files, digest.hexdigest()


class TargetDeployAdapter:
    """Composition for trusted, SHA-bound artifact and runtime adapters only.

    source.changed_files reads the exact merge's parent diff. minecraft.deploy
    must itself stage, back up, verify and roll back, returning proof only after
    sustained runtime health. Neither dependency may come from task text.
    Construction is inert, so absent BDS configuration cannot affect app tasks.
    """

    def __init__(self, apps, source, minecraft_factory):
        self.apps = apps
        self.source = source
        self.minecraft_factory = minecraft_factory

    def deploy(self, merge_sha, *, stop_event=None):
        validate_sha(merge_sha)
        targets = required_targets(self.source.changed_files(merge_sha))
        if stop_event is not None and stop_event.is_set():
            reject()
        app = self.apps.deploy(merge_sha, stop_event=stop_event)
        verify_deployment(app, merge_sha, frozenset({"apps"}))
        if stop_event is not None and stop_event.is_set():
            reject()
        summaries = [app.summary]
        if "minecraft" in targets:
            minecraft = self.minecraft_factory().deploy(merge_sha, stop_event=stop_event)
            verify_deployment(minecraft, merge_sha, frozenset({"minecraft"}))
            summaries.append(minecraft.summary)
        if stop_event is not None and stop_event.is_set():
            reject()
        return DeploymentResult(merge_sha, " ".join(summaries), targets)

    def catch_up(self, *, stop_event=None):
        """Independent best-effort operation; failure carries no completion proof.

        Reconcile the exact app first, including migration/health, then ask that
        app for DB-managed packs. The managed route rechecks terminal proof and
        live health even when the operation was already applied.
        """
        try:
            sha = self.source.current_main_sha()
            validate_sha(sha)
            if stop_event is not None and stop_event.is_set():
                return False
            app = self.apps.deploy(sha, stop_event=stop_event)
            verify_deployment(app, sha, frozenset({"apps"}))
            if stop_event is not None and stop_event.is_set():
                return False
            proof = self.minecraft_factory().deploy(
                sha,
                stop_event=stop_event,
                refresh_source=False,
            )
            verify_deployment(proof, sha, frozenset({"minecraft"}))
            return stop_event is None or not stop_event.is_set()
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Minecraft catch-up failed: %s", redact_secrets(exception_detail(exc)))
            return False
