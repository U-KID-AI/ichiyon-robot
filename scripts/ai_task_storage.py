"""Read-only production storage admission checks (Python 3.8, stdlib only).

All paths, multipliers and reserves are trusted installation constants, never task
input. Existing releases/images/backups are already charged to statvfs usage.
Only *additional* allocations are budgeted, and a filesystem's availability and
reserve are counted once even when several categories share st_dev.

S = max(current source, runner source, target source), rounded allocation size;
unknown target source gets 2*S before fetch. Deployment start budgets 3*S for
git/archive staging and the new release, and 3*I on Docker's filesystem when a
new image is needed (image + temporary layers + cache), where I is at least the
largest current/target app image and four times source payload. Before build the
published target is measured again; before stop already allocated source/build
costs disappear from the *remaining* budget. At every destructive phase retain
backup tar bound + 2*logical DB size + 16MiB, shared growth 10% of persistence,
separate recreate and rollback allowances each max(256MiB, I/4), temporary
max(64MiB,S/2), and per-filesystem max(1GiB,5% total). The inode reserve is
max(8192,1% total), plus measured source entries and fixed operation allowances.

Runner additionally budgets S for source-repository fetch, 2*S for worktrees/tests
and 2*measured JDK image
(fallback largest app image, minimum 512MiB) for temporary extraction. Same-SHA
reconciliation needs only 2*S staging + 16MiB temporary + 64MiB per filesystem
and 128 reserved inodes; no build, backup or rollback budget is imposed. The
control directory always gets 1MiB and 64 inodes for helper/snapshot/pointer.

This is an admission estimate, not a quota: unbounded concurrent writes or a
new dependency much larger than existing images remain a documented limit.
"""

import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from dataclasses import dataclass

MIB = 1024 ** 2
GIB = 1024 ** 3
RELEASES = Path('/home/ubuntu/ichiyon-releases')
BACKUPS = Path('/home/ubuntu/ichiyon-backups')
SHARED = Path('/home/ubuntu/ichiyon-shared')
CURRENT = Path('/home/ubuntu/ichiyon-current')
RUNNER_SOURCE = Path('/home/ubuntu/ichiyon-ai-runner-src')
RUNNER_WORKTREES = Path('/home/ubuntu/ichiyon-ai-runner-worktrees')
CONTROL = Path('/home/ubuntu')
TEMP = Path('/tmp')
PHASES = ('runner', 'deploy-start', 'pre-build', 'pre-stop', 'reconcile')
CATEGORIES = ('releases', 'backups', 'shared', 'docker', 'temp', 'control', 'source', 'worktrees')
REASONS = ('BYTES', 'INODES', 'MEASUREMENT_UNAVAILABLE', 'INVALID_REQUEST')
SOURCE_GROWTH = 2
BUILD_MULTIPLIER = 3
SAFETY_BYTES = GIB
SAFETY_PERCENT = 5
SAFETY_INODES = 8192
SAFETY_INODE_PERCENT = 1
RECONCILE_RESERVE_BYTES = 64 * MIB
RECONCILE_RESERVE_INODES = 128


@dataclass(frozen=True)
class StorageRecord:
    phase: str
    filesystem: str
    available_bytes: int
    required_bytes: int
    available_inodes: int
    required_inodes: int

    def format_line(self):
        categories = self.filesystem.split('+')
        numbers = (self.available_bytes, self.required_bytes,
                   self.available_inodes, self.required_inodes)
        if self.phase not in PHASES or not categories or any(
                category not in CATEGORIES for category in categories):
            raise ValueError('invalid diagnostic label')
        if len(categories) != len(set(categories)) or any(
                type(value) is not int or value < 0 or value >= 10 ** 20 for value in numbers):
            raise ValueError('invalid diagnostic measurement')
        return ('STORAGE_PHASE={} STORAGE_FS={} STORAGE_AVAILABLE_BYTES={} '
                'STORAGE_REQUIRED_BYTES={} STORAGE_AVAILABLE_INODES={} '
                'STORAGE_REQUIRED_INODES={}').format(
                    self.phase, self.filesystem, self.available_bytes,
                    self.required_bytes, self.available_inodes, self.required_inodes)


class StorageError(RuntimeError):
    """Only allowlisted labels and measured integers cross this error boundary."""

    def __init__(self, phase, category, records=()):
        self.phase = phase if phase in PHASES else 'deploy-start'
        self.category = category if category in REASONS else 'MEASUREMENT_UNAVAILABLE'
        try:
            self.records = tuple(records)
            lines = tuple(record.format_line() for record in self.records
                          if isinstance(record, StorageRecord))
            if len(lines) != len(self.records):
                raise ValueError('invalid diagnostic record')
        except Exception:
            self.records, lines = (), ()
            self.category = 'MEASUREMENT_UNAVAILABLE'
        message = 'INSUFFICIENT_STORAGE phase={} reason={}'.format(self.phase, self.category)
        super().__init__('\n'.join((message,) + lines))

    def format_lines(self):
        return ('STORAGE_CHECK_PHASE=' + self.phase,) + tuple(
            record.format_line() for record in self.records) + (
            'STORAGE_REASON=' + self.category, 'DEPLOY_ERROR=INSUFFICIENT_STORAGE')


@dataclass(frozen=True)
class TreeSize:
    payload: int
    allocated: int
    entries: int
    tar_bytes: int


@dataclass(frozen=True)
class Capacity:
    device: int
    total_bytes: int
    available_bytes: int
    total_inodes: int
    available_inodes: int


@dataclass(frozen=True)
class Measurements:
    phase: str
    source: TreeSize
    persistence: TreeSize
    image_bytes: int
    target_image_exists: bool
    logical_db_bytes: int
    jdk_image_bytes: int
    capacities: dict


def _ceil_div(value, divisor):
    return (value + divisor - 1) // divisor


def tree_size(path, dereference=False, skip_git=False):
    """Metadata-only upper bound; dereference exactly the backup's roots.

    Hard links count independently (conservative). Sparse files use apparent
    size, matching non-sparse tar. Cycles, missing paths and special files fail
    closed; filenames and symlink targets never leave this module.
    """
    payload = allocated = entries = tar_bytes = 0
    active = set()

    def visit(item):
        nonlocal payload, allocated, entries, tar_bytes
        metadata = item.stat() if dereference else item.lstat()
        entries += 1
        # Three 512-byte headers allow GNU/PAX long-name metadata as well as
        # the ordinary header; include the encoded path itself for long paths.
        tar_bytes += 1536 + _ceil_div(len(os.fsencode(str(item))), 512) * 512
        allocated += 4096
        if stat.S_ISDIR(metadata.st_mode):
            identity = (metadata.st_dev, metadata.st_ino)
            if identity in active:
                raise ValueError('directory cycle')
            active.add(identity)
            with os.scandir(str(item)) as children:
                for child in children:
                    if not (skip_git and child.name == '.git'):
                        visit(Path(child.path))
            active.remove(identity)
        elif stat.S_ISREG(metadata.st_mode):
            if metadata.st_size < 0:
                raise ValueError('negative file size')
            payload += metadata.st_size
            allocated += _ceil_div(metadata.st_size, 4096) * 4096
            tar_bytes += _ceil_div(metadata.st_size, 512) * 512
        elif stat.S_ISLNK(metadata.st_mode) and not dereference:
            payload += metadata.st_size
            tar_bytes += _ceil_div(metadata.st_size, 512) * 512
        else:
            raise ValueError('unsupported file type')

    # A source root may itself be a supported directory symlink. Resolve only
    # this root; interior source symlinks still stay links like git archive.
    visit(Path(path).resolve(strict=True))
    return TreeSize(payload, allocated, entries, _ceil_div(tar_bytes + 1024, 10240) * 10240)


def _command(argv):
    result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=30, check=False)
    if result.returncode != 0 or len(result.stdout) > 65536:
        raise ValueError('measurement command failed')
    return result.stdout.decode('utf-8', errors='strict').strip()


def _positive_int(value):
    if not re.fullmatch('[0-9]+', str(value)) or int(value) <= 0:
        raise ValueError('invalid size')
    return int(value)


def _image_size(reference):
    return _positive_int(_command(['docker', 'image', 'inspect', '--format', '{{.Size}}', reference]))


def _optional_image(reference):
    # A successful empty listing means absent. An inspect/daemon error must not
    # be mistaken for absence and silently turn into a smaller estimate.
    images = _command(['docker', 'image', 'ls', '--no-trunc', '--quiet',
                       '--filter', 'reference=' + reference]).splitlines()
    if not images:
        return 0
    if len(set(images)) != 1 or not re.fullmatch('sha256:[0-9a-f]{64}', images[0]):
        raise ValueError('ambiguous image')
    return _image_size(images[0])


def _capacity(path):
    device = path.stat().st_dev
    values = os.statvfs(str(path))
    if values.f_frsize <= 0 or values.f_blocks <= 0 or values.f_files <= 0:
        raise ValueError('capacity unavailable')
    if values.f_bavail < 0 or values.f_favail < 0:
        raise ValueError('capacity unavailable')
    if values.f_bavail > values.f_blocks or values.f_favail > values.f_files:
        raise ValueError('inconsistent capacity')
    if values.f_flag & getattr(os, 'ST_RDONLY', 1):
        raise ValueError('read-only filesystem')
    return Capacity(device, values.f_blocks * values.f_frsize,
                    values.f_bavail * values.f_frsize, values.f_files, values.f_favail)


def measure(phase, sha):
    current = CURRENT.resolve(strict=True)
    if current.parent != RELEASES.resolve(strict=True) or not re.fullmatch('[0-9a-f]{40}', current.name):
        raise ValueError('current release identity')
    if phase == 'deploy-start' and sha == current.name:
        phase = 'reconcile'
    if phase == 'reconcile' and sha != current.name:
        raise ValueError('reconciliation identity')
    sources = [tree_size(current / 'src')]
    if phase != 'reconcile':
        sources.append(tree_size(RUNNER_SOURCE, skip_git=True))
    target = RELEASES / sha if sha else None
    if target is not None and target.exists():
        sources.append(tree_size(target / 'src'))
    elif phase in ('pre-build', 'pre-stop'):
        raise ValueError('target unavailable')
    source = TreeSize(*(max(getattr(item, field) for item in sources)
                        for field in ('payload', 'allocated', 'entries', 'tar_bytes')))
    docker_root = Path(_command(['docker', 'info', '--format', '{{.DockerRootDir}}']))
    if not docker_root.is_absolute():
        raise ValueError('docker root unavailable')
    roots = {'releases': RELEASES, 'docker': docker_root, 'temp': TEMP, 'control': CONTROL}
    if phase != 'reconcile':
        roots.update({'backups': BACKUPS, 'shared': SHARED})
    if phase == 'runner':
        roots['source'] = RUNNER_SOURCE
        roots['worktrees'] = RUNNER_WORKTREES
    empty = TreeSize(0, 0, 0, 0)
    persistence, image_bytes, target_size, db_size, jdk_size = empty, 0, 0, 0, 0
    if phase != 'reconcile':
        trees = [tree_size(SHARED / relative, dereference=True)
                 for relative in ('data', 'assets/images', 'secrets', '.env')]
        persistence = TreeSize(*(sum(getattr(item, field) for item in trees)
                                  for field in ('payload', 'allocated', 'entries', 'tar_bytes')))
        sizes = []
        for service in ('admin', 'bot', 'bot-irsia'):
            ids = _command(['docker', 'ps', '--quiet', '--no-trunc', '--filter',
                            'label=com.docker.compose.project=ichiyon-robot', '--filter',
                            'label=com.docker.compose.service=' + service]).splitlines()
            if len(ids) != 1 or not re.fullmatch('[0-9a-f]{64}', ids[0]):
                raise ValueError('current app identity')
            image_id = _command(['docker', 'inspect', '--format', '{{.Image}}', ids[0]])
            if not re.fullmatch('sha256:[0-9a-f]{64}', image_id):
                raise ValueError('current app image identity')
            sizes.append(_image_size(image_id))
        target_size = _optional_image('ichiyon-robot-app:' + sha) if sha else 0
        if phase == 'pre-stop' and not target_size:
            raise ValueError('target image unavailable')
        image_bytes = max(sizes + [target_size, source.payload * 4])
        db_size = _positive_int(_command([
            'docker', 'exec', 'ichiyon-robot-db', 'sh', '-c',
            'exec psql -XqAt -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
            '-c "BEGIN READ ONLY; SELECT pg_database_size(current_database()); COMMIT;"']))
        if phase == 'runner':
            jdk_size = _optional_image('eclipse-temurin:21-jdk') or image_bytes
    # Read availability last, after potentially expensive metadata probes.
    capacities = {name: _capacity(path) for name, path in roots.items()}
    return Measurements(phase, source, persistence, image_bytes, bool(target_size),
                        db_size, jdk_size, capacities)


def requirements(measured):
    """Pure peak-remaining-allocation model; values are bytes and inode counts."""
    phase = measured.phase
    costs = {name: [0, 0] for name in measured.capacities}

    def add(category, size, inodes):
        costs[category][0] += size
        costs[category][1] += inodes

    source = measured.source
    size = max(source.allocated, source.tar_bytes)
    entries = source.entries
    # Fixed helper, infrastructure snapshot and atomic current-link pointer are
    # written under /home/ubuntu, which may differ from every mounted data root.
    add('control', MIB, 64)
    if phase == 'reconcile':
        add('releases', 2 * size, 2 * entries + 128)
        add('temp', 16 * MIB, 32)
        return costs
    # Start and Runner do not yet know the fetched target source. Pre-build
    # measures the published target; its source allocation already exists.
    if phase in ('runner', 'deploy-start'):
        size *= SOURCE_GROWTH
        entries *= SOURCE_GROWTH
        add('releases', 3 * size, 3 * entries + 256)
    if phase != 'pre-stop' and not measured.target_image_exists:
        add('docker', BUILD_MULTIPLIER * measured.image_bytes,
            max(32768, 4 * entries))
    add('backups', measured.persistence.tar_bytes + 2 * measured.logical_db_bytes + 16 * MIB, 32)
    add('shared', _ceil_div(measured.persistence.tar_bytes, 10), 256)
    # Do not spend rollback headroom on the backup: two independent allowances
    # survive even when a completed backup already exists.
    add('docker', 2 * max(256 * MIB, _ceil_div(measured.image_bytes, 4)), 8192)
    add('temp', max(64 * MIB, _ceil_div(size, 2)), 1024)
    if phase == 'runner':
        add('source', size, entries + 256)
        add('worktrees', 2 * size, 2 * entries + 1024)
        add('temp', max(512 * MIB, 2 * measured.jdk_image_bytes), 8192)
    return costs


def evaluate(measured):
    costs = requirements(measured)
    groups = {}
    for category in CATEGORIES:
        if category not in measured.capacities:
            continue
        if measured.phase == 'reconcile' and category in ('backups', 'shared'):
            continue
        capacity = measured.capacities[category]
        group = groups.setdefault(capacity.device, [])
        group.append((category, capacity))
    records = []
    for group in groups.values():
        # Same-device samples may race ordinary service writes. Taking minima
        # avoids summing availability or picking the most optimistic sample.
        available = min(capacity.available_bytes for _, capacity in group)
        available_inodes = min(capacity.available_inodes for _, capacity in group)
        total = max(capacity.total_bytes for _, capacity in group)
        total_inodes = max(capacity.total_inodes for _, capacity in group)
        if measured.phase == 'reconcile':
            reserve, inode_reserve = RECONCILE_RESERVE_BYTES, RECONCILE_RESERVE_INODES
        else:
            reserve = max(SAFETY_BYTES, _ceil_div(total * SAFETY_PERCENT, 100))
            inode_reserve = max(SAFETY_INODES, _ceil_div(total_inodes * SAFETY_INODE_PERCENT, 100))
        records.append(StorageRecord(
            measured.phase, '+'.join(category for category, _ in group), available,
            reserve + sum(costs[category][0] for category, _ in group), available_inodes,
            inode_reserve + sum(costs[category][1] for category, _ in group)))
    records = tuple(records)
    if any(record.available_bytes < record.required_bytes for record in records):
        raise StorageError(measured.phase, 'BYTES', records)
    if any(record.available_inodes < record.required_inodes for record in records):
        raise StorageError(measured.phase, 'INODES', records)
    return records


def check_storage(phase, sha=None):
    if phase not in PHASES or (sha is None and phase != 'runner') or (
            sha is not None and (not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{40}', sha))):
        raise StorageError(phase, 'INVALID_REQUEST')
    try:
        return evaluate(measure(phase, sha))
    except StorageError:
        raise
    except Exception:
        # No filenames, arbitrary command output, credentials or traceback.
        raise StorageError(phase, 'MEASUREMENT_UNAVAILABLE') from None


def safe_diagnostics(output):
    """Select only the fixed numeric storage protocol from mixed SSH output."""
    result = ['DEPLOY_ERROR=INSUFFICIENT_STORAGE']
    phase_pattern = '(?:' + '|'.join(PHASES) + ')'
    category_pattern = '(?:' + '|'.join(CATEGORIES) + ')'
    record_pattern = (r'STORAGE_PHASE=' + phase_pattern + r' STORAGE_FS=('
                      + category_pattern + r'(?:\+' + category_pattern + r')*)'
                      + r' STORAGE_AVAILABLE_BYTES=[0-9]{1,20}'
                      + r' STORAGE_REQUIRED_BYTES=[0-9]{1,20}'
                      + r' STORAGE_AVAILABLE_INODES=[0-9]{1,20}'
                      + r' STORAGE_REQUIRED_INODES=[0-9]{1,20}')
    reason = 'MEASUREMENT_UNAVAILABLE'
    for line in str(output).splitlines():
        match = re.fullmatch(record_pattern, line)
        if match:
            categories = match.group(1).split('+')
            if len(categories) == len(set(categories)) and line not in result:
                result.append(line)
        elif line in tuple('STORAGE_CHECK_PHASE=' + phase for phase in PHASES):
            if line not in result:
                result.append(line)
        elif line in tuple('STORAGE_REASON=' + item for item in REASONS):
            reason = line.split('=', 1)[1]
    result.append('STORAGE_REASON=' + reason)
    return '\n'.join(result)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    phase = args[0] if args else 'deploy-start'
    sha = args[1] if len(args) == 2 else None
    try:
        if len(args) != 2 and not (len(args) == 1 and phase == 'runner'):
            raise StorageError(phase, 'INVALID_REQUEST')
        records = check_storage(phase, sha)
    except StorageError as error:
        for line in error.format_lines():
            print(line)
        return 1
    for record in records:
        print(record.format_line())
    return 0


if __name__ == '__main__':
    sys.exit(main())
