"""Lossless Blockbench export, using Mokuro's coordinate/UV/keyframe method.

Independent exporter: never imports, edits, or reconfigures export_mokuro.py.
Only the verified green embedded PNG is exported; no pixel re-encoding.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import hashlib
import io
import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "minecraft/source_assets/death_prairie_dog/deathprairie.bbmodel"
SOURCE_SHA = "03a67110f3dae8c1ba2b86f6325438b32f49f613018471358032bfe279c3d4ad"
RP = ROOT / "minecraft/resource_packs/ichiyon_avatar_rp"
ANIMATION_IDS = {
    "animation.model.new": "animation.death_prairie_dog.transition",
    "prairie4walk": "animation.death_prairie_dog.prairie4walk",
    "prairie2walk": "animation.death_prairie_dog.prairie2walk",
}


def position(v):
    return [-v[0], v[1], v[2]]


def rotation(v):
    return [-v[0], -v[1], v[2]]


def green_texture(source):
    """Identify by dominant opaque color, never by id or array position."""
    candidates = []
    for texture in source["textures"]:
        assert texture["source"].startswith("data:image/png;base64,")
        png = base64.b64decode(texture["source"].split(",", 1)[1], validate=True)
        with Image.open(io.BytesIO(png)) as image:
            assert image.size == (64, 64)
            colors = Counter(p for p in image.convert("RGBA").getdata() if p[3] > 0)
        dominant, count = colors.most_common(1)[0]
        r, g, b, _ = dominant
        if g > r * 1.5 and g > b * 1.5 and count > sum(colors.values()) / 2:
            candidates.append((texture, png))
    assert len(candidates) == 1, "Green texture must be uniquely identifiable"
    return candidates[0]


def export():
    raw = SOURCE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == SOURCE_SHA, "Immutable source differs"
    source = json.loads(raw)
    assert source["resolution"] == {"width": 64, "height": 64}
    assert len(source["elements"]) == 52 and len(source["groups"]) == 8
    assert len(source["textures"]) == 4
    elements = {c["uuid"]: c for c in source["elements"]}
    groups = {g["uuid"]: g for g in source["groups"]}
    bones, seen_cubes = [], set()

    def visit(node, parent=None):
        group = groups[node["uuid"]]
        assert group["export"]
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
            assert child not in seen_cubes
            seen_cubes.add(child)
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
    assert seen_cubes == set(elements) and len(bones) == len(groups)
    geometry = {"format_version": "1.12.0", "minecraft:geometry": [{
        "description": {"identifier": "geometry.death_prairie_dog", "texture_width": 64,
                        "texture_height": 64, "visible_bounds_width": 3,
                        "visible_bounds_height": 3, "visible_bounds_offset": [0, 0.5, 0]},
        "bones": bones,
    }]}
    animations = {}
    assert {a["name"] for a in source["animations"]} == set(ANIMATION_IDS)
    for src in source["animations"]:
        anim = {"loop": {"loop": True, "hold": "hold_on_last_frame", "once": False}[src["loop"]],
                "animation_length": src["length"], "bones": {}}
        for name in ("anim_time_update", "blend_weight", "start_delay", "loop_delay"):
            if src.get(name):
                anim[name] = src[name]
        if src.get("override"):
            anim["override_previous_animation"] = True
        for uuid, animator in src["animators"].items():
            if animator["type"] == "effect":
                assert not animator.get("keyframes"), "Effect tracks require explicit export"
                continue
            assert animator["type"] == "bone"
            keys = animator.get("keyframes", [])
            if not keys and not animator.get("rotation_global"):
                continue
            target = {}
            if animator.get("rotation_global"):
                target.update(relative_to={"rotation": "entity"}, rotation=[0, 0, 0])
            for key in keys:
                assert key["interpolation"] == "linear" and len(key["data_points"]) == 1
                channel = key["channel"]
                assert channel in ("rotation", "position", "scale")
                vector = [float(key["data_points"][0][axis]) for axis in "xyz"]
                if channel == "rotation":
                    vector = rotation(vector)
                elif channel == "position":
                    vector = position(vector)
                if not isinstance(target.get(channel), dict):
                    target[channel] = {}
                time = str(float(key["time"]))
                assert time not in target[channel], "Duplicate keyframe would lose data"
                target[channel][time] = vector
            anim["bones"][groups[uuid]["name"]] = target
        animations[ANIMATION_IDS[src["name"]]] = anim
    _, png = green_texture(source)
    encode = lambda obj: (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode()
    return {
        RP / "models/entity/death_prairie_dog.geo.json": encode(geometry),
        RP / "animations/death_prairie_dog.animation.json": encode({"format_version": "1.8.0", "animations": animations}),
        RP / "textures/entity/death_prairie_dog.png": png,
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
