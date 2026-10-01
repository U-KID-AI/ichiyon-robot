"""Notify the existing operations channel from the host's numeric report only."""
import asyncio
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import stat
import tempfile
import time

import discord
from bot import config

REPORT = Path('/app/data/storage-monitor.json')
STATE = Path('/app/data/storage-monitor-notification.json')
WARNING_REMINDER_SECONDS = 24 * 3600
CRITICAL_REMINDER_SECONDS = 6 * 3600
DETERIORATION_BYTES = 1024**3
RETRY_SECONDS = 300
STALE_SECONDS = 1200
MAX_BYTES = 65536
STATES = ('OK', 'WARNING', 'CRITICAL')
REASONS = ('P0_SUFFICIENT', 'P0_RESERVE_HEADROOM_LOW', 'P0_REJECTED', 'P0_MEASUREMENT_UNAVAILABLE',
           'MONITOR_REPORT_STALE')
_LOCK = asyncio.Lock()
_MEMORY = {}  # Additional protection when a filesystem write itself fails.
_STARTED_AT = time.time()


def _directory(path):
    path = Path(path).absolute()
    for part in (path,) + tuple(path.parents):
        if part.is_symlink() or not stat.S_ISDIR(part.lstat().st_mode):
            raise ValueError('notification_directory_invalid')
    if path.resolve(strict=True) != path:
        raise ValueError('notification_directory_alias')


def _read(path):
    _directory(path.parent)
    value = path.lstat()
    if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1 or value.st_size > MAX_BYTES:
        raise ValueError('notification_file_invalid')
    with os.fdopen(os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)), 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if (value.st_dev, value.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError('notification_file_changed')
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('notification_file_too_large')
    return json.loads(data)


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value < 10**20


def validated_report(value, now):
    if (not isinstance(value, dict) or type(value.get('schema_version')) is not int or value.get('schema_version') != 1 or
            value.get('status') not in STATES or type(value.get('sampled_at')) is not int or
            not 0 <= value['sampled_at'] <= now+60 or not isinstance(value.get('reasons'), list) or
            not value['reasons'] or any(reason not in REASONS for reason in value['reasons'])):
        raise ValueError('monitor_report_invalid')
    rows = value.get('p0')
    if not isinstance(rows, list) or len(rows) > 16:
        raise ValueError('monitor_report_invalid')
    safe = []
    categories = {'releases','backups','shared','docker','temp','control','source','worktrees'}
    for row in rows:
        if (not isinstance(row, dict) or row.get('phase') not in ('runner', 'deploy-start') or
                not isinstance(row.get('filesystem'), str) or
                not set(row['filesystem'].split('+')) <= categories or
                not all(type(row.get(k)) is int and _number(row[k]) for k in
                    ('available_bytes','required_bytes','available_inodes','required_inodes'))):
            raise ValueError('monitor_report_invalid')
        safe.append({key:row[key] for key in ('phase','filesystem','available_bytes','required_bytes',
                                              'available_inodes','required_inodes')})
    stale = now-value['sampled_at'] > STALE_SECONDS
    return dict(status='CRITICAL' if stale else value['status'], sampled_at=value['sampled_at'],
                reason='MONITOR_REPORT_STALE' if stale else value['reasons'][0], p0=safe)


def should_notify(report, previous, now):
    if not isinstance(previous, dict):
        previous = {}
    attempt, sent = previous.get('attempted_at'), previous.get('sent_at')
    # Preserve the last *successful* notification across failed sends. Retrying
    # an uncertain transport is bounded even if the container restarts.
    if _number(attempt) and attempt != sent and now-attempt < RETRY_SECONDS:
        return False
    if report['status'] == 'OK':
        return _number(sent) and previous.get('status') in ('WARNING', 'CRITICAL')
    same = previous.get('status') == report['status'] and previous.get('reason') == report['reason']
    interval = WARNING_REMINDER_SECONDS if report['status'] == 'WARNING' else CRITICAL_REMINDER_SECONDS
    return not (same and _number(sent) and now-sent < interval
                and not deteriorated(report, previous))


def capacity_baseline(report):
    return {row['phase'] + ':' + '+'.join(sorted(row['filesystem'].split('+'))):
            dict(available=row['available_bytes'], headroom=row['available_bytes']-row['required_bytes'])
            for row in report['p0']}


def deteriorated(report, previous):
    """A cumulative >=1 GiB loss since the last delivery warrants a new alert.

    Compare each filesystem/phase separately, including an increase in required
    capacity. Ordinary five-minute sampling jitter never resets the baseline.
    """
    old = previous.get('capacity', {})
    return any(key in old and any(old[key][metric]-value[metric] >= DETERIORATION_BYTES
               for metric in ('available', 'headroom'))
               for key, value in capacity_baseline(report).items())


def validated_state(value):
    """Accept the deployed v1 state as well as the new delivery baseline.

    A corrupt state is not treated as first startup: that would repeat alerts on
    every poll/restart. It fails closed with a diagnostic until repaired.
    """
    if not isinstance(value, dict):
        raise ValueError('notification_state_invalid')
    for key in ('attempted_at', 'sent_at'):
        if value.get(key) is not None and not _number(value[key]):
            raise ValueError('notification_state_invalid')
    if value.get('status') is not None and value['status'] not in STATES:
        raise ValueError('notification_state_invalid')
    if value.get('reason') is not None and value['reason'] not in REASONS:
        raise ValueError('notification_state_invalid')
    if _number(value.get('sent_at')) and (value.get('status') not in STATES or value.get('reason') not in REASONS):
        raise ValueError('notification_state_invalid')
    capacity = value.get('capacity', {})
    if not isinstance(capacity, dict) or len(capacity) > 16:
        raise ValueError('notification_state_invalid')
    for key, row in capacity.items():
        if (not isinstance(key, str) or not isinstance(row, dict)
                or set(row) != {'available', 'headroom'} or not _number(row['available'])
                or type(row['headroom']) not in (int, float)
                or not math.isfinite(row['headroom']) or abs(row['headroom']) >= 10**20):
            raise ValueError('notification_state_invalid')
    return value


def _save(value):
    _directory(STATE.parent)
    if STATE.exists() or STATE.is_symlink():
        info = STATE.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('notification_state_invalid')
    raw = json.dumps(value, sort_keys=True).encode('ascii')
    descriptor, name = tempfile.mkstemp(prefix='.storage-notification-', dir=str(STATE.parent))
    try:
        # Containers run as root, while the host's full persistence tar runs as
        # ubuntu. These numeric-only state bytes must remain backup-readable,
        # including a private pending file left by an interrupted process.
        with os.fdopen(descriptor, 'wb') as stream:
            os.chmod(name, 0o644)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, str(STATE))
        if os.name == 'posix':
            descriptor = os.open(str(STATE.parent), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        if os.path.exists(name):
            os.unlink(name)  # Only this attempt's private unpublished file.


def format_warning(report):
    lines = ['production storage ' + ('RECOVERED' if report['status'] == 'OK' else report['status'])]
    runner = [row for row in report['p0'] if row['phase'] == 'runner']
    if runner:
        tightest = min(runner, key=lambda row: row['available_bytes']-row['required_bytes'])
        lines.extend(['空き: {:.2f} GiB'.format(tightest['available_bytes']/1024**3),
                      'Runner安全必要量: {:.2f} GiB'.format(tightest['required_bytes']/1024**3),
                      '残余裕: {:.2f} GiB'.format((tightest['available_bytes']-tightest['required_bytes'])/1024**3)])
    lines.append('reason: ' + report['reason'])
    if report.get('sampled_at') is not None:
        lines.append('observed: ' + datetime.fromtimestamp(report['sampled_at'], timezone.utc).isoformat())
    for row in report['p0']:
        lines.append('{0} [{1}] available {2:.2f} GiB / required {3:.2f} GiB; inode {4} / {5}'.format(
            row['phase'], row['filesystem'], row['available_bytes']/1024**3,
            row['required_bytes']/1024**3, row['available_inodes'], row['required_inodes']))
    lines.append('P0容量判定の観測です。自動cleanupは実行していません。')
    return '\n'.join(lines)[:1900]


async def notify_storage_once(bot, now=None):
    """All transport, malformed-file and filesystem errors stay inside this task."""
    global _MEMORY
    if config.BOT_INSTANCE_ID != 'ichiyon':
        return False
    async with _LOCK:
        try:
            current = int(time.time() if now is None else now)
            channel = bot.get_channel(config.AI_TASK_DISCORD_CHANNEL_ID)
            if (channel is None or channel.id != config.AI_TASK_DISCORD_CHANNEL_ID or
                    getattr(getattr(channel, 'guild', None), 'id', None) != config.AI_TASK_DISCORD_GUILD_ID):
                return False
            try:
                report = validated_report(_read(REPORT), current)
            except FileNotFoundError:
                if current - _STARTED_AT < STALE_SECONDS:
                    return False
                report = dict(status='CRITICAL', reason='P0_MEASUREMENT_UNAVAILABLE', p0=[], sampled_at=None)
            try:
                previous = validated_state(_read(STATE))
            except FileNotFoundError:
                previous = {}
            if _MEMORY.get('attempted_at', -1) > previous.get('attempted_at', -1):
                previous = _MEMORY
            if not should_notify(report, previous, current):
                return False
            # Persist the attempt before transport. A failure has a retry delay
            # even across container restarts, and uncertain sends are not spammed.
            pending = dict(previous, attempted_at=current)
            _MEMORY = pending
            _save(pending)
            await channel.send(format_warning(report), allowed_mentions=discord.AllowedMentions.none())
            complete = dict(pending, status=report['status'], reason=report['reason'],
                            sent_at=current, capacity=capacity_baseline(report))
            _MEMORY = complete
            _save(complete)
            return True
        except Exception as exc:
            print('[WARN] Storage notification unavailable: ' + type(exc).__name__)
            return False
