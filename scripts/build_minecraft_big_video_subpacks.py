"""Offline-only Big V3 layout. This command never edits world refs or enables Big.

--check-enable is deliberately blocked until a separate compatibility task changes
the production contract. A selected-file view is not a network-delivery proof.
"""
import argparse
import ast
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
PACK = "minecraft/resource_packs/ichiyon_video_big_rp"
UUID = "a9689c00-b236-53ec-b952-a5fa64cb0cbc"
STEM = "video_screen_big"
BLACK = "video_big_black"
TIERS = {"lightweight": 1, "full": 3}
MANIFEST = {
    "format_version": 3,
    "header": {"name": "Ichiyon Video Big", "description": "Paused: experimental tiered Big Video",
               "uuid": UUID, "version": "1.0.2", "min_engine_version": "1.26.52"},
    "modules": [{"type": "resources", "uuid": "dce8cadf-c59a-59c6-9862-b590cd2cc461", "version": "1.0.2"}],
    "capabilities": ["pbr"],
    "metadata": {"authors": ["Ichiyon"]},
    "subpacks": [{"folder_name": name, "name": label, "memory_performance_tier": TIERS[name]}
                 for name, label in [("lightweight", "Lightweight - static black screen"),
                                     ("full", "Full - 128x72, 20fps")]],
}


def json_bytes(value):
    return (json.dumps(value, indent=2) + "\n").encode()


def lightweight_files(pack):
    full = pack / "subpacks/full"
    geometry_path = f"models/entity/{STEM}.geo.json"
    geometries = json.loads((full / geometry_path).read_bytes())["minecraft:geometry"]
    black_geometry = next(g for g in geometries if g["description"]["identifier"] == f"geometry.ichiyon_{BLACK}")
    controller = f"controller.render.ichiyon_{BLACK}"
    texture = f"textures/entity/{STEM}/black"
    return {
        f"entity/{STEM}.entity.json": json_bytes({"format_version": "1.10.0", "minecraft:client_entity": {
            "description": {"identifier": f"ichiyon:{STEM}", "materials": {"black": "entity_alphatest"},
                            "textures": {"black": texture}, "geometry": {"black": f"geometry.ichiyon_{BLACK}"},
                            "render_controllers": [controller]}}}),
        geometry_path: json_bytes({"format_version": "1.12.0", "minecraft:geometry": [black_geometry]}),
        f"render_controllers/{STEM}.render_controllers.json": json_bytes({"format_version": "1.8.0",
            "render_controllers": {controller: {"geometry": "Geometry.black", "materials": [{"*": "Material.black"}],
                                                "textures": ["Texture.black"], "ignore_lighting": True}}}),
        texture + ".png": (full / (texture + ".png")).read_bytes(),
    }


def generated_files(pack):
    light = lightweight_files(pack)
    return {"manifest.json": json_bytes(MANIFEST), **light,
            **{"subpacks/lightweight/" + path: data for path, data in light.items()}}


def validate_manifest(manifest):
    # Narrow V3 authoring contract only. The live apply parser still rejects V3.
    if manifest != MANIFEST:
        raise ValueError("Big V3 manifest differs from the reviewed offline contract")


def normalized(path, data):
    return data.replace(b"\r\n", b"\n") if path.endswith((".json", ".material")) else data


def validate_layout(pack):
    validate_manifest(json.loads((pack / "manifest.json").read_bytes()))
    expected = generated_files(pack)
    actual = {p.relative_to(pack).as_posix(): p for p in pack.rglob("*") if p.is_file()}
    outside_full = {p for p in actual if not p.startswith("subpacks/full/")}
    if outside_full != set(expected):
        raise ValueError("Only lightweight generated files may exist outside subpacks/full")
    for path, data in expected.items():
        if normalized(path, actual[path].read_bytes()) != data:
            raise ValueError("Stale lightweight asset: " + path)
    atlases = sorted((pack / f"subpacks/full/textures/entity/{STEM}").glob("atlas_*.png"))
    if [p.name for p in atlases] != [f"atlas_{i:03d}.png" for i in range(26)]:
        raise ValueError("Full video must retain exactly 26 atlases")


def selected_subpack(device_tier, manual=None):
    """Fixture model of the documented rule; never used for runtime/device guesses."""
    if type(device_tier) is not int or device_tier < 1:
        raise ValueError("An observed resource-pack performance tier is required")
    selected = max((name for name, tier in TIERS.items() if tier <= device_tier), key=TIERS.get)
    if manual is not None:
        if manual not in TIERS or TIERS[manual] > TIERS[selected]:
            raise ValueError("Manual subpack exceeds the engine default")
        selected = manual
    return selected


def selected_files(pack, subpack):
    """Logical base+one-subpack overlay, NOT a pack to distribute to a client."""
    if subpack not in TIERS:
        raise ValueError("Unknown subpack")
    files = {p.relative_to(pack).as_posix(): p.read_bytes() for p in pack.rglob("*")
             if p.is_file() and "subpacks" not in p.relative_to(pack).parts}
    root = pack / "subpacks" / subpack
    files.update({p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()})
    return files


def pause_state(root=ROOT):
    tree = ast.parse((root / 'bot/services/minecraft_resource_packs.py').read_text(encoding='utf-8'))
    values = [node.value.value for node in tree.body if isinstance(node, ast.Assign)
              and any(isinstance(target, ast.Name) and target.id == 'BIG_VIDEO_ENABLED' for target in node.targets)
              and isinstance(node.value, ast.Constant) and type(node.value.value) is bool]
    js = (root / 'minecraft/behavior_packs/import_structures/scripts/wall_displays_config.js').read_text(encoding='utf-8')
    flags = re.findall(r'^export const BIG_VIDEO_ENABLED = (true|false);$', js, re.MULTILINE)
    if len(values) != 1 or len(flags) != 1:
        raise ValueError('Cannot verify local compiler/runtime pause flags')
    return {'compiler_enabled': values[0], 'runtime_enabled': flags[0] == 'true'}


def enable_report():
    state = pause_state()
    return {"scope": "local_source_only", **state, "big_video_enabled": any(state.values()),
            "enable_status": "blocked", "production_manifest_contract": 2,
            "offline_big_manifest": 3, "selection": {str(t): selected_subpack(t) for t in range(1, 6)},
            "blocking_requirements": [
                "Human confirmation of phone improvement while Big remains paused; separate enable task",
                "Actual BDS/target clients validate experimental V3 and automatic/manual subpack selection",
                "Managed compiler, Control API, version comparison, world refs and rollback support V3 end-to-end",
                "Prove Tier 1/2 network download excludes full atlases; local subpack overlay does not prove this",
                "Cold/warm client load and texture-residency tests for Tier 1/2 and manual lightweight selection"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--check-enable", action="store_true", help="read-only readiness report; exits 2 while blocked")
    args = parser.parse_args()
    pack = ROOT / PACK
    if not args.check and not args.check_enable:
        for path, data in generated_files(pack).items():
            target = pack / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    validate_layout(pack)
    if any(pause_state().values()):
        raise ValueError('Big must remain paused in compiler AND Script')
    if args.check_enable:
        print(json.dumps(enable_report(), indent=2))
        return 2
    print("Big offline V3 layout verified; production enable remains blocked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
