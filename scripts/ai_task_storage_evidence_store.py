"""Fixed-root transactional deployment receipt store; never a cleanup executor.

SQLite is the durable index and archive. Active operations and recent successful
no-op receipts are bounded. Object and incident receipts are lossless; only
verified C raw rows are retired after their digest enters a success rollup in
the same FULL-synchronous transaction. Legacy v1 files are never changed.

Checksums detect accidental damage, not an attacker running as ubuntu. Fixed
paths, Linux ownership, private modes, protocol locking and caller validation
are the authority boundary. No environment or CLI selects paths or policies.
"""
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import uuid

ROOT = Path('/home/ubuntu/ichiyon-storage-evidence')
MAX_ACTIVE = 64
RECENT_LIMIT = 64
PAGE_LIMIT = 128
MAX_PAYLOAD_BYTES = 256 * 1024
MAX_OBJECT_KEYS = 16
SCHEMA = 'ichiyon-deployment-evidence-store'
RECEIPT_SCHEMA = 'ichiyon-deployment-evidence-store-receipt'
VERSION = 2
APPLICATION_ID = 0x49434849
OPID = re.compile(r'[0-9a-f]{32}\Z')
HEX = re.compile(r'[0-9a-f]{64}\Z')
SHA = re.compile(r'[0-9a-f]{40}\Z')
STAGE = re.compile(r'\.(?:prepare|release|backup)-[0-9a-f]{40}\.[A-Za-z0-9]{8,32}\Z')
COUNTER_KEYS = {'generation', 'active', 'A', 'B', 'C_total', 'recent', 'rollups', 'object_bindings'}
ZERO = '0' * 64
_TABLES = {
    'meta': 'CREATE TABLE meta (singleton INTEGER PRIMARY KEY CHECK(singleton=1), body TEXT NOT NULL, checksum TEXT NOT NULL)',
    'active': 'CREATE TABLE active (operation_id TEXT PRIMARY KEY, body TEXT NOT NULL, checksum TEXT NOT NULL)',
    'receipts': 'CREATE TABLE receipts (operation_id TEXT PRIMARY KEY, generation INTEGER NOT NULL UNIQUE, classification TEXT NOT NULL CHECK(classification IN (\'A\',\'B\',\'C\')), body TEXT NOT NULL, checksum TEXT NOT NULL)',
    'object_index': 'CREATE TABLE object_index (category TEXT NOT NULL, object_key TEXT NOT NULL, operation_id TEXT NOT NULL REFERENCES receipts(operation_id), receipt_checksum TEXT NOT NULL, checksum TEXT NOT NULL, PRIMARY KEY(category,object_key,operation_id))',
    'rollups': 'CREATE TABLE rollups (target_sha TEXT NOT NULL, previous_sha TEXT NOT NULL, body TEXT NOT NULL, checksum TEXT NOT NULL, PRIMARY KEY(target_sha,previous_sha))',
}
_INDEXES = {
    'receipt_class_generation': 'CREATE INDEX receipt_class_generation ON receipts(classification,generation)',
    'object_operation': 'CREATE INDEX object_operation ON object_index(operation_id)',
}


class StoreError(ValueError):
    """Stable diagnostic only; never echo untrusted content."""


def need(condition, code):
    if not condition:
        raise StoreError(code)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode('ascii')


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def _decode(raw, checksum, limit=MAX_PAYLOAD_BYTES * 2 + 16384):
    def pairs(items):
        out = {}
        for key, value in items:
            need(key not in out, 'store_duplicate_json_key')
            out[key] = value
        return out
    need(isinstance(raw, str) and len(raw) <= limit, 'store_document_limit')
    try:
        value = json.loads(raw, object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(StoreError('store_json_number_invalid')))
        need(isinstance(checksum, str) and HEX.fullmatch(checksum) and digest(value) == checksum,
             'store_checksum_invalid')
        return value
    except (TypeError, OverflowError, json.JSONDecodeError, UnicodeError):
        raise StoreError('store_json_invalid') from None


def _uid():
    import pwd
    return pwd.getpwnam('ubuntu').pw_uid


def _normal_dir(path):
    path = Path(path)
    need(path.is_absolute() and path.resolve(strict=True) == path, 'store_directory_alias')
    for parent in (path, *path.parents):
        info = parent.lstat()
        need(stat.S_ISDIR(info.st_mode) and not parent.is_symlink(), 'store_directory_alias')
    return path


def _owned(info, mode):
    if os.name != 'nt':
        need(info.st_uid == _uid() and stat.S_IMODE(info.st_mode) == mode, 'store_owner_or_mode_invalid')


def _file(path):
    info = path.lstat()
    need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and not path.is_symlink(),
         'store_file_not_exclusive_regular')
    _owned(info, 0o600)
    return info


def _fsync_dir(path):
    if os.name == 'nt':
        return
    fd = os.open(str(path), os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _paths(create=False):
    _normal_dir(ROOT.parent)
    new_namespace = False
    for path in (ROOT, ROOT / 'v2'):
        if create:
            try:
                path.mkdir(mode=0o700)
                if path == ROOT / 'v2':
                    new_namespace = True
                _fsync_dir(path)
                _fsync_dir(path.parent)
            except FileExistsError:
                pass
        _normal_dir(path)
        _owned(path.lstat(), 0o700)
        need(path.stat().st_dev == ROOT.parent.stat().st_dev, 'store_mount_crossing')
    path = ROOT / 'v2' / 'index.sqlite3'
    created = False
    if create:
        need(new_namespace or path.exists() or path.is_symlink(), 'store_missing_existing_database')
        try:
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
            _fsync_dir(path.parent)
            created = True
    info = _file(path)
    # DELETE journal is SQLite's crash-recovery mechanism. A private regular
    # hot journal is allowed; readonly opens cannot recover it and fail closed.
    for suffix in ('-journal', '-wal', '-shm'):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            need(suffix == '-journal', 'store_unexpected_sidecar')
            _file(sidecar)
    return path, (info.st_dev, info.st_ino), created


def _object_key(value):
    need(isinstance(value, (list, tuple)) and len(value) == 2 and all(isinstance(x, str) for x in value),
         'store_object_key_invalid')
    category, key = value
    valid = ((category in ('releases', 'backups') and SHA.fullmatch(key)) or
             (category == 'images' and key.startswith('sha256:') and HEX.fullmatch(key[7:])) or
             (category == 'migration' and HEX.fullmatch(key)) or
             (category == 'staging' and STAGE.fullmatch(key)))
    need(valid, 'store_object_key_invalid')
    return (category, key)


def _keys(values):
    need(isinstance(values, (list, tuple)) and len(values) <= MAX_OBJECT_KEYS, 'store_object_limit')
    result = [_object_key(value) for value in values]
    need(len(set(result)) == len(result), 'store_duplicate_object_key')
    return sorted(result)


def _payload(payload, terminal=False):
    need(isinstance(payload, dict) and len(encoded(payload)) <= MAX_PAYLOAD_BYTES, 'store_payload_invalid')
    opid = payload.get('operation_id')
    need(isinstance(opid, str) and OPID.fullmatch(opid), 'store_operation_id_invalid')
    need(payload.get('state') in ('succeeded', 'failed', 'cancelled') if terminal else payload.get('state') == 'running',
         'store_payload_state_invalid')
    for key in ('target_sha', 'previous_sha'):
        need(isinstance(payload.get(key), str) and SHA.fullmatch(payload[key]), 'store_sha_invalid')
    need(type(payload.get('sequence')) is int and payload['sequence'] > 0, 'store_sequence_invalid')
    for key in ('created_at', 'completed_at') if terminal else ('created_at',):
        value = payload.get(key)
        need(type(value) in (int, float) and math.isfinite(value) and value >= 0, 'store_timestamp_invalid')
    if terminal:
        need(payload['completed_at'] >= payload['created_at'], 'store_timestamp_invalid')
    return opid


def _receipt(body, checksum):
    envelope = _decode(body, checksum)
    need(isinstance(envelope, dict) and set(envelope) == {'schema', 'version', 'classification',
         'active_payload', 'payload', 'object_keys', 'proof'} and envelope['schema'] == RECEIPT_SCHEMA and
         envelope['version'] == VERSION and type(envelope['version']) is int and
         envelope['classification'] in ('A', 'B', 'C'), 'store_receipt_schema_invalid')
    active, payload = envelope['active_payload'], envelope['payload']
    opid = _payload(payload, True)
    need(_payload(active) == opid and payload.get('active_payload_sha256') == digest(active) and
         payload['sequence'] == active['sequence'] + 1, 'store_active_binding_invalid')
    for key in ('owner', 'target_sha', 'previous_sha', 'created_at', 'lock', 'initial_names', 'initial_runtime', 'staging'):
        need(payload.get(key) == active.get(key), 'store_active_binding_invalid')
    keys = _keys(envelope['object_keys'])
    need(envelope['object_keys'] == [list(key) for key in keys], 'store_object_order_invalid')
    if envelope['classification'] == 'C':
        need(not keys and envelope['proof'] == {'persistent_absence_verified': True, 'health_verified': True,
             'cleanup_verified': True} and payload['state'] == 'succeeded' and
             payload.get('rollback_result') == 'not_needed' and payload['target_sha'] == payload['previous_sha'],
             'store_rollup_not_proven')
    else:
        need(envelope['proof'] is None, 'store_proof_invalid')
    return dict(envelope, sha256=checksum)


def _rollup(raw, checksum):
    item = _decode(raw, checksum, 8192)
    need(isinstance(item, dict) and set(item) == {'schema', 'version', 'target_sha', 'previous_sha', 'first_at',
         'last_at', 'count', 'state', 'health_verified', 'root_sha256'} and
         item['schema'] == SCHEMA + '-success-rollup' and item['version'] == VERSION and
         type(item['count']) is int and item['count'] > 0 and item['state'] == 'succeeded' and
         item['health_verified'] is True and isinstance(item['root_sha256'], str) and HEX.fullmatch(item['root_sha256']) and
         isinstance(item['target_sha'], str) and SHA.fullmatch(item['target_sha']) and item['target_sha'] == item['previous_sha'] and
         type(item['first_at']) in (int, float) and type(item['last_at']) in (int, float) and
         math.isfinite(item['first_at']) and math.isfinite(item['last_at']) and
         0 <= item['first_at'] <= item['last_at'], 'store_rollup_invalid')
    return item


class Store:
    """Writer session. Front end must authenticate the deployment lock and payload."""
    def __init__(self, create=False, readonly=False):
        self.connection = None
        self.readonly = readonly
        self._summary_cache = None
        self.path, self.identity, created = _paths(create)
        need(not created or not readonly, 'store_readonly_creation')
        try:
            uri = self.path.as_uri() + ('?mode=ro' if readonly else '?mode=rw')
            self.connection = sqlite3.connect(uri, uri=True, timeout=5, isolation_level=None)
            self.connection.execute('PRAGMA trusted_schema=OFF')
            self.connection.execute('PRAGMA foreign_keys=ON')
            self.connection.execute('PRAGMA busy_timeout=5000')
            if not created:
                # Inspect existing identities before any persistent PRAGMA.
                self._schema()
            if readonly:
                self.connection.execute('PRAGMA query_only=ON')
            else:
                need(self.connection.execute('PRAGMA journal_mode').fetchone()[0] == 'delete', 'store_journal_mode_invalid')
                self.connection.execute('PRAGMA synchronous=FULL')
            if created:
                with self.transaction():
                    for sql in _TABLES.values():
                        self.connection.execute(sql)
                    for sql in _INDEXES.values():
                        self.connection.execute(sql)
                    self.connection.execute('PRAGMA application_id=' + str(APPLICATION_ID))
                    self.connection.execute('PRAGMA user_version=' + str(VERSION))
                    self._write_meta({key: 0 for key in COUNTER_KEYS}, initial=True)
            self._schema()
            self._identity()
            if readonly:
                self.connection.execute('BEGIN')
                self._meta()  # Establish the stable snapshot immediately.
        except (sqlite3.Error, StoreError):
            self.close()
            raise StoreError('store_open_failed') from None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def _identity(self):
        info = _file(self.path)
        need((info.st_dev, info.st_ino) == self.identity, 'store_file_changed')

    def _schema(self):
        need(self.connection.execute('PRAGMA application_id').fetchone()[0] == APPLICATION_ID and
             self.connection.execute('PRAGMA user_version').fetchone()[0] == VERSION, 'store_schema_invalid')
        tables = dict(self.connection.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
        need(tables == _TABLES, 'store_schema_invalid')
        indexes = dict(self.connection.execute("SELECT name,sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"))
        need(indexes == _INDEXES, 'store_schema_invalid')
        need(not self.connection.execute("SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view')").fetchone(), 'store_schema_invalid')

    @contextmanager
    def transaction(self):
        need(not self.readonly, 'store_readonly')
        self._identity()
        self.connection.execute('BEGIN IMMEDIATE')
        try:
            yield
            self._identity()
            self.connection.execute('COMMIT')
        except BaseException:
            if self.connection.in_transaction:
                self.connection.execute('ROLLBACK')
            raise

    def _meta(self):
        row = self.connection.execute('SELECT body,checksum FROM meta WHERE singleton=1').fetchone()
        need(row is not None, 'store_metadata_missing')
        body = _decode(*row, limit=8192)
        need(isinstance(body, dict) and set(body) == {'schema', 'version', 'counters'} and
             body['schema'] == SCHEMA and body['version'] == VERSION and
             isinstance(body['counters'], dict) and set(body['counters']) == COUNTER_KEYS and
             all(type(v) is int and 0 <= v <= 0x7fffffffffffffff for v in body['counters'].values()), 'store_metadata_invalid')
        counts = body['counters']
        need(counts['active'] <= MAX_ACTIVE and counts['recent'] <= RECENT_LIMIT and
             counts['recent'] <= counts['C_total'] and counts['generation'] >=
             counts['active'] + counts['A'] + counts['B'] + counts['C_total'], 'store_metadata_invalid')
        return counts

    def _write_meta(self, counts, initial=False):
        body = dict(schema=SCHEMA, version=VERSION, counters=counts)
        values = (encoded(body).decode('ascii'), digest(body))
        if initial:
            self.connection.execute('INSERT INTO meta VALUES(1,?,?)', values)
        else:
            need(self.connection.execute('UPDATE meta SET body=?,checksum=? WHERE singleton=1', values).rowcount == 1,
                 'store_metadata_missing')

    def begin(self, payload_factory):
        with self.transaction():
            counts = self._meta()
            need(counts['active'] < MAX_ACTIVE and counts['generation'] < 0x7fffffffffffffff, 'store_active_admission_limit')
            counts['generation'] += 1
            opid = format(counts['generation'], '016x') + uuid.uuid4().hex[:16]
            payload = payload_factory(opid)
            need(_payload(payload) == opid and payload['sequence'] == 1, 'store_new_payload_invalid')
            self.connection.execute('INSERT INTO active VALUES(?,?,?)', (opid, encoded(payload).decode('ascii'), digest(payload)))
            counts['active'] += 1
            self._write_meta(counts)
        return opid

    def get_active(self, opid):
        need(isinstance(opid, str) and OPID.fullmatch(opid), 'store_operation_id_invalid')
        row = self.connection.execute('SELECT body,checksum FROM active WHERE operation_id=?', (opid,)).fetchone()
        need(row is not None, 'store_active_missing_or_terminal')
        payload = _decode(*row, limit=MAX_PAYLOAD_BYTES)
        need(_payload(payload) == opid, 'store_active_identity_invalid')
        return payload

    def get(self, opid):
        """Lossless retained terminal receipt. Compacted C IDs are not recreated."""
        need(isinstance(opid, str) and OPID.fullmatch(opid), 'store_operation_id_invalid')
        row = self.connection.execute('SELECT operation_id,classification,body,checksum FROM receipts WHERE operation_id=?', (opid,)).fetchone()
        need(row is not None, 'store_terminal_missing_or_compacted')
        return self._terminal_row(row)

    def update(self, payload):
        opid = _payload(payload)
        with self.transaction():
            self._meta()
            old = self.get_active(opid)
            need(payload['sequence'] == old['sequence'] + 1, 'store_active_sequence_invalid')
            for key in ('owner', 'target_sha', 'previous_sha', 'created_at', 'lock', 'initial_names', 'initial_runtime'):
                need(payload.get(key) == old.get(key), 'store_active_identity_invalid')
            self.connection.execute('UPDATE active SET body=?,checksum=? WHERE operation_id=?',
                                    (encoded(payload).decode('ascii'), digest(payload), opid))

    def finish(self, payload, classification, object_keys=(), proof=None):
        opid = _payload(payload, True)
        keys = _keys(object_keys)
        need(classification in ('A', 'B', 'C'), 'store_classification_invalid')
        with self.transaction():
            counts = self._meta()
            active = self.get_active(opid)
            body = dict(schema=RECEIPT_SCHEMA, version=VERSION, classification=classification,
                        active_payload=active, payload=payload, object_keys=[list(key) for key in keys], proof=proof)
            checksum = digest(body)
            envelope = _receipt(encoded(body).decode('ascii'), checksum)
            generation = int(opid[:16], 16)
            need(0 < generation <= counts['generation'], 'store_generation_invalid')
            self.connection.execute('INSERT INTO receipts VALUES(?,?,?,?,?)',
                (opid, generation, classification, encoded(body).decode('ascii'), checksum))
            for category, key in keys:
                index_value = [category, key, opid, checksum]
                self.connection.execute('INSERT INTO object_index VALUES(?,?,?,?,?)', (*index_value, digest(index_value)))
            counts['object_bindings'] += len(keys)
            if classification == 'C':
                self._add_rollup(payload, checksum, counts)
                counts['C_total'] += 1
                counts['recent'] += 1
                if counts['recent'] > RECENT_LIMIT:
                    retired = self.connection.execute("SELECT operation_id,body,checksum FROM receipts WHERE classification='C' ORDER BY generation LIMIT 1").fetchone()
                    need(retired is not None, 'store_recent_missing')
                    old = _receipt(retired[1], retired[2])
                    need(old['classification'] == 'C' and not old['object_keys'], 'store_compaction_not_proven')
                    rolled = self.connection.execute('SELECT body,checksum FROM rollups WHERE target_sha=? AND previous_sha=?',
                        (old['payload']['target_sha'], old['payload']['previous_sha'])).fetchone()
                    need(rolled is not None, 'store_compaction_rollup_missing')
                    previous_rollup = _rollup(*rolled)
                    need(previous_rollup['target_sha'] == old['payload']['target_sha'] and
                         previous_rollup['previous_sha'] == old['payload']['previous_sha'] and
                         previous_rollup['first_at'] <= old['payload']['completed_at'] <= previous_rollup['last_at'],
                         'store_compaction_rollup_invalid')
                    need(not self.connection.execute('SELECT 1 FROM object_index WHERE operation_id=? LIMIT 1', (retired[0],)).fetchone(),
                         'store_compaction_referenced')
                    # Rollup publication and raw retirement are one transaction.
                    self.connection.execute('DELETE FROM receipts WHERE operation_id=?', (retired[0],))
                    counts['recent'] -= 1
            else:
                counts[classification] += 1
            need(self.connection.execute('DELETE FROM active WHERE operation_id=?', (opid,)).rowcount == 1, 'store_active_missing')
            counts['active'] -= 1
            self._write_meta(counts)
        return envelope

    def _add_rollup(self, payload, checksum, counts):
        target, previous = payload['target_sha'], payload['previous_sha']
        row = self.connection.execute('SELECT body,checksum FROM rollups WHERE target_sha=? AND previous_sha=?', (target, previous)).fetchone()
        at = payload['completed_at']
        if row is None:
            item = dict(schema=SCHEMA + '-success-rollup', version=VERSION, target_sha=target, previous_sha=previous,
                        first_at=at, last_at=at, count=0, state='succeeded', health_verified=True, root_sha256=ZERO)
            counts['rollups'] += 1
        else:
            item = _rollup(*row)
            need(item['target_sha'] == target and item['previous_sha'] == previous, 'store_rollup_key_invalid')
        item['first_at'] = min(item['first_at'], at)
        item['last_at'] = max(item['last_at'], at)
        item['count'] += 1
        item['root_sha256'] = digest(dict(previous_root=item['root_sha256'], receipt_sha256=checksum,
            operation_id=payload['operation_id'], ordinal=item['count']))
        self.connection.execute('INSERT OR REPLACE INTO rollups VALUES(?,?,?,?)',
            (target, previous, encoded(item).decode('ascii'), digest(item)))

    def summary(self):
        self._identity()
        if self.readonly and self._summary_cache is not None:
            return json.loads(self._summary_cache)
        counts = self._meta()
        measured = dict(active=self.connection.execute('SELECT count(*) FROM active').fetchone()[0],
                        rollups=self.connection.execute('SELECT count(*) FROM rollups').fetchone()[0],
                        object_bindings=self.connection.execute('SELECT count(*) FROM object_index').fetchone()[0])
        terminal = dict(self.connection.execute('SELECT classification,count(*) FROM receipts GROUP BY classification'))
        need(measured['active'] == counts['active'] and measured['rollups'] == counts['rollups'] and
             measured['object_bindings'] == counts['object_bindings'] and
             terminal.get('A', 0) == counts['A'] and terminal.get('B', 0) == counts['B'] and
             terminal.get('C', 0) == counts['recent'], 'store_count_mismatch')
        rollup_total = 0
        for target, previous, body, checksum in self.connection.execute('SELECT target_sha,previous_sha,body,checksum FROM rollups'):
            item = _rollup(body, checksum)
            need(item['target_sha'] == target and item['previous_sha'] == previous, 'store_rollup_key_invalid')
            rollup_total += item['count']
        need(rollup_total == counts['C_total'], 'store_rollup_count_mismatch')
        # Audit only object-bearing edges, never the historical C population.
        # This catches a damaged key that would otherwise look like "not found".
        for category, key, opid, receipt_checksum, index_checksum, *receipt in self.connection.execute(
                'SELECT i.category,i.object_key,i.operation_id,i.receipt_checksum,i.checksum,r.operation_id,r.classification,r.body,r.checksum FROM object_index i LEFT JOIN receipts r ON r.operation_id=i.operation_id'):
            _object_key((category, key))
            need(index_checksum == digest([category, key, opid, receipt_checksum]) and receipt[0] == opid and
                 receipt[-1] == receipt_checksum, 'store_object_index_invalid')
            envelope = self._terminal_row(receipt)
            need([category, key] in envelope['object_keys'] and envelope['classification'] != 'C', 'store_object_index_invalid')
        result = dict(schema=SCHEMA, version=VERSION, counters=counts, database_bytes=self.path.stat().st_size,
                      limits=dict(active=MAX_ACTIVE, recent=RECENT_LIMIT, page=PAGE_LIMIT))
        if self.readonly:
            self._summary_cache = encoded(result)
        return result

    def active(self):
        rows = self.connection.execute('SELECT operation_id,body,checksum FROM active ORDER BY operation_id LIMIT ?', (MAX_ACTIVE + 1,)).fetchall()
        need(len(rows) <= MAX_ACTIVE, 'store_active_admission_limit')
        result = []
        for opid, raw, checksum in rows:
            payload = _decode(raw, checksum, MAX_PAYLOAD_BYTES)
            need(_payload(payload) == opid, 'store_active_identity_invalid')
            result.append(dict(schema=SCHEMA + '-active', version=VERSION, payload=payload, sha256=checksum))
        return result

    def _terminal_row(self, row):
        opid, classification, body, checksum = row
        envelope = _receipt(body, checksum)
        need(envelope['payload']['operation_id'] == opid and envelope['classification'] == classification,
             'store_receipt_identity_invalid')
        # Each retained object binding must still resolve through the index.
        for category, key in envelope['object_keys']:
            index = self.connection.execute('SELECT receipt_checksum,checksum FROM object_index WHERE category=? AND object_key=? AND operation_id=?',
                                           (category, key, opid)).fetchone()
            need(index is not None and index[0] == checksum and index[1] == digest([category, key, opid, checksum]),
                 'store_object_index_invalid')
        return envelope

    def recent(self):
        rows = self.connection.execute("SELECT operation_id,classification,body,checksum FROM receipts WHERE classification='C' ORDER BY generation DESC LIMIT ?", (RECENT_LIMIT + 1,)).fetchall()
        need(len(rows) <= RECENT_LIMIT, 'store_recent_limit')
        return [self._terminal_row(row) for row in rows]

    def incidents(self, after_id='', limit=PAGE_LIMIT):
        return self._page('B', after_id, limit)

    def persistent(self, after_id='', limit=PAGE_LIMIT):
        return self._page('A', after_id, limit)

    def _page(self, classification, after_id, limit):
        need(type(limit) is int and 1 <= limit <= PAGE_LIMIT and (after_id == '' or
             isinstance(after_id, str) and OPID.fullmatch(after_id)), 'store_page_invalid')
        generation = int(after_id[:16], 16) if after_id else 0
        rows = self.connection.execute('SELECT operation_id,classification,body,checksum FROM receipts WHERE classification=? AND generation>? ORDER BY generation LIMIT ?',
                                       (classification, generation, limit)).fetchall()
        return [self._terminal_row(row) for row in rows]

    def resolve_objects(self, values):
        """Convenience API; streaming collectors should use resolve_objects_page."""
        # Missing edges must not silently become "unowned". The read-session
        # audit is cached only inside its immutable SQLite read transaction.
        self.summary()
        need(isinstance(values, (list, tuple)) and len(values) <= PAGE_LIMIT, 'store_query_limit')
        result = {}
        for category, key in [_object_key(value) for value in values]:
            after_id = ''
            while True:
                page = self.resolve_objects_page(category, key, after_id)
                for envelope in page:
                    result[envelope['payload']['operation_id']] = envelope
                if len(page) < PAGE_LIMIT:
                    break
                after_id = page[-1]['payload']['operation_id']
        return [result[key] for key in sorted(result)]

    def resolve_objects_page(self, category, key, after_id='', limit=PAGE_LIMIT):
        """Return one indexed page, ordered by ID; last ID is the next cursor."""
        self.summary()
        _object_key((category, key))
        need(type(limit) is int and 1 <= limit <= PAGE_LIMIT and (after_id == '' or
             isinstance(after_id, str) and OPID.fullmatch(after_id)), 'store_page_invalid')
        rows = self.connection.execute('SELECT i.operation_id,i.receipt_checksum,i.checksum,r.operation_id,r.classification,r.body,r.checksum FROM object_index i LEFT JOIN receipts r ON r.operation_id=i.operation_id WHERE i.category=? AND i.object_key=? AND i.operation_id>? ORDER BY i.operation_id LIMIT ?',
                                       (category, key, after_id, limit)).fetchall()
        result = []
        for opid, receipt_checksum, index_checksum, *receipt in rows:
            need(index_checksum == digest([category, key, opid, receipt_checksum]) and receipt[0] == opid and
                 receipt[-1] == receipt_checksum, 'store_object_index_invalid')
            envelope = self._terminal_row(receipt)
            need([category, key] in envelope['object_keys'] and envelope['classification'] != 'C', 'store_object_index_invalid')
            result.append(envelope)
        return result

    def rollups(self, after_sha='', limit=PAGE_LIMIT):
        need(type(limit) is int and 1 <= limit <= PAGE_LIMIT and (after_sha == '' or
             isinstance(after_sha, str) and SHA.fullmatch(after_sha)), 'store_page_invalid')
        rows = self.connection.execute('SELECT target_sha,previous_sha,body,checksum FROM rollups WHERE target_sha>? ORDER BY target_sha LIMIT ?',
                                       (after_sha, limit)).fetchall()
        result = []
        for target, previous, body, checksum in rows:
            item = _rollup(body, checksum)
            need(item['target_sha'] == target and item['previous_sha'] == previous, 'store_rollup_key_invalid')
            result.append(item)
        return result


def read_session():
    """Stable readonly SQLite snapshot; missing or damaged stores fail closed."""
    return Store(readonly=True)
