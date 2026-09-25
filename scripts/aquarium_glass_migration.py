"""Emit guarded vanilla fill commands for the surveyed building, never edit LevelDB.

Execution is an operator step AFTER managed deployment and a fresh consistent
backup/identity check. Reverse commands touch only our replacement block IDs.
"""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLAN = ROOT / 'minecraft/world_migrations/aquarium_glass_20260925.json'
MAPPING = {'minecraft:glass': 'ichiyon:aquarium_glass_clear',
           'minecraft:light_blue_stained_glass': 'ichiyon:aquarium_glass_light_blue'}


def cells(plan):
    result = {}
    assert plan['dimension'] == 'minecraft:overworld'
    for row in plan['runs']:
        x0, x1, y, z, old = row
        assert all(type(n) is int for n in row[:4])
        assert 30 <= x0 <= x1 <= 92 and 64 <= y <= 114 and 212 <= z <= 260
        assert old in MAPPING
        for x in range(x0, x1 + 1):
            key = (x, y, z)
            assert key not in result
            result[key] = old
    assert len(result) == plan['count'] == 1875
    assert sum(v == 'minecraft:glass' for v in result.values()) == 1558
    assert sum(v == 'minecraft:light_blue_stained_glass' for v in result.values()) == 317
    return result


def commands(plan, reverse=False):
    cells(plan)
    output = []
    for x0, x1, y, z, old in plan['runs']:
        new = MAPPING[old]
        if reverse:
            old, new = new, old
        output.append(f'execute in overworld run fill {x0} {y} {z} {x1} {y} {z} {new} replace {old}')
    return output


def verify(before, after, plan):
    """Compare full surveyed block/state dictionaries, not only the changed count."""
    expected = cells(plan)
    assert before.keys() == after.keys(), 'survey coverage differs'
    changes = {}
    for pos, original in before.items():
        if pos in expected:
            assert original == (expected[pos], {}), ('original changed', pos, original)
            assert after[pos] == (MAPPING[expected[pos]], {}), ('replacement mismatch', pos, after[pos])
            changes[pos] = after[pos]
        else:
            assert after[pos] == original, ('outside allowlist changed', pos, original, after[pos])
    assert len(changes) == len(expected)
    return len(changes)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reverse', action='store_true')
    args = parser.parse_args()
    print('\n'.join(commands(json.loads(PLAN.read_text()), args.reverse)))
