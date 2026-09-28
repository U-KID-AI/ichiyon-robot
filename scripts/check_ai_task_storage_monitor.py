"""Offline monitor + Discord fault injection; no production/network/DB access."""
import asyncio
from contextlib import redirect_stdout
from dataclasses import replace
import importlib
import io
import json
import os
import stat
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import ai_task_storage as storage
import ai_task_storage_monitor as monitor
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
