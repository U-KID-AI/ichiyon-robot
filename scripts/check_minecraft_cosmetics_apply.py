"""Offline filesystem transactions: healthy apply, rollback, restart recovery and hostile ZIPs."""
from contextlib import nullcontext
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile
import json
import shutil
import sys
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts/minecraft')]
import minecraft_cosmetics_apply as deploy
from bot.services.minecraft_cosmetics_pack import builtin_assets, pack_zip


class NoThread:
    def __init__(self, **kwargs): pass
    def start(self): pass
    def is_alive(self): return False


class API:
    WORLD_NAME = 'test-world'
    COMPOSE_SERVICE = 'test-service'
    RESTART_WAIT_SECONDS = 1
    def __init__(self, root):
        self.PROJECT_DIR = root
        self.DATA_DIR = root / 'data'
        self.commands = []
        self.health_failures = 0
    def run_fixed(self, args, timeout):
        self.commands.append(args)
        return SimpleNamespace(returncode=0)
    def wait_for_container_stopped(self, timeout): return True
    def status_payload(self): return {'container': {'state':'running', 'health':'healthy'}, 'bridge': {'responding':True}}
    def wait_for_ready(self, timeout, *, require_healthy=False):
        if self.health_failures:
            self.health_failures -= 1
            return {'container': {'state':'running', 'health':'unhealthy'}}
        return self.status_payload()
    def update_world_pack_reference(self, path, pack_id, version):
        refs = json.loads(path.read_text(encoding='utf-8'))
        refs = [r for r in refs if r['pack_id'] != pack_id]
        refs.append({'pack_id':pack_id, 'version':version})
        deploy.atomic_json(path, refs)


class ApplyChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.archive = pack_zip(ROOT / 'minecraft', builtin_assets(ROOT / 'minecraft'), 1)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.api = API(Path(temporary.name))
        for pack in deploy.PACKS:
            shutil.copytree(ROOT / 'minecraft' / pack, self.api.DATA_DIR / pack)
        for kind in ('behavior', 'resource'):
            deploy.atomic_json(self.api.DATA_DIR / 'worlds/test-world' / f'world_{kind}_packs.json', [{'pack_id':'keep-other-pack','version':[2,0,0]}])
        deploy.atomic_json(self.api.DATA_DIR / deploy.PERMISSIONS, {'allowed_modules':['@minecraft/server'], 'module_permissions': {'preserve':True}})
        self.manager = deploy.PackApplications(self.api)
        self.identifier = str(uuid.uuid4())
        for p in (patch.object(deploy, 'deployment_lock', nullcontext), patch.object(deploy.threading, 'Thread', NoThread)):
            p.start(); self.addCleanup(p.stop)

    def submit(self): return self.manager.submit(self.identifier, self.archive)

    def test_apply_preserves_world_other_packs_permissions_and_records_success(self):
        job = self.submit()
        self.manager._run(job)
        status = self.manager.status()
        self.assertEqual(status['status'], 'succeeded')
        self.assertTrue(status['installed']); self.assertTrue(self.manager.managed())
        self.assertEqual(len(self.api.commands), 2)
        refs = json.loads((self.api.DATA_DIR / 'worlds/test-world/world_behavior_packs.json').read_text(encoding='utf-8'))
        self.assertEqual(refs[0]['pack_id'], 'keep-other-pack'); self.assertEqual(len(refs), 3)
        permission = json.loads((self.api.DATA_DIR / deploy.PERMISSIONS).read_text(encoding='utf-8'))
        self.assertTrue(permission['module_permissions']['preserve']); self.assertIn('@minecraft/server-ui', permission['allowed_modules'])

    def test_duplicate_submit_does_not_start_second_restart_and_other_id_is_busy(self):
        self.submit()
        self.assertEqual(self.manager.submit(self.identifier, b'ignored retry')['operation_id'], self.identifier)
        with self.assertRaises(RuntimeError): self.manager.submit(str(uuid.uuid4()), self.archive)
        self.assertFalse(self.api.commands)

    def test_health_failure_restores_every_pack_and_reference(self):
        original = {str(p.relative_to(self.api.DATA_DIR)):p.read_bytes() for p in self.api.DATA_DIR.rglob('*') if p.is_file()}
        job = self.submit(); self.api.health_failures = 1
        self.manager._run(job)
        self.assertEqual(self.manager.status()['status'], 'failed')
        restored = {str(p.relative_to(self.api.DATA_DIR)):p.read_bytes() for p in self.api.DATA_DIR.rglob('*') if p.is_file()}
        self.assertEqual(original, restored)
        self.assertFalse(self.manager.managed())

    def test_interrupted_install_recovers_from_durable_backup(self):
        job = self.submit()
        directory = self.manager.root / self.identifier
        old = (self.api.DATA_DIR / deploy.PACKS[1] / 'manifest.json').read_bytes()
        self.manager._backup(directory)
        self.manager._save(job, status='installing')
        self.manager._install(directory)
        recovered = deploy.PackApplications(self.api)
        recovered._run(recovered.status(), recovering=True)
        self.assertEqual(recovered.status()['status'], 'failed')
        self.assertEqual((self.api.DATA_DIR / deploy.PACKS[1] / 'manifest.json').read_bytes(), old)

    def test_bad_archive_never_stops_server(self):
        for name in ('../outside', 'behavior_packs/ichiyon_avatar_bp/../../outside', 'behavior_packs/ichiyon_avatar_bp/a/../x', 'worlds/test-world/level.dat'):
            stream = BytesIO()
            with ZipFile(stream, 'w') as archive: archive.writestr(name, b'x')
            with self.assertRaises(ValueError): self.manager.submit(str(uuid.uuid4()), stream.getvalue())
        self.assertFalse(self.api.commands)

    def test_pack_uuid_substitution_is_rejected(self):
        stream = BytesIO()
        with ZipFile(BytesIO(self.archive)) as old, ZipFile(stream, 'w') as new:
            for name in old.namelist():
                data = old.read(name)
                if name == deploy.PACKS[1] + '/manifest.json':
                    value = json.loads(data); value['header']['uuid'] = str(uuid.uuid4()); data = json.dumps(value).encode()
                new.writestr(name, data)
        with self.assertRaises(ValueError): self.manager.submit(self.identifier, stream.getvalue())
        self.assertFalse(self.api.commands)

    def test_next_runtime_version_increases_even_with_same_export_revision(self):
        job = self.submit(); self.manager._run(job)
        first = json.loads((self.api.DATA_DIR / deploy.PACKS[1] / 'manifest.json').read_text(encoding='utf-8'))['header']['version']
        job = self.manager.submit(str(uuid.uuid4()), self.archive); self.manager._run(job)
        second = json.loads((self.api.DATA_DIR / deploy.PACKS[1] / 'manifest.json').read_text(encoding='utf-8'))['header']['version']
        self.assertGreater(second, first)


class ReadinessChecks(unittest.TestCase):
    def setUp(self):
        import minecraft_control_api as api
        self.api = api
        self.elapsed = 0
        def sleep(seconds): self.elapsed += seconds
        for p in (patch.object(api.time, 'monotonic', side_effect=lambda: self.elapsed),
                  patch.object(api.time, 'sleep', side_effect=sleep),
                  patch.object(api, 'run_fixed', return_value=SimpleNamespace(returncode=0))):
            p.start(); self.addCleanup(p.stop)

    def state(self, health='healthy', responding=True):
        return {'container': {'state':'running', 'health':health}, 'bridge': {'responding':responding}}

    def test_apply_and_rollback_start_wait_for_docker_health_after_udp_responds(self):
        # Exercise the real _start + wait_for_ready pair used in both transactions.
        with patch.object(self.api, 'status_payload', side_effect=[self.state('starting'), self.state('starting'), self.state()]) as status:
            deploy.PackApplications(self.api)._start()
        self.assertEqual(status.call_count, 3)
        self.assertEqual(self.elapsed, 4)

    def test_docker_health_alone_is_not_ready_without_bedrock_response(self):
        with patch.object(self.api, 'status_payload', side_effect=[self.state(responding=False), self.state()]) as status:
            deploy.PackApplications(self.api)._start()
        self.assertEqual(status.call_count, 2)

    def test_timeout_still_fails_closed_after_waiting(self):
        for state in (self.state('starting'), self.state('unhealthy'), self.state(None), self.state(responding=False)):
            with self.subTest(state=state), patch.object(self.api, 'RESTART_WAIT_SECONDS', 5), patch.object(self.api, 'status_payload', return_value=state):
                before = self.elapsed
                with self.assertRaisesRegex(RuntimeError, 'health failed'):
                    deploy.PackApplications(self.api)._start()
                self.assertEqual(self.elapsed - before, 5)

    def test_existing_restart_keeps_udp_readiness(self):
        with patch.object(self.api, 'status_payload', return_value=self.state('starting')):
            result = self.api.wait_for_ready(5)
        self.assertEqual(result['container']['health'], 'starting')
        self.assertEqual(self.elapsed, 0)


class ControlHTTPChecks(unittest.TestCase):
    def test_control_auth_body_validation_and_fixed_operation(self):
        from fastapi.testclient import TestClient
        import minecraft_control_api as api
        with patch.object(api, 'CONTROL_SECRET', 'offline-control-test'), patch.object(api, 'cosmetic_applications') as manager:
            client = TestClient(api.app)
            identifier = str(uuid.uuid4())
            self.assertEqual(client.get('/cosmetics').status_code, 401)
            self.assertEqual(client.post('/cosmetics/' + identifier, content=b'x').status_code, 401)
            manager.submit.assert_not_called()
            headers = {'X-Minecraft-Control-Secret':'offline-control-test'}
            manager.submit.side_effect = ValueError('never disclose details')
            response = client.post('/cosmetics/' + identifier, content=b'x', headers=headers)
            self.assertEqual(response.status_code, 400); self.assertNotIn('disclose', response.text)
            manager.submit.side_effect = RuntimeError('busy')
            self.assertEqual(client.post('/cosmetics/' + identifier, content=b'x', headers=headers).status_code, 409)
            manager.submit.side_effect = None; manager.submit.return_value = {'status':'queued'}
            self.assertEqual(client.post('/cosmetics/' + identifier, content=b'pack', headers=headers).status_code, 202)
            manager.submit.assert_called_with(identifier, b'pack')
            client.close()


if __name__ == '__main__': unittest.main()
