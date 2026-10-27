"""Patch ONLY the 17 existing JUNKET catalog block entities in an offline world.

No terrain, button, entity, player or protected return control is written.
Callers must stop the server and take the full world backup before --apply.
The rollback file additionally preserves every changed raw LevelDB value.
"""
from __future__ import annotations

import argparse
import base64
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
import math
from pathlib import Path
import struct

import nbtlib as n

from build_junket_warp import (
    EXPECTED_IDS, build_plan, legacy_warp_command, load, script_load_command,
)
from junket_structure import ConvertedWorld


def digest(value):
    return sha256(value).hexdigest()


def chunk_key(position):
    return struct.pack('<ii', position[0] // 16, position[2] // 16) + b'1'


def parse_records(raw):
    """Keep untouched compound byte slices; no lossy NBT round trip."""
    stream = BytesIO(raw)
    records = []
    while stream.tell() < len(raw):
        start = stream.tell()
        root = n.File.parse(stream, byteorder='little')
        position = tuple(int(root[k]) for k in ('x', 'y', 'z'))
        records.append((position, root, raw[start:stream.tell()]))
    if len({at for at, _, _ in records}) != len(records):
        raise ValueError('duplicate block entity position')
    return records


def encode_record(root):
    stream = BytesIO()
    root.write(stream, byteorder='little')
    return stream.getvalue()


def normalize_block(block):
    return {'name': str(block['name']),
            'states': {key: value.unpack() for key, value in block.get('states', {}).items()}}


def native_plan(layouts, catalog=None):
    """Use the recorded production origins, never choose a new venue position."""
    plan = build_plan(catalog or load())
    layouts = {item['id']: item for item in layouts['games']}
    if set(layouts) != set(EXPECTED_IDS):
        raise ValueError('source layout must contain exactly 17 game IDs')
    for index, item in enumerate(plan['placements']):
        source = layouts[item['id']]
        if list(source['production_origin']) != [20000 + 96 * index, 65, 20000]:
            raise ValueError(f"recorded production origin mismatch: {item['id']}")
        destination = source['destination']
        if isinstance(destination, dict):
            destination = [destination[axis] for axis in ('x', 'y', 'z')]
        if (len(destination) != 3 or
                any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in destination)):
            raise ValueError(f"invalid destination: {item['id']}")
        item['destination'] = destination
        item['production_origin'] = list(source['production_origin'])
        item['command_mode'] = 'script_load'
        # The script retains the exact presser and loads/checks the destination
        # before teleporting. A nearby-player selector cannot identify a presser
        # and direct /tp ... true can reject an unloaded destination.
        item['command'] = script_load_command(item['id'])
    plan['offline_installed'] = True
    # The recorded home control is historical metadata. A later world may have
    # moved or removed it; preserve the observed cells, including absent NBT,
    # rather than reconstructing an old facility during a 17-slot update.
    plan['protected_anchor_mode'] = 'preserve_observed'
    return plan


def previous_native_tp_command(item):
    """Only recognize the exact prior command for this fixed slot/destination."""
    x, y, z = item['button']
    coordinates = ' '.join(format(v, 'g') for v in item['destination'])
    return f'tp @p[x={x},y={y},z={z},r=4] {coordinates} true'


def block_at(world, position):
    layers = world.blocks(*position)
    if not layers:
        raise ValueError(f'unloaded/missing chunk at {position}')
    return normalize_block(layers[0])


def require_block(world, at, name, states, *, allow_button_press=False):
    actual = block_at(world, at)
    expected = dict(states)
    if allow_button_press:
        # A stopped snapshot may retain a legally pressed button. This is
        # dynamic state, not a placement difference; never write its palette.
        if actual['states'].get('button_pressed_bit') not in (0, 1):
            raise ValueError(f'catalog button press state invalid at {at}: {actual}')
        expected.pop('button_pressed_bit', None)
    if actual['name'] != name or any(actual['states'].get(k) != v for k, v in expected.items()):
        raise ValueError(f'catalog block differs at {at}: {actual}')
    return actual


def snapshot(world, plan):
    """Inspect palette + NBT + direct physical button wiring before any write."""
    keys = {chunk_key(item[role]) for item in plan['placements']
            for role in ('command_block', 'sign')}
    anchor = plan['protected_anchor']
    keys.update(chunk_key(anchor[role]) for role in ('command_block', 'sign', 'button'))
    raw_values = {}
    records = {}
    for key in keys:
        raw = world.get(key)
        if raw is None:
            raise ValueError(f'missing block entity chunk {key.hex()}')
        raw_values[key] = raw
        for at, root, raw_record in parse_records(raw):
            records[at] = (root, raw_record)
    proof = []
    for item in plan['placements']:
        for role in ('command_block', 'sign', 'button'):
            require_block(world, item[role], item[f'{role}_type'], item[f'{role}_states'],
                          allow_button_press=role == 'button')
        command_at, sign_at, button_at = (tuple(item[k]) for k in ('command_block', 'sign', 'button'))
        if button_at != (command_at[0], command_at[1] + 1, command_at[2]):
            raise ValueError('button is not directly on its command block')
        for at, type_id in ((command_at, 'CommandBlock'), (sign_at, 'Sign')):
            if at not in records or str(records[at][0].get('id')) != type_id:
                raise ValueError(f'missing/wrong block entity at {at}')
        command_root = records[command_at][0]
        sign_root = records[sign_at][0]
        # Never replace an unrelated use of one of the catalog slots.
        text = str(sign_root.get('FrontText', {}).get('Text', sign_root.get('Text', '')))
        if text.strip() and item['title'] not in text:
            raise ValueError(f"catalog sign belongs to another use: {item['id']} / {text}")
        command = str(command_root.get('Command', ''))
        if item.get('command_mode') == 'script_load':
            accepted = {'', item['command'], previous_native_tp_command(item),
                        legacy_warp_command(item['id'], item['button'])}
            owned_command = command in accepted
        else:
            owned_command = not command or command == item['command'] or 'junket:warp' in command
        if not owned_command:
            raise ValueError(f"catalog command belongs to another use: {item['id']}")
        proof.append({'id': item['id'], 'title': item['title'],
                      'command_block': item['command_block'], 'sign': item['sign'],
                      'button': item['button'], 'wiring': 'top-mounted button directly powers impulse block',
                      'command': command, 'sign_text': text})
    protected = []
    preserve_observed = plan.get('protected_anchor_mode') == 'preserve_observed'
    for role in ('command_block', 'sign', 'button'):
        at = tuple(anchor[role])
        block = (block_at(world, at) if preserve_observed else
                 require_block(world, at, anchor[f'{role}_type'], anchor[f'{role}_states']))
        raw = records[at][1] if at in records else b''
        expected = {'name': anchor[f'{role}_type'], 'states': anchor[f'{role}_states']}
        matches = (block['name'] == expected['name'] and
                   all(block['states'].get(k) == v for k, v in expected['states'].items()))
        observed = {}
        if role == 'command_block':
            expected['command'] = anchor['command']
            observed['command'] = str(records[at][0].get('Command', '')) if at in records else None
            matches = matches and observed['command'] == anchor['command']
        elif role == 'sign':
            root = records[at][0] if at in records else {}
            observed['sign_text'] = str(root.get('FrontText', {}).get('Text', root.get('Text', '')))
            expected['nonblank_sign'] = True
            matches = matches and bool(observed['sign_text'].strip())
        protected.append({'role': role, 'position': list(at), 'block': block,
                          'block_layers': [normalize_block(layer) for layer in world.blocks(*at)],
                          'block_entity_present': at in records,
                          'block_entity_sha256': digest(raw) if raw else None,
                          'historical_expected': expected,
                          'historical_expected_matches': matches, **observed})
    if not preserve_observed:
        protected_command = records.get(tuple(anchor['command_block']), ({},))[0]
        if str(protected_command.get('Command')) != anchor['command']:
            raise ValueError('protected return command mismatch')
        protected_sign = records.get(tuple(anchor['sign']), ({},))[0]
        text = str(protected_sign.get('FrontText', {}).get('Text', protected_sign.get('Text', '')))
        if not text.strip():
            raise ValueError('protected return sign is blank')
    return {'raw_values': raw_values, 'records': records, 'protected': protected, 'slots': proof}


def command_record(root, item):
    root = deepcopy(root)
    root['Command'] = n.String(item['command'])
    root['CustomName'] = n.String('JUNKET ' + item['title'])
    for key in ('auto', 'powered', 'ExecuteOnFirstTick', 'LPCondionalMode', 'LPRedstoneMode'):
        root[key] = n.Byte(0)
    root['conditionMet'] = n.Byte(1)
    root['LPCommandMode'] = n.Int(0)
    root['TickDelay'] = n.Int(0)
    return root


def sign_record(root, item):
    root = deepcopy(root)
    for side in ('FrontText', 'BackText'):
        if side not in root:
            root[side] = n.Compound()
        root[side]['Text'] = n.String(item['title'])
        root[side]['FilteredText'] = n.String('')
        root[side]['TextOwner'] = n.String('')
    if 'Text' in root:
        root['Text'] = n.String(item['title'])
    return root


def prepare_patch(world, plan, *, command_only=False):
    before = snapshot(world, plan)
    updates = {}
    for item in plan['placements']:
        if command_only:
            sign = before['records'][tuple(item['sign'])][0]
            if any(str(sign.get(side, {}).get('Text', '')) != item['title']
                   for side in ('FrontText', 'BackText')):
                raise ValueError(f"command-only patch requires existing formal sign: {item['id']}")
        editors = [('command_block', command_record)]
        if not command_only:
            editors.append(('sign', sign_record))
        for role, editor in editors:
            at = tuple(item[role])
            updates[at] = encode_record(editor(before['records'][at][0], item))
    expected = 17 if command_only else 34
    if len(updates) != expected:
        raise ValueError(f'patch must contain exactly {expected} existing block entities')
    values = {}
    untouched = {}
    for key, raw in before['raw_values'].items():
        encoded = []
        for at, _, raw_record in parse_records(raw):
            encoded.append(updates.get(at, raw_record))
            if at not in updates:
                untouched[at] = digest(raw_record)
        new_raw = b''.join(encoded)
        if new_raw != raw:
            values[key] = new_raw
    return before, values, untouched


def verify_patch(world, plan, before=None, untouched=None):
    after = snapshot(world, plan)
    for item, slot in zip(plan['placements'], after['slots']):
        if slot['command'] != item['command'] or slot['sign_text'] != item['title']:
            raise ValueError(f"persisted catalog mismatch: {item['id']}")
        root = after['records'][tuple(item['command_block'])][0]
        for key in ('auto', 'powered', 'LPCommandMode', 'LPCondionalMode', 'LPRedstoneMode', 'TickDelay'):
            if int(root.get(key, -1)) != 0:
                raise ValueError(f"impulse/redstone mode invalid: {item['id']} / {key}")
        sign = after['records'][tuple(item['sign'])][0]
        if str(sign['BackText']['Text']) != item['title']:
            raise ValueError(f"back sign text mismatch: {item['id']}")
        slot['destination'] = item['destination']
        slot['command_mode'] = item.get('command_mode', 'legacy')
        slot['verified'] = True
    if before and before['protected'] != after['protected']:
        raise ValueError('protected return equipment changed')
    if untouched:
        for at, expected in untouched.items():
            if at not in after['records'] or digest(after['records'][at][1]) != expected:
                raise ValueError(f'unrelated block entity changed: {at}')
    return {'verified': True, 'slots': after['slots'], 'protected': after['protected'],
            'protected_anchor_mode': plan.get('protected_anchor_mode', 'strict'),
            'historical_anchor_matches': all(item['historical_expected_matches'] for item in after['protected']),
            'anchor_missing': any(item['block']['name'] == 'minecraft:air' for item in after['protected']),
            'protected_unchanged': before is not None,
            'unrelated_block_entities_unchanged': len(untouched or {}),
            'button_palette_written': False, 'terrain_written': False,
            'gameplay_verified': False}


def apply_patch(world, plan, rollback_path, *, command_only=False):
    before, values, untouched = prepare_patch(world, plan, command_only=command_only)
    before_keys = {key: digest(value) for key, value in world.db.items()}
    rollback_path = Path(rollback_path)
    rollback_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {'schema': 1, 'command_only': command_only, 'protected': before['protected'],
               'records': [{'key': key.hex(), 'sha256': digest(before['raw_values'][key]),
                            'base64': base64.b64encode(before['raw_values'][key]).decode('ascii')}
                           for key in values]}
    with rollback_path.open('x', encoding='utf-8') as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
    # Amulet LevelDB putBatch is one atomic write batch. No other keys are touched.
    if values:
        world.db.putBatch(values)
    report = verify_patch(world, plan, before, untouched)
    after_keys = {key: digest(value) for key, value in world.db.items()}
    if before_keys.keys() != after_keys.keys():
        raise ValueError('catalog patch added or removed unrelated LevelDB keys')
    changed = {key for key in before_keys if before_keys[key] != after_keys[key]}
    if changed != set(values):
        raise ValueError('catalog patch changed unexpected LevelDB keys')
    changed_records = sum(
        raw != before['records'][at][1]
        for value in values.values() for at, _, raw in parse_records(value))
    report.update({'changed_leveldb_keys': [key.hex() for key in values],
                   'unrelated_leveldb_keys_unchanged': len(before_keys) - len(values),
                   'all_leveldb_keys_accounted_for': True,
                   'changed_block_entities': changed_records,
                   'command_only': command_only,
                   'sign_block_entities_written': not command_only and bool(values),
                   'rollback_file': str(rollback_path)})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--world', type=Path, required=True)
    parser.add_argument('--layouts', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--command-only', action='store_true',
                        help='preserve all 17 existing sign records byte-for-byte')
    parser.add_argument('--world-is-stopped', action='store_true')
    parser.add_argument('--full-backup', type=Path)
    parser.add_argument('--rollback', type=Path)
    args = parser.parse_args()
    plan = native_plan(json.loads(args.layouts.read_text(encoding='utf-8')))
    if args.plan:
        args.plan.parent.mkdir(parents=True, exist_ok=True)
        args.plan.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
    if args.apply and (not args.world_is_stopped or not args.full_backup or
                       not args.full_backup.exists() or not args.rollback):
        parser.error('--apply requires --world-is-stopped, --full-backup and a new --rollback file')
    world = ConvertedWorld(args.world)
    try:
        if args.apply:
            report = apply_patch(world, plan, args.rollback, command_only=args.command_only)
        else:
            report = verify_patch(world, plan)
    finally:
        world.db.close()
    report['world'] = str(args.world.resolve())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'verified': report['verified'], 'slots': len(report['slots']),
                      'report': str(args.report)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
