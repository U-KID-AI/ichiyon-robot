"""Generate the shared 17-game JUNKET warp catalog and installation plan.

The checked-in JSON is the source of truth. Runtime JavaScript and the private
world-control plan are generated from it. This script never writes a live world.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'scripts/minecraft/junket_games.json'
RUNTIME = ROOT / 'minecraft/behavior_packs/ichiyon_junket_bp/scripts/junket_warp_generated.js'
PRIVATE = ROOT / 'data/junket-private/world-controls'
PLAN = PRIVATE / 'junket-warp-plan.json'
FUNCTION = PRIVATE / 'junket-warp-install.mcfunction'
STRUCTURES = ROOT / 'minecraft/behavior_packs/ichiyon_junket_bp/structures/junket'
OBSOLETE_STRUCTURE = STRUCTURES / 'warp_row.mcstructure'
EXPECTED_IDS = [
    'sparrow', 'temperance', 'lucy', 'silence', 'jinn', 'ace', 'grail',
    'hope', 'auction', 'fire', 'pig', 'light', 'mine', 'clock', 'walker',
    'garden', 'rabbit',
]
READINESS = {'verified', 'implemented', 'venue-review', 'in-progress', 'not-implemented'}
COMPLETION_STATES = {
    'source_spec': {'verified', 'partial', 'not_verified'},
    'venue_controls': {'verified', 'partial', 'placeholder', 'not_started'},
    'playthrough': {'verified', 'not_verified'},
    'production_deployment': {'verified', 'not_verified'},
}
EXPECTED_ROW = {
    'first_command_block': [10048, 65, 10102],
    'axis': 'z',
    'stride': -2,
    'sign_offset': [1, 0, 0],
    'button_offset': [0, 1, 0],
    'slot_order': 'catalog_order_from_corridor_entrance_descending_z',
}
EXPECTED_ANCHOR = {
    'command_block': [10051, 65, 10077],
    'sign': [10051, 65, 10076],
    'button': [10051, 66, 10077],
    'command': 'tp @p -44 75 210',
}
EXPECTED_MISPLACED_CELLS = 53


def load(path: Path = SOURCE) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding='utf-8'))
    if value.get('schema') != 1:
        raise ValueError('unsupported catalog schema')
    games = value.get('games')
    if not isinstance(games, list) or [game.get('id') for game in games] != EXPECTED_IDS:
        raise ValueError('game order or IDs differ from the verified ver1.09 inventory')
    titles = [game.get('title') for game in games]
    if any(not isinstance(title, str) or not title.strip() for title in titles) or len(set(titles)) != 17:
        raise ValueError('game titles must be 17 unique non-empty strings')
    for game in games:
        if game.get('readiness') not in READINESS:
            raise ValueError(f"invalid readiness for {game['id']}")
        if game.get('enabled') is not True:
            raise ValueError(f"JUNKET test venue must declare explicit enabled=true: {game['id']}")
        if game.get('player_mode') not in ('fixed', 'registration'):
            raise ValueError(f"invalid player mode for {game['id']}")
        completion = game.get('completion')
        if not isinstance(completion, dict):
            raise ValueError(f"missing completion matrix for {game['id']}")
        for field, allowed in COMPLETION_STATES.items():
            if completion.get(field) not in allowed:
                raise ValueError(f"invalid completion.{field} for {game['id']}")
        minimum = game.get('min_players')
        maximum = game.get('max_players')
        if not isinstance(minimum, int) or minimum < 1:
            raise ValueError(f"invalid minimum players for {game['id']}")
        if maximum is not None and (not isinstance(maximum, int) or maximum < minimum):
            raise ValueError(f"invalid maximum players for {game['id']}")
        arrival = game.get('source_arrival')
        if not isinstance(arrival, list) or len(arrival) != 3 or not all(isinstance(v, (int, float)) for v in arrival):
            raise ValueError(f"invalid arrival for {game['id']}")
    controls = value.get('controls')
    if not isinstance(controls, dict):
        raise ValueError('missing production warp controls')
    if controls.get('row') != EXPECTED_ROW:
        raise ValueError('warp row no longer matches the verified white corridor slots')
    anchor = controls.get('protected_anchor')
    if not isinstance(anchor, dict) or any(anchor.get(key) != expected for key, expected in EXPECTED_ANCHOR.items()):
        raise ValueError('protected 拠点行き anchor changed')
    expected_types = {
        'command_block_type': 'minecraft:command_block',
        'sign_type': 'minecraft:acacia_wall_sign',
        'button_type': 'minecraft:acacia_button',
    }
    for key, expected in expected_types.items():
        if controls.get(key) != expected:
            raise ValueError(f'invalid corridor control type: {key}')
    if controls.get('command_block_states', {}).get('facing_direction') != 5:
        raise ValueError('corridor command blocks must keep facing_direction=5')
    if controls.get('sign_states', {}).get('facing_direction') != 5:
        raise ValueError('corridor wall signs must keep facing_direction=5')
    if controls.get('button_states', {}).get('facing_direction') != 1:
        raise ValueError('corridor buttons must keep facing_direction=1')
    return value


def row_position(controls: dict[str, Any], index: int) -> list[int]:
    row = controls['row']
    first = row['first_command_block']
    result = list(first)
    axis = row['axis']
    if axis not in ('x', 'z'):
        raise ValueError('row axis must be x or z')
    result[0 if axis == 'x' else 2] += int(row['stride']) * index
    return result


def offset(location: list[int], delta: list[int]) -> list[int]:
    return [value + change for value, change in zip(location, delta)]


def cleanup_cells(controls: dict[str, Any]) -> list[dict[str, Any]]:
    cleanup = controls.get('misplaced_row_cleanup')
    if not isinstance(cleanup, dict):
        raise ValueError('missing guarded cleanup for the misplaced JUNKET row')
    bounds = cleanup.get('scan_bounds')
    if not isinstance(bounds, list) or len(bounds) != 6 or not all(isinstance(v, int) for v in bounds):
        raise ValueError('invalid misplaced-row scan bounds')
    cells: list[dict[str, Any]] = []
    seen: set[tuple[int, int, int]] = set()
    for rule in cleanup.get('rules', []):
        xs = rule.get('x')
        y = rule.get('y')
        z = rule.get('z')
        current_type = rule.get('current_type')
        restore_type = rule.get('restore_type')
        if (not isinstance(xs, list) or not xs or not all(isinstance(x, int) for x in xs) or
                not isinstance(y, int) or not isinstance(z, int) or
                not isinstance(current_type, str) or not isinstance(restore_type, str)):
            raise ValueError('invalid misplaced-row cleanup rule')
        for x in xs:
            at = (x, y, z)
            if at in seen:
                raise ValueError(f'duplicate misplaced-row cleanup cell: {at}')
            if not (bounds[0] <= x <= bounds[3] and bounds[1] <= y <= bounds[4] and bounds[2] <= z <= bounds[5]):
                raise ValueError(f'misplaced-row cleanup cell outside guarded bounds: {at}')
            seen.add(at)
            cells.append({
                'location': [x, y, z],
                'current_type': current_type,
                'restore_type': restore_type,
            })
    if len(cells) != EXPECTED_MISPLACED_CELLS:
        raise ValueError(f'misplaced-row cleanup must cover exactly {EXPECTED_MISPLACED_CELLS} cells')
    protected = {
        tuple(controls['protected_anchor']['command_block']),
        tuple(controls['protected_anchor']['sign']),
        tuple(controls['protected_anchor']['button']),
    }
    if seen.intersection(protected):
        raise ValueError('misplaced-row cleanup overlaps the protected 拠点行き anchor')
    entity_tags = cleanup.get('entity_tags')
    if not isinstance(entity_tags, list) or not entity_tags:
        raise ValueError('misplaced-row cleanup must declare guarded entity tags')
    entity_bounds = cleanup.get('entity_bounds')
    if (not isinstance(entity_bounds, list) or len(entity_bounds) != 6 or
            not all(isinstance(v, int) for v in entity_bounds)):
        raise ValueError('invalid misplaced-row entity bounds')
    if not (entity_bounds[0] <= bounds[0] and entity_bounds[1] <= bounds[1] and
            entity_bounds[2] <= bounds[2] and entity_bounds[3] >= bounds[3] and
            entity_bounds[4] >= bounds[4] and entity_bounds[5] >= bounds[5]):
        raise ValueError('misplaced-row entity bounds must contain all block cleanup cells')
    return cells


def script_load_command(game_id: str) -> str:
    """The runtime captures the exact button presser before this block event."""
    if game_id not in EXPECTED_IDS:
        raise ValueError(f'unknown JUNKET warp game: {game_id}')
    return f'scriptevent junket:warp {game_id}'


def legacy_warp_command(game_id: str, button: list[int]) -> str:
    return (f'execute as @a[tag=junket:warp_{game_id},'
            f'x={button[0]},y={button[1]},z={button[2]},r=4] '
            f'run scriptevent junket:warp {game_id}')


def build_plan(catalog: dict[str, Any]) -> dict[str, Any]:
    controls = catalog['controls']
    misplaced_cells = cleanup_cells(controls)
    placements = []
    occupied = set()
    for index, game in enumerate(catalog['games']):
        command = row_position(controls, index)
        sign = offset(command, controls['row']['sign_offset'])
        button = offset(command, controls['row']['button_offset'])
        cells = [tuple(command), tuple(sign), tuple(button)]
        if len(set(cells)) != 3 or occupied.intersection(cells):
            raise ValueError(f"overlapping control placement for {game['id']}")
        occupied.update(cells)
        placements.append({
            'id': game['id'],
            'title': game['title'],
            'readiness': game['readiness'],
            'enabled': game['enabled'],
            'command_block': command,
            'sign': sign,
            'button': button,
            'command_block_type': controls['command_block_type'],
            'command_block_states': controls['command_block_states'],
            'sign_type': controls['sign_type'],
            'sign_states': controls['sign_states'],
            'button_type': controls['button_type'],
            'button_states': controls['button_states'],
            'command': legacy_warp_command(game['id'], button),
        })
    anchor = controls['protected_anchor']
    protected = {
        tuple(anchor['command_block']),
        tuple(anchor['sign']),
        tuple(anchor['button']),
    }
    if occupied.intersection(protected):
        raise ValueError('generated controls overlap the protected 拠点行き anchor')
    return {
        'schema': 1,
        'control_schema': 3,
        'source': catalog['source'],
        'dimension': controls['dimension'],
        'protected_anchor': anchor,
        'return_point': controls['return_point'],
        'row': controls['row'],
        'placements': placements,
        'misplaced_row': {
            'scan_bounds': controls['misplaced_row_cleanup']['scan_bounds'],
            'entity_bounds': controls['misplaced_row_cleanup']['entity_bounds'],
            'cells': misplaced_cells,
            'entity_tags': controls['misplaced_row_cleanup']['entity_tags'],
        },
    }


def runtime_source(plan: dict[str, Any]) -> str:
    data = {
        'WARP_DIMENSION': plan['dimension'],
        'WARP_RETURN_POINT': plan['return_point'],
        'PROTECTED_ANCHOR': plan['protected_anchor'],
        'WARP_ROW': plan['row'],
        'WARP_CONTROL_SCHEMA': plan['control_schema'],
        'WARP_CONTROLS': plan['placements'],
        'MISPLACED_ROW_CLEANUP': plan['misplaced_row'],
    }
    if plan.get('offline_installed'):
        data['WARP_OFFLINE_INSTALLED'] = True
        if plan.get('protected_anchor_mode') == 'preserve_observed':
            data['WARP_PRESERVE_OBSERVED_ANCHOR'] = True
    body = json.dumps(data, ensure_ascii=False, indent=2)
    return (
        '// Generated by scripts/minecraft/build_junket_warp.py. Do not edit by hand.\n'
        f'export const WARP_CONFIG = Object.freeze({body});\n'
    )


def function_source(plan: dict[str, Any]) -> str:
    lines = [
        '# Generated review/install function for the existing JUNKET corridor controls.',
        '# The 17 command blocks, wall signs and buttons already exist in the white corridor.',
        '# This function only updates block-entity data; it never places or removes buttons.',
        f"# Protected anchor: {plan['protected_anchor']['label']} "
        f"{plan['protected_anchor']['command_block']}",
        '# Script API performs the authoritative 51-cell preflight, retries and verification.',
    ]
    for item in plan['placements']:
        x, y, z = item['command_block']
        sx, sy, sz = item['sign']
        command = json.dumps({'Command': item['command'], 'CustomName': f"JUNKET {item['title']}"}, ensure_ascii=False)
        sign = json.dumps({'FrontText': {'Text': item['title'], 'TextOwner': 'JUNKET'}}, ensure_ascii=False)
        lines.extend([
            f"# {item['id']} / {item['title']}",
            f'# Existing command block: ({x} {y} {z}) {item["command_block_type"]} {item["command_block_states"]}',
            f'blockdata {x} {y} {z} {command}',
            f'# Existing wall sign: ({sx} {sy} {sz}) {item["sign_type"]} {item["sign_states"]}',
            f'blockdata {sx} {sy} {sz} {sign}',
        ])
    return '\n'.join(lines) + '\n'


def expected_outputs(catalog: dict[str, Any] | None = None) -> dict[Path, str]:
    plan = build_plan(catalog or load())
    outputs: dict[Path, str | bytes] = {
        RUNTIME: runtime_source(plan),
        PLAN: json.dumps(plan, ensure_ascii=False, indent=2) + '\n',
        FUNCTION: function_source(plan),
    }
    return outputs


def check(outputs: dict[Path, str], include_private: bool = False) -> None:
    changed = []
    for path, text in outputs.items():
        # Private world plans are absent or read-only in many CI/local sandboxes.
        if path.is_relative_to(PRIVATE) and not include_private:
            continue
        current = path.read_bytes() if path.exists() else None
        expected = text.encode(encoding='utf-8') if isinstance(text, str) else text
        if current != expected:
            changed.append(path)
    if changed:
        raise SystemExit('generated JUNKET warp files are stale: ' + ', '.join(str(path.relative_to(ROOT)) for path in changed))
    if OBSOLETE_STRUCTURE.exists():
        raise SystemExit('obsolete JUNKET warp_row.mcstructure still exists')
    stale_slots = sorted(STRUCTURES.glob('warp_slot_*.mcstructure'))
    if stale_slots:
        raise SystemExit('obsolete JUNKET warp slot structures still exist: ' +
                         ', '.join(str(path.relative_to(ROOT)) for path in stale_slots))


def write(outputs: dict[Path, str]) -> None:
    for path, text in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if isinstance(text, str):
                path.write_text(text, encoding='utf-8', newline='\n')
            else:
                path.write_bytes(text)
        except PermissionError:
            if not path.is_relative_to(PRIVATE):
                raise
            print(f'warning: read-only private output was not updated: {path.relative_to(ROOT)}')
    if OBSOLETE_STRUCTURE.exists():
        OBSOLETE_STRUCTURE.unlink()
    for path in STRUCTURES.glob('warp_slot_*.mcstructure'):
        path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='fail when generated outputs are stale')
    parser.add_argument('--include-private', action='store_true',
                        help='also compare private world plans when the filesystem allows it')
    args = parser.parse_args()
    outputs = expected_outputs()
    check(outputs, args.include_private) if args.check else write(outputs)
    print(f"JUNKET warp catalog: {len(load()['games'])} games")


if __name__ == '__main__':
    main()
