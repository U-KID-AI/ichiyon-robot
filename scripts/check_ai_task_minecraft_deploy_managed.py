"""Offline runner and authenticated app-route regressions. No production I/O."""

from copy import deepcopy
from pathlib import Path
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, Mock, patch
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from ai_task_api_client import RunnerAPIClient, RunnerAPIError
from ai_task_deploy import DeploymentResult as AppResult
from ai_task_deploy_config import DeploymentError
from ai_task_minecraft_deploy import TargetDeployAdapter, required_targets
from ai_task_minecraft_deploy_managed import ManagedMinecraftDeployAdapter, verified_result
from ai_task_production_release import release

with patch("dotenv.load_dotenv", return_value=False):
    from admin import minecraft_release as endpoint

from fastapi import FastAPI
from fastapi.testclient import TestClient

SHA = "a" * 40
DIGEST = "b" * 64
OPERATION = "0c05a9ea-fc31-4f7d-8562-527754bb9916"


def payload(state="succeeded"):
    return {"merge_sha": SHA, "expected_catalog_digest": DIGEST, "current_catalog_digest": DIGEST,
            "operation": {"operation_id": OPERATION, "catalog_digest": DIGEST,
                          "status": state, "installed": True, "active_digest": DIGEST},
            "runtime": {"container": {"state": "running", "health": "healthy"},
                        "bridge": {"responding": True}}}


class ManagedChecks(unittest.TestCase):
    def adapter(self, states=("queued", "starting", "succeeded")):
        client = Mock()
        client.minecraft_release.return_value = payload("queued")
        client.minecraft_release_status.side_effect = [payload(state) for state in states]
        return ManagedMinecraftDeployAdapter(client, sleep=lambda _: None), client

    def test_202_is_only_acceptance_and_proof_retained(self):
        adapter, client = self.adapter()
        proof = adapter.deploy(SHA)
        self.assertEqual(client.minecraft_release_status.call_count, 3)
        self.assertEqual(proof.verified_targets, frozenset({"minecraft"}))
        for value in (SHA, OPERATION, DIGEST, "running/healthy", "Bedrock=responding"):
            self.assertIn(value, proof.summary)

    def test_post_success_still_requires_fresh_get(self):
        adapter, client = self.adapter(("failed",))
        client.minecraft_release.return_value = payload()
        with self.assertRaises(DeploymentError):
            adapter.deploy(SHA)
        client.minecraft_release_status.assert_called_once()

    def test_every_pending_and_failed_status(self):
        adapter, _ = self.adapter(("queued", "stopping", "installing", "starting", "succeeded"))
        adapter.deploy(SHA)
        for state in ("failed", "recovery_failed", "rolled_back", "cancelled", "idle", "done", None, []):
            adapter, _ = self.adapter((state,))
            with self.subTest(state=state), self.assertRaises(DeploymentError):
                adapter.deploy(SHA)

    def test_missing_mismatched_digests_identity_and_health(self):
        cases = [("merge_sha", "c" * 40), ("runtime", None), ("operation", None),
                 ("current_catalog_digest", None), ("current_catalog_digest", "f" * 64)]
        nested = {
            "operation": [("operation_id", str(uuid4())), ("catalog_digest", "d" * 64),
                          ("catalog_digest", None), ("active_digest", "e" * 64),
                          ("active_digest", None), ("installed", False), ("installed", 1),
                          ("status", "starting")],
            "container": [("state", "exited"), ("health", "starting"), ("health", None)],
            "bridge": [("responding", False), ("responding", "true")],
        }
        samples = []
        for key, value in cases:
            sample = payload(); sample[key] = value; samples.append(sample)
        for section, changes in nested.items():
            for key, value in changes:
                sample = payload()
                target = sample[section] if section == "operation" else sample["runtime"][section]
                target[key] = value; samples.append(sample)
        for sample in samples:
            with self.subTest(sample=sample), self.assertRaises(DeploymentError):
                verified_result(sample, SHA, OPERATION, DIGEST)

    def test_acceptance_expected_digest_required(self):
        for digest in (None, "", "c" * 64, 1):
            adapter, client = self.adapter()
            client.minecraft_release.return_value["expected_catalog_digest"] = digest
            with self.subTest(digest=digest), self.assertRaises(DeploymentError):
                adapter.deploy(SHA)
            client.minecraft_release_status.assert_not_called()

    def test_poll_timeout_and_lease_loss(self):
        adapter, client = self.adapter()
        adapter.clock = Mock(side_effect=[0, 0, 0, 1801])
        with self.assertRaisesRegex(DeploymentError, "timed out"):
            adapter.deploy(SHA)
        stop = threading.Event(); stop.set()
        adapter, client = self.adapter()
        with self.assertRaisesRegex(DeploymentError, "lease lost"):
            adapter.deploy(SHA, stop_event=stop)
        client.minecraft_release.assert_not_called()
        stop.clear()
        def lose_lease(*args):
            stop.set()
            return payload()
        client.minecraft_release_status.side_effect = lose_lease
        with self.assertRaisesRegex(DeploymentError, "lease lost"):
            adapter.deploy(SHA, stop_event=stop)

    def test_transport_failure_never_falls_back(self):
        for method in ("minecraft_release", "minecraft_release_status"):
            adapter, client = self.adapter()
            getattr(client, method).side_effect = RunnerAPIError("Control API unavailable")
            with patch("subprocess.Popen") as popen, self.assertRaises(DeploymentError):
                adapter.deploy(SHA)
            popen.assert_not_called()

    def test_timing_input(self):
        for value in (0, -1, float("inf"), float("nan")):
            with self.assertRaises(DeploymentError):
                ManagedMinecraftDeployAdapter(Mock(), timeout=value)

    def test_target_scope_and_app_first_including_idle_and_helper(self):
        for path in ("minecraft/a", "bot/services/minecraft_cosmetics_pack.py",
                     "admin/minecraft_cosmetics.py", "admin/minecraft_release.py"):
            self.assertIn("minecraft", required_targets([path]))
        events = []
        apps = Mock()
        apps.deploy.side_effect = lambda *a, **kw: (events.append("apps") or AppResult(SHA, "app checked"))
        managed = Mock()
        managed.deploy.side_effect = lambda *a, **kw: (events.append("minecraft") or verified_result(payload(), SHA, OPERATION, DIGEST))
        source = Mock()
        source.changed_files.return_value = ["minecraft/a"]
        source.current_main_sha.return_value = SHA
        adapter = TargetDeployAdapter(apps, source, lambda: managed)
        self.assertIn(OPERATION, adapter.deploy(SHA).summary)
        self.assertTrue(adapter.catch_up())
        self.assertIn(OPERATION, release(SHA, apps, managed).summary)
        self.assertEqual(events, ["apps", "minecraft"] * 3)
        apps.deploy.side_effect = DeploymentError("app failed")
        managed.reset_mock()
        with self.assertRaises(DeploymentError): adapter.deploy(SHA)
        self.assertFalse(adapter.catch_up())
        with self.assertRaises(DeploymentError): release(SHA, apps, managed)
        managed.deploy.assert_not_called()

    def test_client_fixed_paths_and_payload(self):
        requester = Mock(return_value=json.dumps(payload()).encode())
        client = RunnerAPIClient("https://example.test", "test-only", "runner", requester=requester)
        client.minecraft_release(SHA)
        requester.assert_called_with("POST", "/internal/minecraft-release/" + SHA, {})
        client.minecraft_release_status(SHA, OPERATION)
        requester.assert_called_with("GET", f"/internal/minecraft-release/{SHA}/operations/{OPERATION}", None)
        with self.assertRaises(ValueError): client.minecraft_release("../bad")

    def test_only_release_post_gets_compilation_upload_and_staging_timeout(self):
        for configured in (15.0, 180.0, 900.0):
            with self.subTest(timeout=configured), patch("ai_task_api_client.urlopen") as request:
                request.return_value.__enter__.return_value.read.return_value = json.dumps(payload()).encode()
                client = RunnerAPIClient("https://example.test", "test-only", "runner", timeout=configured)
                client.minecraft_release(SHA)
                self.assertEqual(request.call_args.kwargs["timeout"], max(configured, 600.0))
                client.minecraft_release_status(SHA, OPERATION)
                self.assertEqual(request.call_args.kwargs["timeout"], configured)
                client.claim()
                self.assertEqual(request.call_args.kwargs["timeout"], configured)
                client.heartbeat(uuid4(), uuid4())
                self.assertEqual(request.call_args.kwargs["timeout"], configured)
                self.assertEqual(client.timeout, configured)

    def test_normal_runner_factory_needs_no_new_credentials(self):
        import ai_task_runner as runner
        config = SimpleNamespace(repo_root=ROOT, worktree_root=ROOT.parent, git_path=ROOT / "git",
                                 api_base_url="http://127.0.0.1:18765", api_token="test-only",
                                 runner_id="windows-prod-runner-1")
        with patch.dict(os.environ, {}, clear=True), patch.object(sys, "argv", ["runner", "--once"]), \
                patch.object(runner.RunnerConfig, "from_environment", return_value=config), \
                patch.object(runner.DeployConfig, "from_environment"), \
                patch.object(runner, "ProductionDeployAdapter"), patch.object(runner, "LocalRunner") as local, \
                patch.object(runner, "run_idle_maintenance"), patch.object(runner, "outcome_exit_code", return_value=0):
            self.assertEqual(runner.main(), 0)
            factory = local.call_args.kwargs["deployer"].minecraft_factory
            adapter = factory()
        self.assertIsInstance(adapter, ManagedMinecraftDeployAdapter)
        self.assertEqual(adapter.client.base_url, config.api_base_url)
        self.assertEqual(adapter.client.token, config.api_token)
        self.assertEqual(adapter.client.timeout, 180)

    def test_app_failure_proof_and_lease_loss_prevent_minecraft(self):
        apps = Mock()
        factory = Mock()
        source = Mock()
        source.changed_files.return_value = ["minecraft/a"]
        source.current_main_sha.return_value = SHA
        adapter = TargetDeployAdapter(apps, source, factory)
        apps.deploy.return_value = AppResult("c" * 40, "wrong app")
        with self.assertRaises(DeploymentError): adapter.deploy(SHA)
        self.assertFalse(adapter.catch_up())
        factory.assert_not_called()
        stop = threading.Event()
        def lose_lease(*args, **kwargs):
            stop.set()
            return AppResult(SHA, "app checked")
        apps.deploy.side_effect = lose_lease
        with self.assertRaises(DeploymentError): adapter.deploy(SHA, stop_event=stop)
        factory.assert_not_called()


class EndpointChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        revision = Path(self.temp.name) / "REVISION"
        revision.write_text(SHA + "\n", encoding="ascii")
        self.assets = [{"managed": "DB record"}]
        self.patches = [
            patch.object(endpoint, "REVISION", revision),
            patch("admin.ai_tasks_internal.config.AI_TASK_RUNNER_API_TOKEN", "test-runner-secret"),
            patch.object(endpoint, "catalog_snapshot", return_value=self.assets),
            patch.object(endpoint, "catalog_digest", return_value=DIGEST),
            patch.object(endpoint, "build_archive", return_value=b"compiled-with-managed-assets"),
            patch.object(endpoint, "fetch_control_status", new_callable=AsyncMock),
            patch.object(endpoint, "cosmetics_control", new_callable=AsyncMock),
        ]
        for item in self.patches:
            item.start(); self.addCleanup(item.stop)
        self.latest = {"status": "idle"}
        async def control(operation_id=None, archive=None):
            if operation_id is not None:
                self.latest = {"operation_id": operation_id, "catalog_digest": DIGEST, "status": "queued"}
            return deepcopy(self.latest)
        endpoint.cosmetics_control.side_effect = control
        endpoint.fetch_control_status.return_value = payload()["runtime"]
        app = FastAPI(); app.include_router(endpoint.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.headers = {"Authorization": "Bearer test-runner-secret"}

    def post(self, sha=SHA, body=None):
        return self.client.post("/internal/minecraft-release/" + sha,
                                headers=self.headers, json=body or {})

    def test_auth_and_sha_before_db_or_control(self):
        self.assertEqual(self.client.post("/internal/minecraft-release/" + SHA, json={}).status_code, 401)
        self.assertEqual(self.post("c" * 40).status_code, 409)
        self.assertEqual(self.post("bad").status_code, 422)
        endpoint.REVISION.unlink()
        self.assertEqual(self.post().status_code, 503)
        endpoint.catalog_snapshot.assert_not_called()
        endpoint.cosmetics_control.assert_not_called()

    def test_generation_uses_db_snapshot_and_control_client_only(self):
        response = self.post()
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["expected_catalog_digest"], DIGEST)
        endpoint.build_archive.assert_called_once_with(self.assets)
        endpoint.cosmetics_control.assert_awaited_with(self.latest["operation_id"], b"compiled-with-managed-assets")
        self.assertEqual(self.post().json(), response.json())
        endpoint.build_archive.assert_called_once()

    def test_retry_changes_operation_without_erasing_failed_record(self):
        first = self.post().json()["operation"]["operation_id"]
        self.latest["status"] = "failed"
        self.assertEqual(self.post().json()["operation"]["status"], "failed")
        retried = self.post(body={"attempt": str(uuid4())})
        self.assertNotEqual(retried.json()["operation"]["operation_id"], first)

    def test_control_unavailable_prevents_generation(self):
        endpoint.fetch_control_status.side_effect = endpoint.MinecraftControlError("secret details")
        response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("secret details", response.text)
        endpoint.build_archive.assert_not_called()

    def test_poll_identity_live_health_and_supersession(self):
        operation_id = self.post().json()["operation"]["operation_id"]
        url = f"/internal/minecraft-release/{SHA}/operations/{operation_id}"
        self.assertIsNone(self.client.get(url, headers=self.headers).json()["runtime"])
        self.latest.update(status="succeeded", installed=True, active_digest=DIGEST)
        result = self.client.get(url, headers=self.headers)
        self.assertEqual(result.status_code, 200)
        verified_result(result.json(), SHA, operation_id, DIGEST)
        self.latest["operation_id"] = str(uuid4())
        self.assertEqual(self.client.get(url, headers=self.headers).status_code, 409)

    def test_control_digest_mismatch(self):
        async def wrong(operation_id=None, archive=None):
            return {"operation_id": operation_id, "catalog_digest": "c" * 64, "status": "queued"}
        endpoint.cosmetics_control.side_effect = wrong
        self.assertEqual(self.post().status_code, 409)

    def test_generation_exact_sha_changes_operation_even_if_catalog_unchanged(self):
        first = self.post().json()["operation"]["operation_id"]
        endpoint.REVISION.write_text("c" * 40 + "\n", encoding="ascii")
        second = self.post("c" * 40).json()["operation"]["operation_id"]
        self.assertNotEqual(first, second)

    def test_current_db_drift_cannot_prove_preservation(self):
        operation_id = self.post().json()["operation"]["operation_id"]
        self.latest.update(status="succeeded", installed=True, active_digest=DIGEST)
        endpoint.catalog_digest.return_value = "c" * 64
        response = self.client.get(f"/internal/minecraft-release/{SHA}/operations/{operation_id}", headers=self.headers)
        with self.assertRaisesRegex(DeploymentError, "DB assets"):
            verified_result(response.json(), SHA, operation_id, DIGEST)


class PreservationChecks(unittest.TestCase):
    def test_real_builder_preserves_db_blobs_ids_and_excludes_deleted_builtins(self):
        from contextlib import contextmanager
        from io import BytesIO
        from zipfile import ZipFile
        from admin.minecraft_cosmetics import records
        from bot.services.minecraft_cosmetics import asset, public_asset
        from bot.services.minecraft_cosmetics_pack import catalog_digest
        from bot.services.minecraft_resource_packs import ACCESSORIES
        from check_minecraft_cosmetics import fixture_assets, png

        stored = fixture_assets() + [asset("poster", 1, "poster_1", "Stored poster",
                                           png((192, 128)), width=3, height=2)]
        repository = Mock()
        repository.assets.return_value = stored
        repository.deleted_assets.return_value = {("skin", 1)}
        repository.revision.return_value = 22
        connection = Mock()
        @contextmanager
        def connect():
            yield connection
        with patch.object(endpoint, "get_connection", connect), patch.object(
                endpoint, "MinecraftCosmeticsRepository", return_value=repository):
            snapshot = endpoint.catalog_snapshot()
            self.assertEqual(snapshot, records(repository))
            self.assertNotIn(("skin", 1), {(a["kind"], a["id"]) for a in snapshot})
            archive = endpoint.build_archive(snapshot)
        with ZipFile(BytesIO(archive)) as zip_file:
            lock = json.loads(zip_file.read("cosmetics/catalog.lock.json"))
            self.assertEqual(lock["digest"], catalog_digest(snapshot))
            entries = lock["skins"] + lock["accessories"] + lock["posters"]
            for record in stored:
                actual = next(a for a in entries if a["kind"] == record["kind"] and a["id"] == record["id"])
                for key, value in public_asset(record).items():
                    self.assertEqual(actual[key], value)
                for blob in ("texture", "icon"):
                    if blob in record:
                        self.assertTrue(any(zip_file.read(name) == record[blob] for name in zip_file.namelist()),
                                        f"{record['kind']} {record['id']} {blob} missing")
                if "geometry" in record:
                    model = json.loads(zip_file.read(ACCESSORIES + "models/entity/cosmetics/molcar_cosmetic_1.geo.json"))
                    actual_bones = model["minecraft:geometry"][0]["bones"]
                    for bone in json.loads(record["geometry"])["minecraft:geometry"][0]["bones"]:
                        actual = next(b for b in actual_bones if b["name"] == "cosmetic_1_" + bone["name"])
                        self.assertEqual(actual["cubes"], bone["cubes"])
                        self.assertEqual(actual["pivot"], bone["pivot"])
        changed = deepcopy(snapshot)
        changed[-1]["name"] = "changed"
        self.assertNotEqual(catalog_digest(changed), catalog_digest(snapshot))


if __name__ == "__main__":
    unittest.main()
