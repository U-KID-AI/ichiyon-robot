#!/usr/bin/env python3
"""Read-only collection on the BDS host, or via the runner's configured BDS SSH."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import subprocess


def command(argv, remote):
    if remote:
        prefix = 'AI_TASK_RUNNER_BDS_'
        argv = [os.environ[prefix + 'SSH_PATH'], '-i', os.environ[prefix + 'SSH_KEY_PATH'],
                '-o', 'UserKnownHostsFile=' + os.environ[prefix + 'KNOWN_HOSTS_PATH'],
                '-o', 'StrictHostKeyChecking=yes', '-o', 'BatchMode=yes',
                '-o', 'ConnectTimeout=10', '-o', 'ForwardAgent=no',
                os.environ[prefix + 'SSH_USER'] + '@' + os.environ[prefix + 'SSH_HOST'],
                shlex.join(argv)]
    return subprocess.run(argv, capture_output=True, timeout=30)


def collect(directory, remote=False):
    directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    metadata = {'collected_utc': datetime.now(timezone.utc).isoformat(), 'containers': {}}
    for name in ('mcxboxbroadcast', 'minecraft-bedrock-creative'):
        result = command(['docker', 'inspect', name], remote)
        if result.returncode:
            raise RuntimeError('container inspection failed; no raw output published')
        item = json.loads(result.stdout)[0]
        metadata['containers'][name] = {
            'image_id': item['Image'], 'started_at': item['State']['StartedAt'],
            'state': item['State']['Status'], 'restart_count': item['RestartCount'],
            'health': item['State'].get('Health', {}).get('Status'),
            'network_mode': item['HostConfig']['NetworkMode'],
        }
    count = 0
    for index in range(4):
        result = command(['docker', 'exec', 'mcxboxbroadcast', 'cat',
                          f'/opt/app/config/nethernet-diagnostics/events-{index}.jsonl'], remote)
        if result.returncode:
            continue
        if len(result.stdout) > 3 * 1024 * 1024:
            raise RuntimeError('unexpected diagnostic log size')
        path = directory / f'events-{index}.jsonl'
        with path.open('xb') as stream:
            os.chmod(path, 0o600)
            stream.write(result.stdout)
        count += 1
    metadata['log_files'] = count
    path = directory / 'metadata.json'
    with path.open('x', encoding='utf-8') as stream:
        os.chmod(path, 0o600)
        json.dump(metadata, stream, indent=2)
    if not count:
        raise RuntimeError('no diagnostic events: overlay not enabled, expired/removed, or unreadable')
    return count


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='new private directory')
    parser.add_argument('--ssh-from-runner-env', action='store_true')
    args = parser.parse_args()
    try:
        count = collect(args.output, args.ssh_from_runner_env)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired):
        raise SystemExit('Collection incomplete. Check SSH, container state and diagnostic activation; retained files are preserved.')
    print(f'Collected {count} bounded diagnostic files and container metadata.')
