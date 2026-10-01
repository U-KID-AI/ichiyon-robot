"""Offline monitor + Discord fault injection; no production/network/DB access."""
import asyncio
from contextlib import redirect_stdout
from dataclasses import replace
import importlib
import hashlib
import io
import json
import os
import stat
import sqlite3
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import ai_task_storage as storage
import ai_task_storage_monitor as monitor
import ai_task_storage_evidence_store as evidence_store
from check_ai_task_storage import fixture

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import dotenv
with patch.dict(os.environ, {}, clear=True), patch.object(dotenv, 'load_dotenv', return_value=False):
    from bot.services import storage_monitor as notifier


def report(status='WARNING', sampled=1000):
    return dict(schema_version=1, sampled_at=sampled, status=status,
        reasons=['P0_RESERVE_HEADROOM_LOW' if status == 'WARNING' else
                 'P0_REJECTED' if status == 'CRITICAL' else 'P0_SUFFICIENT'],
        p0=[dict(phase='runner', filesystem='releases+docker', available_bytes=12*storage.GIB,
                 required_bytes=10*storage.GIB, available_inodes=90000, required_inodes=80000)])


class EvidenceMetricTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'evidence'
        self.root.mkdir(mode=0o700)
        for namespace in ('operations', 'active', '.pending'):
            (self.root / namespace).mkdir(mode=0o700)
        for number in range(2):
            (self.root / 'operations' / (str(number) + '.json')).write_bytes(b'legacy sentinel')
            (self.root / 'active' / (str(number) + '.json')).write_bytes(b'legacy sentinel')
        for target, name, value in ((monitor, 'EVIDENCE', self.root),
                                    (evidence_store, 'ROOT', self.root),
                                    (evidence_store, '_uid', lambda: os.getuid() if hasattr(os, 'getuid') else 0)):
            mocked = patch.object(target, name, value)
            mocked.start(); self.addCleanup(mocked.stop)

    def inventory(self):
        return {str(p.relative_to(self.root)): (p.stat().st_ino, p.stat().st_size,
                    p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
                for p in self.root.rglob('*') if p.is_file()}

    def populated_index(self):
        def payload(opid):
            return dict(operation_id=opid, state='running', target_sha='a'*40, previous_sha='a'*40,
                        sequence=1, created_at=1.0, completed_at=None, owner={}, lock={},
                        initial_names={}, initial_runtime={}, staging=[], rollback_result=None)
        with evidence_store.Store(create=True) as writer:
            opid = writer.begin(payload)
            active = writer.get_active(opid)
            terminal = dict(active, state='succeeded', sequence=2, completed_at=2.0,
                            rollback_result='not_needed', active_payload_sha256=evidence_store.digest(active))
            writer.finish(terminal, 'C', proof=dict(persistent_absence_verified=True,
                          health_verified=True, cleanup_verified=True))

    def test_legacy_only_before_first_v2_publication(self):
        before = self.inventory()
        with patch.object(evidence_store, 'read_session') as reader:
            value, errors = monitor.evidence_metrics()
        reader.assert_not_called()
        self.assertEqual(value['operation_entries'], 2)
        self.assertEqual(value['legacy_active_entries'], 2)
        self.assertEqual(value['legacy_pending_entries'], 0)
        self.assertEqual(value['index_status'], 'not_initialized')
        self.assertNotIn('index_counters', value)
        self.assertEqual(errors, [])
        self.assertEqual(self.inventory(), before)

    def test_legacy_and_v2_counts_read_without_any_file_change(self):
        self.populated_index()
        before = self.inventory()
        value, errors = monitor.evidence_metrics()
        self.assertEqual(errors, [])
        self.assertEqual(value['index_status'], 'verified')
        self.assertEqual(value['operation_entries'], 2)
        self.assertEqual(value['index_counters']['C_total'], 1)
        self.assertEqual(value['index_counters']['recent'], 1)
        self.assertEqual(value['index_counters']['rollups'], 1)
        self.assertEqual(value['index_counters']['active'], 0)
        self.assertEqual(self.inventory(), before)

    def test_new_v2_only_bootstrap_counts_absent_legacy_without_error(self):
        self.populated_index()
        for namespace in ('operations', 'active', '.pending'):
            path = self.root / namespace
            for file in path.iterdir():
                file.unlink()
            path.rmdir()
        before = self.inventory()
        value, errors = monitor.evidence_metrics()
        self.assertEqual(errors, [])
        self.assertEqual(value['legacy_status'], 'not_present')
        self.assertEqual(value['operation_entries'], 0)
        self.assertEqual(value['legacy_active_entries'], 0)
        self.assertEqual(value['legacy_pending_entries'], 0)
        self.assertEqual(value['index_status'], 'verified')
        self.assertEqual(value['index_counters']['C_total'], 1)
        self.assertEqual(self.inventory(), before)

    def test_partial_legacy_namespace_remains_unavailable(self):
        self.populated_index()
        (self.root / '.pending').rmdir()
        value, errors = monitor.evidence_metrics()
        self.assertEqual(value['legacy_status'], 'unavailable')
        self.assertNotIn('operation_entries', value)
        self.assertEqual(value['index_status'], 'verified')
        self.assertEqual(errors, ['evidence_legacy'])

    def test_corrupt_index_marks_missing_metric_without_changing_p0(self):
        self.populated_index()
        connection = sqlite3.connect(str(self.root / 'v2/index.sqlite3'))
        try:
            with connection:
                connection.execute("UPDATE meta SET checksum=?", ('f'*64,))
        finally:
            connection.close()
        before = self.inventory()
        with patch.object(storage, 'measure', return_value=fixture('runner')), \
             patch.object(storage, '_capacity', return_value=next(iter(fixture('runner').capacities.values()))), \
             patch.object(monitor, 'tree_allocated', return_value=1), \
             patch.object(monitor, 'docker_usage', return_value={}):
            value = monitor.collect(1000)
        self.assertEqual(value['status'], 'OK')
        self.assertEqual(value['reasons'], ['P0_SUFFICIENT'])
        evidence = value['totals']['evidence']
        self.assertEqual(evidence['operation_entries'], 2)
        self.assertEqual(evidence['index_status'], 'unavailable')
        self.assertNotIn('index_counters', evidence)
        self.assertIn('evidence_index', value['supplementary_errors'])
        self.assertEqual(self.inventory(), before)

    def test_existing_namespace_missing_database_is_not_recreated(self):
        (self.root / 'v2').mkdir(mode=0o700)
        value, errors = monitor.evidence_metrics()
        self.assertEqual(value['index_status'], 'unavailable')
        self.assertEqual(errors, ['evidence_index'])
        self.assertFalse((self.root / 'v2/index.sqlite3').exists())

    def test_legacy_namespace_over_limit_has_no_fabricated_zero(self):
        with patch.object(monitor, 'MAX_LEGACY_EVIDENCE_ENTRIES', 1):
            value, errors = monitor.evidence_metrics()
        self.assertEqual(value['legacy_status'], 'unavailable')
        self.assertNotIn('operation_entries', value)
        self.assertEqual(errors, ['evidence_legacy'])


class MonitorTests(unittest.TestCase):
    def test_p0_classification_ok(self):
        status, reason, rows = monitor.classify(fixture('runner'))
        self.assertEqual((status, reason), ('OK', 'P0_SUFFICIENT'))
        self.assertEqual({row['phase'] for row in rows}, {'runner', 'deploy-start'})

    def test_warning_uses_existing_p0_reserve(self):
        measured = fixture('runner')
        required = storage.evaluate(measured)[0].required_bytes
        caps = {k:replace(v, available_bytes=required+1) for k,v in measured.capacities.items()}
        status, reason, _ = monitor.classify(replace(measured, capacities=caps))
        self.assertEqual((status, reason), ('WARNING', 'P0_RESERVE_HEADROOM_LOW'))

    def test_critical_on_runner_only_rejection(self):
        measured = fixture('runner')
        required = storage.evaluate(measured)[0].required_bytes
        caps = {k:replace(v, available_bytes=required-1) for k,v in measured.capacities.items()}
        status, _, rows = monitor.classify(replace(measured, capacities=caps))
        self.assertEqual(status, 'CRITICAL')
        self.assertTrue(next(row for row in rows if row['phase']=='runner')['rejected'])
        self.assertIsNone(next(row for row in rows if row['phase']=='deploy-start')['rejected'])

    def test_inode_critical(self):
        status, _, rows = monitor.classify(fixture('runner', inodes=10))
        self.assertEqual(status, 'CRITICAL')
        self.assertTrue(all(row['rejected']=='INODES' for row in rows))

    def test_existing_current_image_never_weakens_monitor(self):
        a = monitor.classify(fixture('runner'))
        b = monitor.classify(replace(fixture('runner'), target_image_exists=True))
        self.assertEqual(a, b)

    def test_same_device_not_double_counted(self):
        rows = monitor.filesystem_rows(fixture('runner').capacities)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['total_bytes'], 45*storage.GIB)
        self.assertEqual(rows[0]['available_inodes'], 1000000)

    def test_unknown_p0_still_publishes_critical_and_capacity(self):
        with patch.object(storage, 'measure', side_effect=RuntimeError('private-value')), \
             patch.object(storage, '_capacity', return_value=next(iter(fixture('runner').capacities.values()))), \
             patch.object(monitor, 'tree_allocated', return_value=100), \
             patch.object(monitor, 'docker_usage', side_effect=ValueError('private-value')):
            value = monitor.collect(1000)
        self.assertEqual(value['status'], 'CRITICAL')
        self.assertTrue(value['filesystems'])
        self.assertIn('backups', value['totals'])
        self.assertNotIn('private-value', json.dumps(value))

    def test_sample_uses_runner_unknown_target(self):
        with patch.object(storage, 'measure', return_value=fixture('runner')) as measure, \
             patch.object(storage, '_capacity', return_value=next(iter(fixture('runner').capacities.values()))), \
             patch.object(monitor, 'tree_allocated', return_value=1), \
             patch.object(monitor, 'docker_usage', return_value={}):
            monitor.collect(1000)
        measure.assert_called_once_with('runner', None)

    def test_du_is_read_only_and_allocated_bytes(self):
        with patch.object(monitor, 'command', return_value='1234\t/fixed') as command:
            self.assertEqual(monitor.tree_allocated(Path('/fixed')), 1234)
        self.assertEqual(command.call_args.args[0], ['du', '-sx', '-B1', '--', str(Path('/fixed'))])

    def test_docker_report_is_labeled_estimate(self):
        raw = '\n'.join(json.dumps(dict(Type=kind, Size='1.5GB', TotalCount='2'))
                        for kind in ('Images','Containers','Local Volumes','Build Cache'))
        with patch.object(monitor, 'command', return_value=raw) as command:
            value = monitor.docker_usage()
        self.assertEqual(value['images']['logical_bytes_estimate'], 1500000000)
        self.assertEqual(command.call_args.args[0][:3], ['docker','system','df'])

    def test_atomic_report_and_no_history_deletion(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'storage-monitor.json'
            sentinel = Path(name) / 'old-evidence.json'
            sentinel.write_bytes(b'keep')
            monitor.publish(report(), path)
            monitor.publish(report('CRITICAL'), path)
            self.assertEqual(monitor.read_report(path)['status'], 'CRITICAL')
            self.assertEqual(sentinel.read_bytes(), b'keep')
            self.assertEqual(set(p.name for p in Path(name).iterdir()), {path.name, sentinel.name})

    def test_report_symlink_refused(self):
        with tempfile.TemporaryDirectory() as name:
            target, path = Path(name)/'target', Path(name)/'report'
            target.write_bytes(b'keep')
            try:
                path.symlink_to(target)
            except OSError:
                self.skipTest('host does not permit symlink creation')
            with self.assertRaises(ValueError):
                monitor.publish(report(), path)
            self.assertEqual(target.read_bytes(), b'keep')

    def test_nonproduction_runner_never_starts_worker(self):
        with patch.object(monitor, 'production_host', return_value=False), \
             patch.object(monitor.threading, 'Thread') as thread:
            stop = monitor.start_monitor(Path('/test'))
        self.assertFalse(stop.is_set())
        thread.assert_not_called()

    def test_worker_start_failure_is_isolated(self):
        with patch.object(monitor, 'production_host', return_value=True), \
             patch.object(monitor.threading, 'Thread', side_effect=OSError('private')):
            self.assertFalse(monitor.start_monitor(Path('/test')).is_set())

    @unittest.skipUnless(os.name == 'posix', 'POSIX diagnostic permission contract')
    def test_diagnostic_rollover_keeps_private_mode_despite_umask(self):
        with tempfile.TemporaryDirectory() as name:
            target = Path(name) / 'runner.log'
            original_umask = os.umask(0)
            try:
                handler = monitor.PrivateRotatingFileHandler(str(target), maxBytes=128, backupCount=2,
                                                             encoding='utf-8')
                try:
                    for number in range(5):
                        handler.emit(monitor.logging.LogRecord('fixture', monitor.logging.INFO, '', 0,
                                                               str(number) + 'x' * 100, (), None))
                    handler.flush()
                finally:
                    handler.close()
            finally:
                os.umask(original_umask)
            paths = list(Path(name).iterdir())
            self.assertEqual({path.name for path in paths}, {'runner.log', 'runner.log.1', 'runner.log.2'})
            self.assertTrue(all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in paths))

    def test_runner_wiring_does_not_change_admission(self):
        source = (ROOT/'scripts/ai_task_runner.py').read_text(encoding='utf8')
        self.assertIn('monitor_stop = start_monitor(config.repo_root)', source)
        self.assertIn('check_storage(phase="runner")', source)
        self.assertIn('monitor_stop.finish()', source)

    def test_final_publication_has_no_scan_and_no_wait(self):
        handle = monitor.MonitorHandle()
        handle.pending = report()
        with patch.object(monitor, 'publish_when_idle') as publish, patch.object(monitor, 'collect') as collect:
            handle.finish()
        self.assertTrue(handle.is_set())
        self.assertEqual(publish.call_args.kwargs['wait_seconds'], 0)
        collect.assert_not_called()

    def test_final_publication_failure_cannot_fail_runner(self):
        handle = monitor.MonitorHandle()
        handle.pending = report()
        with patch.object(monitor, 'publish_when_idle', side_effect=OSError('private')):
            handle.finish()
        self.assertTrue(handle.is_set())

    @unittest.skipUnless(sys.platform == 'linux', 'flock is Linux production API')
    def test_publication_refuses_busy_deploy_lock_without_modification(self):
        import fcntl
        with tempfile.TemporaryDirectory() as name:
            path = Path(name)/'deploy.lock'
            path.write_bytes(b'lock-sentinel')
            with path.open('rb') as owner, patch.object(monitor, 'DEPLOY_LOCK', path), \
                 patch.object(monitor, 'publish') as publish:
                fcntl.flock(owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assertFalse(monitor.publish_when_idle(report(), monitor.threading.Event(), wait_seconds=0))
                publish.assert_not_called()
                fcntl.flock(owner.fileno(), fcntl.LOCK_UN)
                self.assertTrue(monitor.publish_when_idle(report(), monitor.threading.Event(), wait_seconds=0))
                publish.assert_called_once()
            self.assertEqual(path.read_bytes(), b'lock-sentinel')

    @unittest.skipUnless(sys.platform == 'linux', 'flock is Linux production API')
    def test_cadence_prevents_repeated_sampling(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name)/'storage-monitor.json'
            with patch.object(monitor, 'REPORT', path), patch.object(monitor, 'LOCK', Path(name)/'lock'), \
                 patch.object(monitor, 'collect', return_value=report()) as collect, \
                 patch.object(monitor, 'publish_when_idle', side_effect=lambda value,stop: path.write_text(json.dumps(value))):
                monitor.sample_once(1000)
                monitor.sample_once(1100)
                self.assertEqual(collect.call_count, 1)
                monitor.sample_once(1300)
                self.assertEqual(collect.call_count, 2)


class FakeChannel:
    def __init__(self):
        self.id = notifier.config.AI_TASK_DISCORD_CHANNEL_ID
        self.guild = SimpleNamespace(id=notifier.config.AI_TASK_DISCORD_GUILD_ID)
        self.sent = []
        self.fail = False

    async def send(self, text, **kwargs):
        self.sent.append((text, kwargs))
        if self.fail:
            raise RuntimeError('synthetic-private-token')


class NotificationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path, self.state = Path(self.temp.name)/'report', Path(self.temp.name)/'state'
        self.path.write_text(json.dumps(report()))
        for name, value in (('REPORT', self.path), ('STATE', self.state), ('_MEMORY', {}), ('_LOCK', asyncio.Lock())):
            mocked = patch.object(notifier, name, value)
            mocked.start(); self.addCleanup(mocked.stop)
        mocked = patch.object(notifier.config, 'BOT_INSTANCE_ID', 'ichiyon')
        mocked.start(); self.addCleanup(mocked.stop)
        self.channel = FakeChannel()
        self.bot = SimpleNamespace(get_channel=lambda _:self.channel)

    async def test_existing_operations_channel_and_no_mentions(self):
        self.assertTrue(await notifier.notify_storage_once(self.bot, 1000))
        self.assertEqual(len(self.channel.sent), 1)
        mentions = self.channel.sent[0][1]['allowed_mentions']
        self.assertFalse(mentions.everyone)
        self.assertFalse(mentions.users)
        self.assertFalse(mentions.roles)

    async def test_persistent_cooldown_survives_memory_reset(self):
        await notifier.notify_storage_once(self.bot, 1000)
        notifier._MEMORY = {}
        self.assertFalse(await notifier.notify_storage_once(self.bot, 1100))
        self.assertEqual(len(self.channel.sent), 1)

    async def observe(self, status, now, available=None, required=None, reason=None):
        value = report(status, now)
        if available is not None:
            value['p0'][0]['available_bytes'] = available
        if required is not None:
            value['p0'][0]['required_bytes'] = required
        if reason is not None:
            value['reasons'] = [reason]
        self.path.write_text(json.dumps(value))
        return await notifier.notify_storage_once(self.bot, now)

    async def test_warning_transition_and_1h_23h_24h_reminder_after_restart(self):
        self.assertFalse(await self.observe('OK', 900))
        self.assertTrue(await self.observe('WARNING', 1000))
        for elapsed in (3600, 23*3600):
            notifier._MEMORY = {}
            self.assertFalse(await self.observe('WARNING', 1000+elapsed))
        notifier._MEMORY = {}
        self.assertTrue(await self.observe('WARNING', 1000+24*3600))
        self.assertFalse(await self.observe('WARNING', 1000+24*3600+1))
        self.assertEqual(len(self.channel.sent), 2)

    async def test_cumulative_worsening_and_required_growth(self):
        self.assertTrue(await self.observe('WARNING', 1000))
        self.assertFalse(await self.observe('WARNING', 1100, available=int(11.6*storage.GIB)))
        notifier._MEMORY = {}
        self.assertTrue(await self.observe('WARNING', 1200, available=11*storage.GIB))
        self.assertFalse(await self.observe('WARNING', 1250, available=11*storage.GIB))
        self.assertTrue(await self.observe('WARNING', 1300, available=11*storage.GIB, required=11*storage.GIB))

    async def test_reason_change_is_immediate_after_successful_delivery(self):
        self.assertTrue(await self.observe('CRITICAL', 1000))
        self.assertTrue(await self.observe('CRITICAL', 1001, reason='P0_MEASUREMENT_UNAVAILABLE'))

    async def test_critical_reminder_is_six_hours(self):
        self.assertTrue(await self.observe('WARNING', 1000))
        self.assertTrue(await self.observe('CRITICAL', 1001))
        for elapsed in (1, 3600, 6*3600-1):
            notifier._MEMORY = {}
            self.assertFalse(await self.observe('CRITICAL', 1001+elapsed))
        self.assertTrue(await self.observe('CRITICAL', 1001+6*3600))

    async def test_recovered_once_after_either_alert_then_warning_is_immediate(self):
        for status, start in (('WARNING', 1000), ('CRITICAL', 2000)):
            self.assertTrue(await self.observe(status, start))
            notifier._MEMORY = {}
            self.assertTrue(await self.observe('OK', start+1))
            self.assertTrue(self.channel.sent[-1][0].startswith('production storage RECOVERED\n'))
            notifier._MEMORY = {}
            self.assertFalse(await self.observe('OK', start+2))
            self.assertFalse(await self.observe('OK', start+100))

    async def test_failed_recovery_retries_and_keeps_last_success_across_restart(self):
        self.assertTrue(await self.observe('WARNING', 1000))
        self.channel.fail = True
        self.assertFalse(await self.observe('OK', 1010))
        self.assertEqual(json.loads(self.state.read_text())['status'], 'WARNING')
        notifier._MEMORY = {}
        self.channel.fail = False
        self.assertFalse(await self.observe('OK', 1100))
        self.assertTrue(await self.observe('OK', 1310))
        self.assertFalse(await self.observe('OK', 1311))

    async def test_failed_worsening_retries_against_successful_baseline(self):
        self.assertTrue(await self.observe('WARNING', 1000))
        self.channel.fail = True
        self.assertFalse(await self.observe('WARNING', 1010, available=11*storage.GIB))
        notifier._MEMORY = {}
        self.channel.fail = False
        self.assertFalse(await self.observe('WARNING', 1100, available=11*storage.GIB))
        self.assertTrue(await self.observe('WARNING', 1310, available=11*storage.GIB))

    async def test_legacy_hourly_state_does_not_repeat_on_upgrade(self):
        self.state.write_text(json.dumps(dict(status='WARNING', reason='P0_RESERVE_HEADROOM_LOW',
                                             attempted_at=1000, sent_at=1000)))
        self.assertFalse(await self.observe('WARNING', 4600))
        self.assertTrue(await self.observe('WARNING', 1000+24*3600))

    async def test_corrupt_state_is_not_first_startup_and_recovers_when_repaired(self):
        self.assertTrue(await self.observe('WARNING', 1000))
        saved = self.state.read_text()
        for corrupt in ('{', '[]', 'null', '{"sent_at": "broken"}',
                        '{"capacity": {"runner:releases": null}}'):
            self.state.write_text(corrupt)
            notifier._MEMORY = {}
            self.assertFalse(await self.observe('WARNING', 4600))
            self.assertEqual(self.state.read_text(), corrupt)
        self.assertEqual(len(self.channel.sent), 1)
        self.state.write_text(saved)
        self.assertFalse(await self.observe('WARNING', 4600))
        self.assertTrue(await self.observe('WARNING', 1000+24*3600))

    async def test_readable_warning_header_and_no_automatic_cleanup_claim(self):
        self.assertTrue(await self.observe('WARNING', 1000))
        message = self.channel.sent[-1][0]
        self.assertEqual(message.splitlines()[:4], ['production storage WARNING',
            '空き: 12.00 GiB', 'Runner安全必要量: 10.00 GiB', '残余裕: 2.00 GiB'])
        self.assertIn('自動cleanupは実行していません', message)

    def test_deterioration_compares_same_filesystem_and_phase_only(self):
        current = notifier.validated_report(report(), 1000)
        previous = dict(capacity=notifier.capacity_baseline(current))
        current['p0'][0]['filesystem'] = 'docker+releases'
        self.assertFalse(notifier.deteriorated(current, previous))
        current['p0'][0]['available_bytes'] -= storage.GIB
        self.assertTrue(notifier.deteriorated(current, previous))
        current['p0'][0]['phase'] = 'deploy-start'
        self.assertFalse(notifier.deteriorated(current, previous))

    @unittest.skipUnless(os.name == 'posix', 'POSIX host backup permission contract')
    async def test_root_written_numeric_state_is_host_backup_readable(self):
        self.assertTrue(await notifier.notify_storage_once(self.bot, 1000))
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o644)

    @unittest.skipUnless(os.name == 'posix', 'POSIX host backup permission contract')
    def test_pending_state_is_readable_before_publication_failure(self):
        def fail_publish(source, target):
            self.assertEqual(stat.S_IMODE(Path(source).stat().st_mode), 0o644)
            raise OSError('simulated publication failure')
        with patch.object(notifier.os, 'replace', side_effect=fail_publish):
            with self.assertRaises(OSError):
                notifier._save(dict(attempted_at=1000))
        self.assertEqual(list(self.state.parent.glob('.storage-notification-*')), [])

    async def test_critical_escalates_after_warning(self):
        await notifier.notify_storage_once(self.bot, 1000)
        self.path.write_text(json.dumps(report('CRITICAL', 1010)))
        self.assertTrue(await notifier.notify_storage_once(self.bot, 1010))
        self.assertEqual(len(self.channel.sent), 2)

    async def test_failure_is_caught_and_retry_is_bounded(self):
        self.channel.fail = True
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertFalse(await notifier.notify_storage_once(self.bot, 1000))
            self.assertFalse(await notifier.notify_storage_once(self.bot, 1010))
        self.assertNotIn('synthetic-private-token', output.getvalue())
        self.assertEqual(len(self.channel.sent), 1)

    async def test_state_write_failure_prevents_send(self):
        with patch.object(notifier, '_save', side_effect=OSError('private')):
            self.assertFalse(await notifier.notify_storage_once(self.bot, 1000))
        self.assertEqual(self.channel.sent, [])

    async def test_ok_does_not_notify(self):
        self.path.write_text(json.dumps(report('OK')))
        self.assertFalse(await notifier.notify_storage_once(self.bot, 1000))

    async def test_stale_report_is_critical_without_changing_p0(self):
        self.assertTrue(await notifier.notify_storage_once(self.bot, 2300))
        self.assertIn('MONITOR_REPORT_STALE', self.channel.sent[0][0])

    async def test_absent_report_has_startup_grace_then_warns(self):
        self.path.unlink()
        with patch.object(notifier, '_STARTED_AT', 0):
            self.assertFalse(await notifier.notify_storage_once(self.bot, 1000))
            self.assertTrue(await notifier.notify_storage_once(self.bot, 2300))
        self.assertIn('P0_MEASUREMENT_UNAVAILABLE', self.channel.sent[0][0])

    async def test_wrong_channel_never_sends(self):
        self.channel.guild.id = 0
        self.assertFalse(await notifier.notify_storage_once(self.bot, 1000))

    async def test_irsia_never_sends(self):
        with patch.object(notifier.config, 'BOT_INSTANCE_ID', 'irsia'):
            self.assertFalse(await notifier.notify_storage_once(self.bot, 1000))

    async def test_malicious_report_text_rejected(self):
        value = report()
        value['p0'][0]['filesystem'] = '@everyone secret'
        self.path.write_text(json.dumps(value))
        self.assertFalse(await notifier.notify_storage_once(self.bot, 1000))
        self.assertEqual(self.channel.sent, [])

    def test_no_cleanup_or_db_interface(self):
        code = (ROOT/'bot/services/storage_monitor.py').read_text(encoding='utf8')
        for forbidden in ('get_connection(', 'shutil.rmtree', 'subprocess', 'prune'):
            self.assertNotIn(forbidden, code)
        source = (ROOT/'main.py').read_text(encoding='utf8')
        self.assertIn('storage_notification_task.start()', source)


if __name__ == '__main__':
    unittest.main()
