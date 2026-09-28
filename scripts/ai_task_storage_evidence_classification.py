"""Pure lifecycle classification for durable evidence retention, never cleanup.

The fixed production frontend validates the complete terminal payload and makes
fresh no-follow filesystem/Docker observations under the deployment lock. This
module has no filesystem, Docker, clock, command or mutation capability. A C
summary is observation history only: it is never object ownership or permission
to clean anything. Legacy observations without image tags remain lossless A/B.
"""
import math
from pathlib import PurePosixPath
import re


SHA = re.compile(r'[0-9a-f]{40}\Z')
HEX = re.compile(r'[0-9a-f]{64}\Z')
IMAGE = re.compile(r'sha256:[0-9a-f]{64}\Z')
OPID = re.compile(r'[0-9a-f]{32}\Z')
NAME = re.compile(r'[A-Za-z0-9_.-]{1,180}\Z')
TAG = re.compile(r'[A-Za-z0-9_.:/@+-]{1,512}\Z')
PHASES = {'preflight', 'prepare', 'release', 'image', 'backup', 'migrate',
          'health', 'health_retry', 'reconcile', 'cleanup', 'ancillary_cleanup', 'rollback'}
ROOTS = {'prepare': '/home/ubuntu/ichiyon-releases',
         'release': '/home/ubuntu/ichiyon-releases',
         'backup': '/home/ubuntu/ichiyon-backups'}
RUNTIME_FIELDS = {'target_release', 'target_image', 'migration_container',
                  'image_ids', 'observation_complete'}
OBSERVATION_FIELDS = {'version', 'operation_id', 'observed_at', 'current_sha',
                      'references_complete', 'names', 'runtime', 'stages',
                      'health_verified', 'reconciliation_verified', 'cleanup_completed',
                      'ancillary_cleanup_verified'}
MAX_ITEMS = 8192


class ClassificationError(ValueError):
    """Malformed or unbound observations must never be interpreted as absence."""


def need(value, code):
    if not value:
        raise ClassificationError(code)


def matches(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def timestamp(value):
    return type(value) in (float, int) and 0 <= value < 10**12 and math.isfinite(value)


def identity(value):
    need(type(value) is dict and set(value) == {'device', 'inode', 'signature'},
         'object_identity_invalid')
    signature = value['signature']
    need(type(signature) is list and len(signature) == 7
         and all(type(item) is int and item >= 0 for item in signature)
         and type(value['device']) is int and type(value['inode']) is int
         and value['device'] == signature[0] and value['inode'] == signature[1]
         and value['inode'] > 0, 'object_identity_invalid')


def names(value):
    need(type(value) is dict and set(value) == {'releases', 'backups'}, 'object_names_invalid')
    for values in value.values():
        need(type(values) is list and len(values) <= MAX_ITEMS
             and all(matches(NAME, name) for name in values)
             and len(set(values)) == len(values), 'object_names_invalid')


def runtime(value, target):
    need(type(value) is dict and set(value) in (RUNTIME_FIELDS, RUNTIME_FIELDS | {'image_tags'}),
         'runtime_observation_invalid')
    need(type(value['observation_complete']) is bool, 'runtime_observation_invalid')
    identifiers = value['image_ids']
    need(type(identifiers) is list and len(identifiers) <= MAX_ITEMS
         and all(matches(IMAGE, item) for item in identifiers)
         and len(set(identifiers)) == len(identifiers), 'image_inventory_invalid')
    if value['target_release'] is not None:
        identity(value['target_release'])
    image = value['target_image']
    if image is not None:
        need(type(image) is dict and set(image) == {'id', 'revision'}
             and matches(IMAGE, image['id']) and image['revision'] == target,
             'target_image_invalid')
        if value['observation_complete']:
            need(image['id'] in identifiers, 'image_inventory_binding_invalid')
    migration = value['migration_container']
    if migration is not None:
        need(type(migration) is dict and set(migration) == {'id', 'image_id', 'status', 'name'}
             and matches(HEX, migration['id']) and matches(IMAGE, migration['image_id'])
             and migration['status'] in ('created', 'running', 'paused', 'restarting', 'removing', 'exited', 'dead')
             and migration['name'] == 'ichiyon-robot-migrate-' + target, 'migration_identity_invalid')
    if 'image_tags' in value:
        tags = value['image_tags']
        need(type(tags) is dict and len(tags) <= MAX_ITEMS
             and all(matches(TAG, tag)
                     and matches(IMAGE, image_id) for tag, image_id in tags.items()), 'image_tags_invalid')
        if value['observation_complete']:
            need(set(tags.values()) <= set(identifiers), 'image_tags_binding_invalid')
            expected = image['id'] if image is not None else None
            need(tags.get('ichiyon-robot-app:' + target) == expected, 'target_image_tag_binding_invalid')


def stage_path(kind, path, target):
    need(isinstance(kind, str) and kind in ROOTS and isinstance(path, str), 'stage_binding_invalid')
    parsed = PurePosixPath(path)
    need(str(parsed) == path and str(parsed.parent) == ROOTS[kind]
         and re.fullmatch(r'\.' + kind + '-' + target + r'\.[A-Za-z0-9]{8,32}', parsed.name),
         'stage_binding_invalid')
    return parsed.name


def checked_inputs(payload, observation):
    need(type(payload) is dict and matches(OPID, payload.get('operation_id'))
         and matches(SHA, payload.get('target_sha')) and matches(SHA, payload.get('previous_sha')),
         'operation_identity_invalid')
    need(payload.get('state') in ('succeeded', 'failed', 'cancelled'), 'operation_not_terminal')
    need(payload.get('rollback_result') in ('not_needed', 'not_attempted', 'succeeded', 'failed', 'unknown'),
         'rollback_result_invalid')
    need(timestamp(payload.get('created_at')) and timestamp(payload.get('completed_at'))
         and payload['completed_at'] >= payload['created_at'], 'operation_time_invalid')
    need(type(observation) is dict and set(observation) == OBSERVATION_FIELDS
         and type(observation['version']) is int and observation['version'] == 1
         and observation['operation_id'] == payload['operation_id']
         and timestamp(observation['observed_at']) and observation['observed_at'] >= payload['completed_at'],
         'final_observation_unbound')
    need(observation['current_sha'] is None or matches(SHA, observation['current_sha']),
         'current_sha_invalid')
    for name in ('references_complete', 'health_verified', 'reconciliation_verified', 'cleanup_completed',
                 'ancillary_cleanup_verified'):
        need(type(observation[name]) is bool, 'observation_flag_invalid')
    names(payload.get('initial_names'))
    names(observation['names'])
    for value in (payload.get('initial_runtime'), payload.get('runtime'), observation['runtime']):
        runtime(value, payload['target_sha'])
    stages = payload.get('staging')
    need(type(stages) is list and len(stages) <= 3, 'stage_binding_invalid')
    recorded = {}
    for item in stages:
        need(type(item) is dict and set(item) == {'kind', 'path', 'created_identity', 'created_at'},
             'stage_binding_invalid')
        stage_path(item['kind'], item['path'], payload['target_sha'])
        identity(item['created_identity'])
        need(timestamp(item['created_at']) and payload['created_at'] <= item['created_at'] <= payload['completed_at']
             and item['path'] not in recorded and item['kind'] not in {s['kind'] for s in recorded.values()},
             'stage_binding_invalid')
        recorded[item['path']] = item
    observed = {}
    need(type(observation['stages']) is list and len(observation['stages']) <= 3,
         'stage_observation_invalid')
    for item in observation['stages']:
        need(type(item) is dict and set(item) == {'kind', 'path', 'status', 'identity'}
             and item['status'] in ('absent', 'present', 'unknown'), 'stage_observation_invalid')
        stage_path(item['kind'], item['path'], payload['target_sha'])
        need(item['path'] in recorded and recorded[item['path']]['kind'] == item['kind']
             and item['path'] not in observed, 'stage_observation_unbound')
        if item['status'] == 'present':
            identity(item['identity'])
        else:
            need(item['identity'] is None, 'stage_observation_invalid')
        observed[item['path']] = item
    need(set(recorded) == set(observed), 'stage_observation_unbound')
    events = payload.get('events')
    need(type(events) is list and len(events) <= 32
         and all(type(item) is dict and set(item) == {'phase', 'at'}
                 and isinstance(item['phase'], str) and item['phase'] in PHASES and timestamp(item['at'])
                 and payload['created_at'] <= item['at'] <= payload['completed_at'] for item in events),
         'phase_observation_invalid')
    need(type(payload.get('omitted_event_count')) is int and payload['omitted_event_count'] >= 0,
         'phase_observation_invalid')
    return recorded, observed


def object_index(payload, observation, recorded):
    """Potentially owned keys need lossless receipt lookup, even after absence.

    An index hit never asserts ownership: the collector still verifies identity,
    operation binding and current references before deciding cleanup eligibility.
    """
    keys = set()
    target = payload['target_sha']
    for item in recorded.values():
        keys.add(('staging', stage_path(item['kind'], item['path'], target)))
        if item['kind'] in ('release', 'backup'):
            keys.add(('releases' if item['kind'] == 'release' else 'backups', target))
    initial = payload['initial_runtime']
    if initial['observation_complete']:
        for final in (payload['runtime'], observation['runtime']):
            if initial['target_release'] is None and final['target_release'] is not None:
                keys.add(('releases', target))
            image = final['target_image']
            if image is not None and image['id'] not in initial['image_ids']:
                keys.add(('images', image['id']))
            migration = final['migration_container']
            old = initial['migration_container']
            if migration is not None and (old is None or old['id'] != migration['id']):
                keys.add(('migration', migration['id']))
    if (observation['references_complete'] and target not in payload['initial_names']['backups']
            and target in observation['names']['backups']):
        keys.add(('backups', target))
    return sorted(keys)


def classify(payload, final_observation):
    """Return A object-bearing, B incident/unproved, or C proved reconciliation.

    Call only on a fully validated terminal payload. Incomplete observations are
    B and remain lossless; malformed observations raise a fixed diagnostic code.
    C returns no object keys and cannot be converted into cleanup authority.
    """
    recorded, observed = checked_inputs(payload, final_observation)
    initial, final = payload['initial_runtime'], final_observation['runtime']
    keys = object_index(payload, final_observation, recorded)

    def result(kind, reasons):
        return dict(classification=kind, reasons=reasons,
                    noop_proof={'persistent_absence_verified': True, 'health_verified': True,
                                'cleanup_verified': True} if kind == 'C' else None,
                    object_keys=[] if kind == 'C' else keys)

    if (payload['state'] != 'succeeded' or payload['rollback_result'] != 'not_needed'
            or any(event['phase'] == 'rollback' for event in payload['events'])):
        return result('B', ['FAILED_CANCELLED_OR_ROLLBACK_OPERATION'])
    if any(event['phase'] == 'health_retry' for event in payload['events']):
        return result('B', ['HEALTH_FAILURE_OBSERVED'])
    complete = (initial['observation_complete'] and final['observation_complete']
                and payload['runtime']['observation_complete'] and final_observation['references_complete'])
    if not complete or any(item['status'] == 'unknown' for item in observed.values()):
        return result('B', ['PERSISTENT_OBJECT_ABSENCE_UNPROVED'])
    if payload['target_sha'] != payload['previous_sha']:
        return result('A', ['SUCCESSFUL_DEPLOYMENT_RETAINS_OBJECT_EVIDENCE'])
    if any(item['status'] == 'present' for item in observed.values()) or final['migration_container'] is not None:
        return result('A', ['PERSISTENT_STAGE_OR_MIGRATION_REMAINS'])
    if any(set(final_observation['names'][category]) - set(payload['initial_names'][category])
           for category in ('releases', 'backups')):
        return result('A', ['PERSISTENT_GENERATION_NAMES_ADDED'])
    if set(final['image_ids']) - set(initial['image_ids']):
        return result('A', ['PERSISTENT_DOCKER_IMAGE_ADDED'])
    if ('image_tags' not in initial or 'image_tags' not in final
            or 'image_tags' not in payload['runtime']):
        return result('B', ['DOCKER_TAG_ABSENCE_UNPROVED'])
    if final['image_tags'] != initial['image_tags']:
        return result('A', ['PERSISTENT_DOCKER_TAG_BINDING_CHANGED'])
    if initial['target_release'] is None or final['target_release'] is None:
        return result('B', ['CURRENT_RELEASE_IDENTITY_UNPROVED'])
    if initial['target_image'] is None or final['target_image'] is None:
        return result('B', ['CURRENT_IMAGE_IDENTITY_UNPROVED'])
    if final['target_release'] != initial['target_release'] or final['target_image'] != initial['target_image']:
        return result('A', ['PERSISTENT_TARGET_IDENTITY_CHANGED'])
    if (any(set(final_observation['names'][category]) != set(payload['initial_names'][category])
            for category in ('releases', 'backups')) or set(final['image_ids']) != set(initial['image_ids'])):
        return result('B', ['UNEXPECTED_PERSISTENT_INVENTORY_CHANGE'])
    for key in ('target_release', 'target_image', 'image_ids', 'image_tags'):
        if final[key] != payload['runtime'][key]:
            return result('B', ['TERMINAL_RUNTIME_NOT_FINAL_OBSERVATION'])
    if (any(item['kind'] != 'prepare' for item in recorded.values())
            or any(event['phase'] in ('release', 'backup', 'image', 'migrate') for event in payload['events'])):
        # Reconciliation never enters these creation paths. Final Docker image
        # IDs do not cover build cache or every transient publication artifact,
        # so matching final inventories alone cannot prove such work was a no-op.
        return result('B', ['NON_RECONCILIATION_CREATION_PATH_OBSERVED'])
    phases = [event['phase'] for event in payload['events']]
    times = [event['at'] for event in payload['events']]
    if times != sorted(times):
        return result('B', ['RECONCILIATION_HEALTH_CLEANUP_ORDER_UNPROVED'])
    try:
        reconcile = phases.index('reconcile')
        health = phases.index('health', reconcile + 1)
        cleanup = phases.index('cleanup', health + 1)
        phases.index('ancillary_cleanup', cleanup + 1)
    except ValueError:
        return result('B', ['RECONCILIATION_HEALTH_CLEANUP_ORDER_UNPROVED'])
    if (payload['omitted_event_count'] or payload.get('phase') != 'ancillary_cleanup'
            or final_observation['current_sha'] != payload['target_sha']
            or not final_observation['health_verified'] or not final_observation['reconciliation_verified']
            or not final_observation['cleanup_completed'] or not final_observation['ancillary_cleanup_verified']):
        return result('B', ['RECONCILIATION_HEALTH_CLEANUP_UNPROVED'])
    return result('C', ['SUCCESSFUL_RECONCILIATION_WITH_NO_PERSISTENT_OBJECT'])
