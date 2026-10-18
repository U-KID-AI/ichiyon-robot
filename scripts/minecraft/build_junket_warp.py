"""Generate the shared 17-game JUNKET warp catalog and installation plan.

The checked-in JSON is the source of truth. Runtime JavaScript and the private
world-control plan are generated from it. This script never writes a live world.
"""
from __future__ import annotations

import argparse
from io import BytesIO
import json
from pathlib import Path
from typing import Any

import nbtlib as n


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'scripts/minecraft/junket_games.json'
RUNTIME = ROOT / 'minecraft/behavior_packs/ichiyon_junket_bp/scripts/junket_warp_generated.js'
PRIVATE = ROOT / 'data/junket-private/world-controls'
PLAN = PRIVATE / 'junket-warp-plan.json'
FUNCTION = PRIVATE / 'junket-warp-install.mcfunction'
STRUCTURE = ROOT / 'minecraft/behavior_packs/ichiyon_junket_bp/structures/junket/warp_row.mcstructure'
EXPECTED_IDS = [
    'sparrow', 'temperance', 'lucy', 'silence', 'jinn', 'ace', 'grail',
    'hope', 'auction', 'fire', 'pig', 'light', 'mine', 'clock', 'walker',
    'garden', 'rabbit',
]
READINESS = {'verified', 'implemented', 'venue-review', 'in-progress', 'not-implemented'}


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
        if game.get('player_mode') not in ('fixed', 'registration'):
            raise ValueError(f"invalid player mode for {game['id']}")
        minimum = game.get('min_players')
        maximum = game.get('max_players')
        if not isinstance(minimum, int) or minimum < 1:
            raise ValueError(f"invalid minimum players for {game['id']}")
        if maximum is not None and (not isinstance(maximum, int) or maximum < minimum):
            raise ValueError(f"invalid maximum players for {game['id']}")
        arrival = game.get('source_arrival')
        if not isinstance(arrival, list) or len(arrival) != 3 or not all(isinstance(v, (int, float)) for v in arrival):
            raise ValueError(f"invalid arrival for {game['id']}")
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


def build_plan(catalog: dict[str, Any]) -> dict[str, Any]:
    controls = catalog['controls']
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
            'enabled': game['readiness'] in ('verified', 'implemented'),
            'command_block': command,
            'sign': sign,
            'button': button,
            'command': f"scriptevent junket:warp {game['id']}",
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
        'source': catalog['source'],
        'dimension': controls['dimension'],
        'protected_anchor': anchor,
        'return_point': controls['return_point'],
        'placements': placements,
    }


def runtime_source(plan: dict[str, Any]) -> str:
    data = {
        'WARP_DIMENSION': plan['dimension'],
        'WARP_RETURN_POINT': plan['return_point'],
        'PROTECTED_ANCHOR': plan['protected_anchor'],
        'WARP_CONTROLS': plan['placements'],
    }
    body = json.dumps(data, ensure_ascii=False, indent=2)
    return (
        '// Generated by scripts/minecraft/build_junket_warp.py. Do not edit by hand.\n'
        f'export const WARP_CONFIG = Object.freeze({body});\n'
    )


def function_source(plan: dict[str, Any]) -> str:
    lines = [
        '# Generated review/install function for JUNKET warp controls.',
        '# Do not execute until the live control row is empty and backed up.',
        f"# Protected anchor: {plan['protected_anchor']['label']} "
        f"{plan['protected_anchor']['command_block']}",
        '# This file is an installation aid; the Script API installer performs preflight.',
    ]
    for item in plan['placements']:
        x, y, z = item['command_block']
        sx, sy, sz = item['sign']
        bx, by, bz = item['button']
        state = 'OPEN' if item['enabled'] else '準備中'
        command = json.dumps({'Command': item['command'], 'CustomName': f"JUNKET {item['title']}"}, ensure_ascii=False)
        sign = json.dumps({'FrontText': {'Text': f"{item['title']}\n{state}", 'TextOwner': 'JUNKET'}}, ensure_ascii=False)
        lines.extend([
            f"# {item['id']} / {item['title']} / {state}",
            f'setblock {x} {y} {z} minecraft:command_block ["facing_direction"=2,"conditional_bit"=false] keep',
            f'blockdata {x} {y} {z} {command}',
            f'setblock {sx} {sy} {sz} minecraft:wall_sign ["facing_direction"=2] keep',
            f'blockdata {sx} {sy} {sz} {sign}',
            f'setblock {bx} {by} {bz} minecraft:wooden_button ["facing_direction"=1,"button_pressed_bit"=false] keep',
        ])
    return '\n'.join(lines) + '\n'


def structure_bytes(plan: dict[str, Any]) -> bytes:
    size = [34, 1, 3]
    blocks = {}
    entities = {}
    for index, item in enumerate(plan['placements']):
        x = index * 2
        blocks[(x, 0, 1)] = 'minecraft:command_block'
        blocks[(x, 0, 0)] = 'minecraft:oak_sign'
        blocks[(x, 0, 2)] = 'minecraft:wooden_button'
        command_index = x * size[1] * size[2] + 1
        sign_index = x * size[1] * size[2]
        entities[str(command_index)] = n.Compound({
            'block_entity_data': n.Compound({
                'id': n.String('CommandBlock'),
                'x': n.Int(x), 'y': n.Int(0), 'z': n.Int(1),
                'Command': n.String(item['command']),
                'CustomName': n.String(f"JUNKET {item['title']}"),
                'auto': n.Byte(0),
                'powered': n.Byte(0),
                'conditionMet': n.Byte(1),
            }),
        })
        entities[str(sign_index)] = n.Compound({
            'block_entity_data': n.Compound({
                'id': n.String('Sign'),
                'x': n.Int(x), 'y': n.Int(0), 'z': n.Int(0),
                'IsWaxed': n.Byte(1),
                'FrontText': n.Compound({
                    'Text': n.String(item['title']),
                    'TextOwner': n.String('JUNKET'),
                    'Hidden': n.Byte(0),
                }),
            }),
        })
    names = ['minecraft:air', 'minecraft:command_block', 'minecraft:oak_sign', 'minecraft:wooden_button']
    values = []
    for x in range(size[0]):
        for y in range(size[1]):
            for z in range(size[2]):
                values.append(names.index(blocks.get((x, y, z), 'minecraft:air')))
    palette = [
        n.Compound({
            'name': n.String(name),
            'states': n.Compound({
                'facing_direction': n.Int(2),
                'button_pressed_bit': n.Byte(0),
            }) if name == 'minecraft:wooden_button' else n.Compound(),
            'version': n.Int(18186752),
        })
        for name in names
    ]
    root = n.File({
        'format_version': n.Int(1),
        'size': n.List[n.Int]([n.Int(value) for value in size]),
        'structure_world_origin': n.List[n.Int]([n.Int(0), n.Int(0), n.Int(0)]),
        'structure': n.Compound({
            'block_indices': n.List[n.List[n.Int]]([
                n.List[n.Int]([n.Int(value) for value in values]),
                n.List[n.Int]([n.Int(-1) for _ in values]),
            ]),
            'entities': n.List[n.Compound]([]),
            'palette': n.Compound({'default': n.Compound({
                'block_palette': n.List[n.Compound](palette),
                'block_position_data': n.Compound(entities),
            })}),
        }),
    })
    output = BytesIO()
    root.write(output, byteorder='little')
    return output.getvalue()


def expected_outputs(catalog: dict[str, Any] | None = None) -> dict[Path, str | bytes]:
    plan = build_plan(catalog or load())
    return {
        RUNTIME: runtime_source(plan),
        PLAN: json.dumps(plan, ensure_ascii=False, indent=2) + '\n',
        FUNCTION: function_source(plan),
        STRUCTURE: structure_bytes(plan),
    }


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
