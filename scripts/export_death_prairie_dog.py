"""Blockbench export with Death Prairie Dog's preview rotations baked locally.

Independent exporter: never imports, edits, or reconfigures export_mokuro.py.
Only the verified green embedded PNG is exported; no pixel re-encoding.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import base64
from collections import Counter
import hashlib
import io
import json
import math
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "minecraft/source_assets/death_prairie_dog/deathprairie.bbmodel"
SOURCE_SHA = "5a96a764fcc95353118a2feef4116229ff7de59c8472f374d5c41c74a424b43c"
RP = ROOT / "minecraft/resource_packs/ichiyon_avatar_rp"
ANIMATION_IDS = {
    "animation.model.new": "animation.death_prairie_dog.transition",
    "prairie4walk": "animation.death_prairie_dog.prairie4walk",
    "prairie2walk": "animation.death_prairie_dog.prairie2walk",
}
REVERSE_TRANSITION_ID = "animation.death_prairie_dog.transition_reverse"
FOUR_LEG_POSE_ID = "animation.death_prairie_dog.prairie4pose"


def position(v):
    return [-v[0], v[1], v[2]]


def rotation(v):
    return [-v[0], -v[1], v[2]]


IDENTITY = (0.0, 0.0, 0.0, 1.0)


def quat_mul(a, b):
    x, y, z, w = a
    X, Y, Z, W = b
    return (w*X+x*W+y*Z-z*Y, w*Y-x*Z+y*W+z*X,
            w*Z+x*Y-y*X+z*W, w*W-x*X-y*Y-z*Z)


def quat_inverse(q):
    # All inputs are unit rotations (no animated scale in this immutable model).
    return (-q[0], -q[1], -q[2], q[3])


def quat_from_zyx(degrees):
    x, y, z = (math.radians(v)/2 for v in degrees)
    return quat_mul(quat_mul((0, 0, math.sin(z), math.cos(z)),
                             (0, math.sin(y), 0, math.cos(y))),
                    (math.sin(x), 0, 0, math.cos(x)))


def quat_to_zyx(q):
    """THREE.Euler.setFromRotationMatrix's ZYX convention, in degrees."""
    x, y, z, w = q
    m11, m21, m31 = 1-2*(y*y+z*z), 2*(x*y+z*w), 2*(x*z-y*w)
    m12, m22 = 2*(x*y-z*w), 1-2*(x*x+z*z)
    m32, m33 = 2*(y*z+x*w), 1-2*(x*x+y*y)
    ry = math.asin(max(-1, min(1, -m31)))
    if abs(m31) < 0.9999999:
        rx, rz = math.atan2(m32, m33), math.atan2(m21, m11)
    else:
        rx, rz = 0, math.atan2(-m12, m22)
    return [round(math.degrees(v), 10) + 0.0 for v in (rx, ry, rz)]


def bone_parents(source):
    parents = {}
    def visit(node, parent=None):
        parents[node['uuid']] = parent
        for child in node['children']:
            if isinstance(child, dict):
                visit(child, node['uuid'])
    for node in source['outliner']:
        visit(node)
    return parents


def sample_rotation(animator, time):
    keys = sorted((k for k in animator.get('keyframes', [])
                   if k['channel'] == 'rotation'), key=lambda k: k['time'])
    if not keys:
        return [0, 0, 0]
    before = next((k for k in reversed(keys) if k['time'] <= time), keys[0])
    after = next((k for k in keys if k['time'] >= time), keys[-1])
    alpha = (time-before['time'])/(after['time']-before['time']) if after != before else 0
    return [float(before['data_points'][0][a])*(1-alpha)
            + float(after['data_points'][0][a])*alpha for a in 'xyz']


def preview_world_rotations(source, animation, time):
    """Blockbench 5.2.1 single-clip preview, including saved Group.all order.

    bbmodel.js initializes groups in source array order; loading the outliner
    changes parenting, not that order. Animator.stackAnimations visits Group.all
    in that order. BoneAnimator.displayRotation cancels the parent's world
    quaternion *at that point*, before later groups have been animated. Sorting
    parents first here would change this model's visible Blockbench pose.
    """
    assert all(not any(g['rotation']) for g in source['groups'])
    parents = bone_parents(source)
    local = {g['uuid']: IDENTITY for g in source['groups']}
    def world(uuid):
        if uuid is None:
            return IDENTITY
        return quat_mul(world(parents[uuid]), local[uuid])
    for group in source['groups']:
        uuid = group['uuid']
        animator = animation['animators'].get(uuid, {})
        local[uuid] = quat_from_zyx(sample_rotation(animator, time))
        if animator.get('rotation_global'):
            local[uuid] = quat_mul(quat_inverse(world(parents[uuid])), local[uuid])
    return {uuid: world(uuid) for uuid in local}


def baked_rotation(source, animation, uuid, time):
    desired = preview_world_rotations(source, animation, time)
    parent = bone_parents(source)[uuid]
    local = quat_mul(quat_inverse(desired[parent] if parent else IDENTITY), desired[uuid])
    # Retain the existing Blockbench -> Bedrock signs and ZYX Euler order.
    # Ordinary local zero rotation needs no relative_to/entity epsilon workaround.
    return rotation(quat_to_zyx(local))


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
                target['rotation'] = baked_rotation(source, src, uuid, 0)
            for key in keys:
                assert key["interpolation"] == "linear" and len(key["data_points"]) == 1
                channel = key["channel"]
                assert channel in ("rotation", "position", "scale")
                vector = [float(key["data_points"][0][axis]) for axis in "xyz"]
                if channel == "rotation":
                    vector = (baked_rotation(source, src, uuid, key['time'])
                              if animator.get('rotation_global') else rotation(vector))
                elif channel == "position":
                    vector = position(vector)
                if not isinstance(target.get(channel), dict):
                    target[channel] = {}
                time = str(float(key["time"]))
                assert time not in target[channel], "Duplicate keyframe would lose data"
                target[channel][time] = vector
            anim["bones"][groups[uuid]["name"]] = target
        animations[ANIMATION_IDS[src["name"]]] = anim

    # Synthetic clips are derived only from the verified baked transition.
    # The source bbmodel remains immutable.
    forward = animations[ANIMATION_IDS["animation.model.new"]]
    length = float(forward["animation_length"])

    reverse = deepcopy(forward)
    for bone in reverse["bones"].values():
        for channel in ("rotation", "position", "scale"):
            track = bone.get(channel)
            if not isinstance(track, dict):
                continue
            remapped = {}
            for time, value in track.items():
                mirrored = round(length - float(time), 10)
                key = str(float(mirrored))
                assert key not in remapped, "Reverse keyframe collision"
                remapped[key] = value
            bone[channel] = dict(sorted(remapped.items(), key=lambda item: float(item[0])))
    animations[REVERSE_TRANSITION_ID] = reverse

    pose_bones = {}
    for bone_name, bone in forward["bones"].items():
        target = {}
        for channel in ("rotation", "position", "scale"):
            track = bone.get(channel)
            if isinstance(track, dict):
                last = max(track, key=lambda item: float(item))
                target[channel] = deepcopy(track[last])
            elif track is not None:
                target[channel] = deepcopy(track)
        if target:
            pose_bones[bone_name] = target
    animations[FOUR_LEG_POSE_ID] = {
        "loop": True,
        "animation_length": 1,
        "bones": pose_bones,
    }

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
