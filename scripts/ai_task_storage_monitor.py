"""Bounded production storage observation; never admission, cleanup or DB writes.

The Runner invokes this on its production host only. Numeric report publication
is the only persistent effect; P0 remains the sole admission decision maker.
"""
from dataclasses import asdict, replace
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time

import ai_task_storage as storage

REPORT = storage.SHARED / 'data/storage-monitor.json'
LOCK = Path('/home/ubuntu/ichiyon-storage-monitor.lock')
DEPLOY_LOCK = Path('/home/ubuntu/ichiyon-deploy.lock')
EVIDENCE = Path('/home/ubuntu/ichiyon-storage-evidence')
DIAGNOSTICS = Path('/home/ubuntu/ichiyon-runner-diagnostics')
INTERVAL = 300
MAX_REPORT_BYTES = 65536
logger = logging.getLogger('ai_task_runner.storage')


def own_file(info):
    return not hasattr(os, 'getuid') or info.st_uid == os.getuid()


def normal_directory(path):
    path = Path(path).absolute()
    for item in (path,) + tuple(path.parents):
        if not stat.S_ISDIR(item.lstat().st_mode) or item.is_symlink():
            raise ValueError('monitor_directory_invalid')
    if path.resolve(strict=True) != path:
        raise ValueError('monitor_directory_alias')
    return path


def read_report(path):
    path = Path(path)
    normal_directory(path.parent)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_REPORT_BYTES:
        raise ValueError('monitor_report_invalid')
    with os.fdopen(os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)), 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError('monitor_report_changed')
        raw = stream.read(MAX_REPORT_BYTES + 1)
    if len(raw) > MAX_REPORT_BYTES:
        raise ValueError('monitor_report_too_large')
    return json.loads(raw)


def publish(report, path=REPORT):
    """Replace one numeric report, never a history, backup or existing log."""
    path = Path(path)
    normal_directory(path.parent)
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not own_file(info):
            raise ValueError('monitor_report_owner_invalid')
    raw = json.dumps(report, sort_keys=True, allow_nan=False).encode('ascii')
    if len(raw) > MAX_REPORT_BYTES:
        raise ValueError('monitor_report_too_large')
    fd, name = tempfile.mkstemp(prefix='.storage-monitor-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o644)  # Numeric metadata must be readable by the app bind mount.
        os.replace(name, str(path))
    finally:
        if os.path.exists(name):
            os.unlink(name)  # Only our unpublished private temporary file.


def classify(measured):
    """Use P0 once per phase. WARNING adds only the same P0 safety reserve."""
    measured = replace(measured, phase='runner', target_image_exists=False)
    deployed_capacities = {key: value for key, value in measured.capacities.items()
                           if key not in ('source', 'worktrees')}
    phases = (measured, replace(measured, phase='deploy-start', capacities=deployed_capacities))
    rows, critical, warning = [], False, False
    for phase in phases:
        try:
            records = storage.evaluate(phase)
            rejected = None
        except storage.StorageError as exc:
            records, rejected = exc.records, exc.category
            critical = True
        for record in records:
            categories = record.filesystem.split('+')
            capacities = [phase.capacities[key] for key in categories]
            reserve = max(storage.SAFETY_BYTES, storage._ceil_div(
                max(c.total_bytes for c in capacities) * storage.SAFETY_PERCENT, 100))
            inode_reserve = max(storage.SAFETY_INODES, storage._ceil_div(
                max(c.total_inodes for c in capacities) * storage.SAFETY_INODE_PERCENT, 100))
            if (record.available_bytes - record.required_bytes < reserve or
                    record.available_inodes - record.required_inodes < inode_reserve):
                warning = True
            rows.append(dict(asdict(record), rejected=rejected))
    state = 'CRITICAL' if critical else 'WARNING' if warning else 'OK'
    reason = 'P0_REJECTED' if critical else 'P0_RESERVE_HEADROOM_LOW' if warning else 'P0_SUFFICIENT'
    return state, reason, rows


def filesystem_rows(capacities):
    groups = {}
    for name, value in capacities.items():
        groups.setdefault(value.device, []).append((name, value))
    rows = []
    for device, group in sorted(groups.items()):
        total = max(cap.total_bytes for _, cap in group)
        available = min(cap.available_bytes for _, cap in group)
        rows.append(dict(device=device, categories=sorted(name for name, _ in group),
            total_bytes=total, available_bytes=available,
            unavailable_bytes=total-available, usage_percent=round(100 * (total-available) / total, 4),
            total_inodes=max(cap.total_inodes for _, cap in group),
            available_inodes=min(cap.available_inodes for _, cap in group)))
    return rows


def command(argv):
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=30, check=False)
    if result.returncode or len(result.stdout) > MAX_REPORT_BYTES:
        raise ValueError('monitor_probe_unavailable')
    return result.stdout.decode('ascii').strip()


def tree_allocated(path):
    # du -x does not follow links or descend another filesystem. This is an
    # allocated-byte metric, unlike P0's conservative source/tar upper bound.
    output = command(['du', '-sx', '-B1', '--', str(path)])
    size = output.split('\t', 1)[0]
    if not re.fullmatch(r'[0-9]{1,20}', size):
        raise ValueError('monitor_tree_size_invalid')
    return int(size)


def docker_usage():
    # Docker prints rounded decimal units; these are labeled estimates and never
    # summed with filesystem allocation or copied to reclaimable capacity.
    kinds = {'Images': 'images', 'Containers': 'containers', 'Local Volumes': 'volumes',
             'Build Cache': 'build_cache'}
    rows = {}
    for line in command(['docker', 'system', 'df', '--format', '{{json .}}']).splitlines():
        value = json.loads(line)
        kind = kinds.get(value.get('Type'))
        size = re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)\s*(B|kB|KB|MB|GB|TB)', str(value.get('Size', '')))
        count = str(value.get('TotalCount', ''))
        if kind is None or kind in rows or size is None or not count.isdigit():
            raise ValueError('monitor_docker_size_invalid')
        scale = {'B':1, 'kB':1000, 'KB':1000, 'MB':1000**2, 'GB':1000**3, 'TB':1000**4}[size[2]]
        rows[kind] = dict(logical_bytes_estimate=int(float(size[1]) * scale), count=int(count))
    if set(rows) != set(kinds.values()):
        raise ValueError('monitor_docker_inventory_incomplete')
    return rows


def collect(now=None):
    sampled_at = int(time.time() if now is None else now)
    report = dict(schema_version=1, sampled_at=sampled_at, status='CRITICAL',
        reasons=['P0_MEASUREMENT_UNAVAILABLE'], p0=[], filesystems=[], totals={},
        supplementary_errors=[], filesystem_usage_semantics='total_minus_available_includes_reserved_blocks',
        docker_usage_semantics='rounded_logical_estimate_not_physical_or_reclaimable')
    try:
        measured = storage.measure('runner', None)
        report['status'], reason, report['p0'] = classify(measured)
        report['reasons'] = [reason]
        capacities = dict(measured.capacities)
        capacities['root'] = storage._capacity(Path('/'))
        report['filesystems'] = filesystem_rows(capacities)
    except Exception:
        # Even failed P0 probes leave useful root/relevant filesystem visibility.
        roots = {'root':Path('/'), 'releases':storage.RELEASES, 'backups':storage.BACKUPS,
                 'shared':storage.SHARED, 'worktrees':storage.RUNNER_WORKTREES, 'temp':storage.TEMP}
        available = {}
        for name, path in roots.items():
            try:
                available[name] = storage._capacity(path)
            except Exception:
                report['supplementary_errors'].append(name)
        report['filesystems'] = filesystem_rows(available)
    roots = {'backups':storage.BACKUPS, 'releases':storage.RELEASES, 'shared':storage.SHARED,
             'worktrees':storage.RUNNER_WORKTREES, 'temp':storage.TEMP, 'evidence':EVIDENCE}
    for name, path in roots.items():
        try:
            report['totals'][name] = dict(allocated_bytes=tree_allocated(path))
            if name == 'evidence':
                normal_directory(path)
                report['totals'][name]['operation_entries'] = len(list((path / 'operations').iterdir()))
        except Exception:
            report['supplementary_errors'].append(name)
    try:
        report['docker'] = docker_usage()
    except Exception:
        report['supplementary_errors'].append('docker')
    report['supplementary_errors'] = sorted(set(report['supplementary_errors']))
    # Missing supplemental du/Docker metrics never turn an unknown P0 into OK,
    # and are not invented capacity thresholds.
    return report


def publish_when_idle(report, stop, wait_seconds=120):
    """Avoid shared persistence writes while deployment owns its existing lock.

    P0/du reads happen before this; the shared flock covers only atomic report
    publication. We never create, truncate or delete the deployment lock.
    """
    import fcntl
    deadline, first = time.monotonic() + wait_seconds, True
    while not stop.is_set() and (first or time.monotonic() < deadline):
        first = False
        normal_directory(DEPLOY_LOCK.parent)
        descriptor = os.open(str(DEPLOY_LOCK), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        try:
            observed = os.fstat(descriptor)
            if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1 or not own_file(observed):
                raise ValueError('deployment_lock_invalid')
            try:
                fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                current = DEPLOY_LOCK.lstat()
                if (current.st_dev, current.st_ino, current.st_mode) != (
                        observed.st_dev, observed.st_ino, observed.st_mode):
                    raise ValueError('deployment_lock_changed')
                publish(report)
                return True
        finally:
            os.close(descriptor)
        if wait_seconds <= 0:
            return False
        stop.wait(0.5)
    return False


def sample_once(now=None, stop=None):
    import fcntl  # Production host is Linux; imported only during the sample.
    normal_directory(LOCK.parent)
    fd = os.open(str(LOCK), os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not own_file(info):
            raise ValueError('monitor_lock_invalid')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return None
        current = int(time.time() if now is None else now)
        try:
            previous = read_report(REPORT)
            captured = previous.get('sampled_at')
            if (type(captured) is int and 0 <= current-captured < INTERVAL and
                    previous.get('schema_version') == 1):
                return None
        except (OSError, ValueError, TypeError):
            pass
        result = collect(current)
        if isinstance(stop, MonitorHandle):
            stop.pending = result
        if publish_when_idle(result, stop or threading.Event()):
            if isinstance(stop, MonitorHandle):
                stop.pending = None
            return result
        return None
    finally:
        os.close(fd)


def production_host(repo_root):
    return sys.platform == 'linux' and Path(repo_root).resolve() == storage.RUNNER_SOURCE.resolve()


class MonitorHandle(threading.Event):
    def __init__(self):
        super().__init__()
        self.pending, self.worker = None, None

    def finish(self):
        # Let a completed pending sample publish after the child deployment has
        # released its lock. No measurement or sleep/retry occurs on this path.
        try:
            self.set()
            if self.worker is not None and self.worker.is_alive():
                self.worker.join(timeout=1)
            if self.pending is not None:
                publish_when_idle(self.pending, threading.Event(), wait_seconds=0)
        except Exception as exc:
            logger.warning('Storage final publication unavailable: %s', type(exc).__name__)


class BoundedDiagnosticFormatter(logging.Formatter):
    def format(self, record):
        value = super().format(record)
        return value if len(value) <= 32768 else value[:32768] + ' [diagnostic truncated]'


class PrivateRotatingFileHandler(RotatingFileHandler):
    def _open(self):
        # FileHandler reopens runner.log after every rotation. Enforce the
        # private mode on each opened descriptor, independent of the umask.
        stream = super()._open()
        try:
            if hasattr(os, 'fchmod'):
                os.fchmod(stream.fileno(), 0o600)
            else:
                os.chmod(self.baseFilename, 0o600)
        except Exception:
            stream.close()
            raise
        return stream


def start_monitor(repo_root):
    """Return a stop event; observation/notification failure cannot fail Runner."""
    stop = MonitorHandle()
    try:
        if not production_host(repo_root):
            return stop
        def loop():
            while not stop.is_set():
                try:
                    sample_once(stop=stop)
                except Exception as exc:
                    logger.warning('Storage monitor unavailable: %s', type(exc).__name__)
                stop.wait(INTERVAL)
        stop.worker = threading.Thread(target=loop, name='storage-monitor', daemon=True)
        stop.worker.start()
    except Exception as exc:
        logger.warning('Storage monitor startup unavailable: %s', type(exc).__name__)
    return stop


def configure_diagnostics(repo_root):
    """Bound a new diagnostic stream without removing old journal/evidence."""
    try:
        if not production_host(repo_root):
            return
        normal_directory(DIAGNOSTICS.parent)
        DIAGNOSTICS.mkdir(mode=0o700, exist_ok=True)
        normal_directory(DIAGNOSTICS)
        root = DIAGNOSTICS.lstat()
        if not own_file(root) or stat.S_IMODE(root.st_mode) != 0o700:
            raise ValueError('diagnostic_directory_owner_invalid')
        target = DIAGNOSTICS / 'runner.log'
        for path in (target,) + tuple(DIAGNOSTICS / ('runner.log.' + str(n)) for n in range(1, 6)):
            if path.exists() or path.is_symlink():
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not own_file(info):
                    raise ValueError('diagnostic_file_invalid')
        handler = PrivateRotatingFileHandler(str(target), maxBytes=10 * storage.MIB, backupCount=5,
                                             encoding='utf-8')
        handler.setFormatter(BoundedDiagnosticFormatter('%(asctime)s %(levelname)s %(message)s'))
        logging.getLogger('ai_task_runner').addHandler(handler)
    except Exception as exc:
        logger.warning('Runner diagnostic file unavailable: %s', type(exc).__name__)
