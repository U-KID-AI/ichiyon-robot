"""Reproducible, lossless source export; never writes the friend's bbmodel.

Coordinate conversion follows Blockbench's bedrock.js / bedrock_animation.js:
https://github.com/JannisX11/blockbench/tree/master/js/formats/bedrock
No animation optimization or resampling is applied. Global zero rotations use
Blockbench v5.2.1's Z=0.01 compatibility correction.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "minecraft/source_assets/mokuro/mocro.bbmodel"
SOURCE_SHA = "eb0c2ab7afc70ad625bd87fd7ed63fe6a7d90c41e10b2e9635fba8703a0f2def"
RP = ROOT / "minecraft/resource_packs/ichiyon_avatar_rp"
ANIMATION_IDS = {f"mocro_{name}": f"animation.mokuro.{name}"
                 for name in ("walk", "flap", "open_wings", "roll")}


def position(v):
    return [-v[0], v[1], v[2]]


def rotation(v):
    return [-v[0], -v[1], v[2]]


def animation_rotation(v, rotation_global):
    """Match Blockbench v5.2.1 bedrock_animation.js global-zero handling."""
    vector = rotation(v)
    if rotation_global and all(value == 0 for value in vector):
        vector[2] = 0.01
    return vector


def export():
    raw = SOURCE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == SOURCE_SHA, "Immutable source differs"
    source = json.loads(raw)
    assert source["resolution"] == {"width": 64, "height": 64}
    elements = {c["uuid"]: c for c in source["elements"]}
    groups = {g["uuid"]: g for g in source["groups"]}
    bones = []

    def visit(node, parent=None):
        group = groups[node["uuid"]]
        bone = {"name": group["name"], "pivot": position(group["origin"])}
        if parent:
            bone["parent"] = parent
        if any(group["rotation"]):
            bone["rotation"] = rotation(group["rotation"])
        cubes = []
        for child in node["children"]:
            if not isinstance(child, str):
                continue
            cube = elements[child]
            assert cube["type"] == "cube" and cube["export"]
            size = [b-a for a, b in zip(cube["from"], cube["to"])]
            item = {"origin": [-cube["to"][0], *cube["from"][1:]], "size": size}
            if cube.get("inflate"):
                item["inflate"] = cube["inflate"]
            if any(cube.get("rotation", [0, 0, 0])):
                item.update(pivot=position(cube["origin"]), rotation=rotation(cube["rotation"]))
            if cube["box_uv"]:
                item["uv"] = cube.get("uv_offset", [0, 0])
                if cube.get("mirror_uv"):
                    item["mirror"] = True
            else:
                item["uv"] = {}
                for face, data in cube["faces"].items():
                    if data.get("texture") is None:
                        continue
                    u, v, u2, v2 = data["uv"]
                    if face in ("up", "down"):
                        u, v, u2, v2 = u2, v2, u, v
                    item["uv"][face] = {"uv": [u, v], "uv_size": [u2-u, v2-v]}
                    if data.get("rotation"):
                        item["uv"][face]["uv_rotation"] = data["rotation"]
            cubes.append(item)
        if cubes:
            bone["cubes"] = cubes
        bones.append(bone)
        for child in node["children"]:
            if isinstance(child, dict):
                visit(child, group["name"])

    for node in source["outliner"]:
        visit(node)
    geometry = {"format_version": "1.12.0", "minecraft:geometry": [{
        "description": {"identifier": "geometry.mokuro", "texture_width": 64,
                        "texture_height": 64, "visible_bounds_width": 3,
                        "visible_bounds_height": 3, "visible_bounds_offset": [0, 0.5, 0]},
        "bones": bones,
    }]}
    animations = {}
    for src in source["animations"]:
        anim = {"loop": {"loop": True, "hold": "hold_on_last_frame", "once": False}[src["loop"]],
                "animation_length": src["length"], "bones": {}}
        for name in ("anim_time_update", "blend_weight", "start_delay", "loop_delay"):
            if src.get(name):
                anim[name] = src[name]
        if src.get("override"):
            anim["override_previous_animation"] = True
        for uuid, animator in src["animators"].items():
            assert animator["type"] == "bone"
            keys = animator.get("keyframes", [])
            if not keys and not animator.get("rotation_global"):
                continue
            target = {}
            if animator.get("rotation_global"):
                target.update(relative_to={"rotation": "entity"}, rotation=[0, 0, 0.01])
            for key in keys:
                assert key["interpolation"] == "linear" and len(key["data_points"]) == 1
                channel = key["channel"]
                vector = [float(key["data_points"][0][axis]) for axis in "xyz"]
                if channel == "rotation":
                    vector = animation_rotation(vector, animator.get("rotation_global", False))
                elif channel == "position":
                    vector = position(vector)
                if not isinstance(target.get(channel), dict):
                    target[channel] = {}
                target[channel][str(float(key["time"]))] = vector
            anim["bones"][groups[uuid]["name"]] = target
        animations[ANIMATION_IDS[src["name"]]] = anim
    assert len(source["textures"]) == 1
    uri = source["textures"][0]["source"]
    assert uri.startswith("data:image/png;base64,")
    png = base64.b64decode(uri.split(",", 1)[1], validate=True)
    assert int.from_bytes(png[16:20], "big") == int.from_bytes(png[20:24], "big") == 128
    encode = lambda obj: (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode()
    return {
        RP / "models/entity/mokuro.geo.json": encode(geometry),
        RP / "animations/mokuro.animation.json": encode({"format_version": "1.8.0", "animations": animations}),
        RP / "textures/entity/mokuro.png": png,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for path, content in export().items():
        if args.check:
            actual = path.read_bytes()
            if path.suffix == ".json":
                actual = actual.replace(b"\r\n", b"\n")
            assert actual == content, f"Export differs: {path}"
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        print(f"OK {path.relative_to(ROOT)}")
