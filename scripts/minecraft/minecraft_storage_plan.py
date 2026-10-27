"""Read-only Minecraft retention plan. There is deliberately no apply/delete mode.

Candidates are review proposals, never permission to delete. Live worlds, private
inputs, backups and the content-addressed store are outside the candidate scope.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time

from minecraft_storage import check_capacity

FAMILIES = ('cosmetics-applications', 'junket-world-applications', 'junket-isolated-resilience')
UUID = re.compile(r'(?<![0-9a-f])[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}(?![0-9a-f])')
TERMINAL = {'succeeded', 'failed', 'recovery_failed'}


def plain(path):
    path = Path(path).absolute()
    for item in (path, *path.parents):
        if (item.is_symlink() or (hasattr(item, 'is_junction') and item.is_junction())
                or (item.exists() and getattr(item.lstat(), 'st_file_attributes', 0) & 0x400)):
            raise ValueError('linked path: ' + str(item))
    return path


def read_json(path):
    path = plain(path)
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('oversized retention metadata: ' + str(path))
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(value, (dict, list)):
        raise ValueError('invalid retention metadata: ' + str(path))
    return value


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from strings(child)


def tree_usage(root):
    """No content hashing, link following, or world DB opening."""
    root = plain(root)
    if not root.is_dir():
        raise ValueError('expected operation directory')
    def unreadable(error):
        raise error
    logical = allocated = files = outside_links = 0
    physical_known = os.name == 'posix'
    seen = set()
    for parent, directories, names in os.walk(root, followlinks=False, onerror=unreadable):
        for name in directories + names:
            item = plain(Path(parent) / name)
            info = item.lstat()
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise ValueError('special entry: ' + str(item))
            if stat.S_ISDIR(info.st_mode):
                continue
            identity = (info.st_dev, info.st_ino)
            if identity in seen:
                continue
            seen.add(identity)
            files += 1
            logical += info.st_size
            allocated += getattr(info, 'st_blocks', 0) * 512
            if info.st_nlink != 1:
                outside_links += 1
    return {'files': files, 'logical_bytes': logical,
            'allocated_bytes': allocated if physical_known else None,
            'multiple_link_files': outside_links,
            'estimated_reclaimable_bytes': allocated if physical_known and not outside_links else 0}


def collect_usage(project):
    """Missing process or Docker inventory inhibits all candidate eligibility."""
    project = str(plain(project))
    references, issues = set(), []
    if os.name != 'posix' or not Path('/proc').is_dir():
        return {'complete': False, 'paths': [], 'issues': ['process_inventory_unavailable_on_this_platform']}
    try:
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            mount = line.split()[4].replace('\\040', ' ').replace('\\011', '\t').replace('\\134', '\\')
            if mount == project or mount.startswith(project + '/'):
                references.add(mount)
    except (OSError, IndexError):
        issues.append('mount_inventory_unavailable')
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():
            continue
        try:
            paths = []
            for entry in ('cwd', 'exe'):
                try:
                    paths.append(os.readlink(process / entry))
                except FileNotFoundError:
                    pass
            for descriptor in (process / 'fd').iterdir():
                try:
                    paths.append(os.readlink(descriptor))
                except FileNotFoundError:
                    pass
            for line in (process / 'maps').read_text().splitlines():
                fields = line.split(None, 5)
                if len(fields) == 6:
                    paths.append(fields[5].removesuffix(' (deleted)'))
            references.update(p for p in paths if p == project or p.startswith(project + '/'))
        except (FileNotFoundError, ProcessLookupError):
            continue
        except (PermissionError, OSError):
            issues.append('process_inventory_incomplete:' + process.name)
    try:
        ids = subprocess.run(['docker', 'ps', '-aq'], check=True, capture_output=True,
                             text=True, timeout=20).stdout.split()
        if ids:
            containers = json.loads(subprocess.run(['docker', 'inspect', *ids], check=True,
                                    capture_output=True, text=True, timeout=30).stdout)
            for container in containers:
                for mount in container.get('Mounts', []):
                    source = mount.get('Source', '')
                    if source == project or source.startswith(project + '/'):
                        references.add(source)
    except (OSError, ValueError, subprocess.SubprocessError):
        issues.append('container_inventory_unavailable')
    return {'complete': not issues, 'paths': sorted(references), 'issues': issues}


def build_plan(project, *, usage=None, current_time=None, minimum_age_days=7):
    project = plain(project)
    current_time = time.time() if current_time is None else current_time
    usage = collect_usage(project) if usage is None else usage
    nodes, by_uuid, issues = {}, defaultdict(set), []
    roots, reasons = set(), defaultdict(set)
    top_metadata = []
    for family in FAMILIES:
        folder = plain(project / family)
        if not folder.exists():
            continue
        for entry in folder.iterdir():
            try:
                plain(entry)
                if entry.is_file() and entry.suffix == '.json':
                    top_metadata.append((entry, read_json(entry)))
                elif entry.is_dir() and family == 'junket-isolated-resilience' and entry.name == '.reuse':
                    for index in sorted(entry.glob('*.json')):
                        top_metadata.append((index, read_json(index)))
                elif entry.is_dir():
                    name = entry.relative_to(project).as_posix()
                    identifier = UUID.match(entry.name)
                    if not identifier:
                        issues.append('unrecognized_operation_directory:' + name)
                        continue
                    metadata = {}
                    for source in sorted(entry.glob('*.json')):
                        metadata[source.name] = read_json(source)
                    for relative in ('original/active.json', 'original/state.json'):
                        if (entry / relative).exists():
                            metadata[relative] = read_json(entry / relative)
                    job = metadata.get('job.json', {})
                    proof = metadata.get('isolated-resilience-proof.json', {})
                    status = job.get('status')
                    if family == 'junket-isolated-resilience':
                        status = 'succeeded' if (proof.get('isolated_server_resilience_verified') is True
                                  and proof.get('production_modified') is False
                                  and proof.get('final_stop', {}).get('graceful_stop') is True
                                  and not proof.get('issues')) else 'unknown_or_failed'
                    nodes[name] = {'path': entry, 'family': family, 'metadata': metadata,
                                   'status': status, 'mtime': entry.stat().st_mtime}
                    by_uuid[identifier.group()].add(name)
                    if status != 'succeeded':
                        roots.add(name)
                        reasons[name].add('active_failed_or_unknown_operation')
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                issues.append(type(exc).__name__ + ':' + str(entry))
    for filename in ('junket-native-overlay.json', 'minecraft-retention-pins.json'):
        if (project / filename).exists():
            try:
                value = read_json(project / filename)
                if filename == 'minecraft-retention-pins.json':
                    if (not isinstance(value, dict)
                            or not isinstance(value.get('operations', []), list)
                            or not isinstance(value.get('paths', []), list)):
                        raise ValueError('invalid explicit pins')
                    for identifier in value.get('operations', []):
                        if not isinstance(identifier, str) or not UUID.fullmatch(identifier) or identifier not in by_uuid:
                            raise ValueError('unresolved operation pin')
                    for path in value.get('paths', []):
                        if (not isinstance(path, str) or not Path(path).is_absolute()
                                or not plain(path).is_relative_to(project) or not Path(path).exists()):
                            raise ValueError('unresolved path pin')
                top_metadata.append((project / filename, value))
            except (OSError, ValueError, TypeError):
                issues.append('invalid_root_metadata:' + filename)

    def links(value):
        result = set()
        for text in strings(value):
            for identifier in UUID.findall(text):
                result.update(by_uuid.get(identifier, ()))
            normalized = text.replace('\\', '/')
            for name, node in nodes.items():
                absolute = node['path'].as_posix()
                if normalized == absolute or normalized.startswith(absolute + '/') or normalized == name:
                    result.add(name)
        return result

    for source, metadata in top_metadata:
        for name in links(metadata):
            roots.add(name)
            reasons[name].add('current_latest_or_explicit_pin:' + source.name)
    for referenced in usage.get('paths', []):
        for name, node in nodes.items():
            path = str(node['path'])
            if (referenced == path or referenced.startswith(path + os.sep)
                    or path.startswith(referenced.rstrip(os.sep) + os.sep)):
                roots.add(name)
                reasons[name].add('process_or_container_reference')
    # Follow current/previous, world/warp lineage and explicit references transitively.
    anchors = set(roots)
    queue = list(roots)
    while queue:
        name = queue.pop()
        metadata = nodes[name]['metadata']
        # Keep the previous state of a current/pinned/unfinished operation.
        # Do not turn every historical "previous" pointer into an infinite
        # retention chain. Explicit native/world lineage still follows fully.
        if name not in anchors:
            metadata = {key: value for key, value in metadata.items() if key != 'original/active.json'}
        for dependency in links(metadata) - {name}:
            reasons[dependency].add('recovery_reference_from:' + name)
            if dependency not in roots:
                roots.add(dependency)
                queue.append(dependency)
    # No recovery graph is complete if an expected previous active record is lost.
    for name in roots:
        metadata = nodes[name]['metadata']
        state = metadata.get('original/state.json', {})
        if state.get('active_exists') is True and 'original/active.json' not in metadata:
            issues.append('previous_active_metadata_missing:' + name)
    results = []
    for name, node in sorted(nodes.items()):
        # Never propose a whole operation: proofs, logs, worlds and private inputs remain.
        parts = ('data',) if node['family'] == 'junket-isolated-resilience' else ('stage', 'original')
        for part in parts:
            target = node['path'] / part
            if not target.exists():
                continue
            row = {'path': str(target), 'operation': name, 'scope': part,
                   'status': node['status'], 'deletion_authorized': False,
                   'reasons': sorted(reasons[name])}
            try:
                row.update(tree_usage(target))
                if name in roots:
                    row['decision'] = 'KEEP'
                elif issues or not usage.get('complete'):
                    row['decision'] = 'NEEDS_REVIEW'
                    row['reasons'].append('reference_or_usage_inventory_incomplete')
                elif row['multiple_link_files']:
                    row['decision'] = 'NEEDS_REVIEW'
                    row['reasons'].append('shared_inode_ownership_unknown')
                elif current_time - node['mtime'] < minimum_age_days * 86400:
                    row['decision'] = 'KEEP'
                    row['reasons'].append('minimum_review_age')
                else:
                    row['decision'] = 'REVIEW_CANDIDATE'
                    row['reasons'].append('requires_unique_asset_hash_restore_and_exact_path_review')
            except (OSError, ValueError) as exc:
                row.update(decision='NEEDS_REVIEW', estimated_reclaimable_bytes=0)
                row['reasons'].append(type(exc).__name__ + ':unsafe_or_unreadable_tree')
            results.append(row)
    return {'schema': 1, 'dry_run': True, 'deletion_supported': False, 'project': str(project),
            'created_at': current_time, 'capacity': check_capacity(project),
            'usage_complete': bool(usage.get('complete')), 'issues': issues + usage.get('issues', []),
            'protected_operations': sorted(roots), 'targets': results,
            'candidate_allocated_bytes': sum(r.get('estimated_reclaimable_bytes', 0) for r in results
                                              if r['decision'] == 'REVIEW_CANDIDATE'),
            'guaranteed_reclaimable_bytes': 0,
            'always_keep': ['data', 'backups', '.minecraft-storage', 'private inputs',
                            'source/candidate ZIPs', 'proof/job/log', 'Minecraft-Preservation'],
            'required_before_separately_approved_cleanup': ['fresh process/container references',
                'exact file/hash inventory', 'verified independent recovery source',
                'no unique code/world/private asset', 'no symlink/mount crossing',
                'review nested scope; never delete the operation or original directory wholesale']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=Path('/home/ubuntu/minecraft-bedrock-creative'))
    parser.add_argument('--dry-run', action='store_true', default=True)
    parser.add_argument('--capacity-only', action='store_true', help='Read disk free space without walking operations')
    parser.add_argument('--minimum-age-days', type=int, default=7)
    args = parser.parse_args(argv)
    if args.minimum_age_days < 0:
        parser.error('minimum age cannot be negative')
    if args.capacity_only:
        print(json.dumps({'dry_run': True, 'capacity': check_capacity(args.project)}, ensure_ascii=False, indent=2))
        return
    print(json.dumps(build_plan(args.project, minimum_age_days=args.minimum_age_days), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
