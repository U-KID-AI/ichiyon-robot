"""Validated cosmetic assets. No environment, database, network or shell access."""
from io import BytesIO
import hashlib
import json
import math
import re

from PIL import Image, UnidentifiedImageError

MAX_UPLOAD = 1024 * 1024
MAX_ID = 127
SLOTS = ("hat", "face", "neck", "back")
MOLCARS = ("molcar", "molcar2", "molcar3")
KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def validate_name(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 48:
        raise ValueError("名前は1〜48文字で指定してください。")
    if any(ord(c) < 32 or c == "§" for c in value):
        raise ValueError("名前に制御文字・装飾コードは使えません。")
    return value.strip()


def png_bytes(data, *, skin=False):
    if not isinstance(data, bytes) or len(data) > MAX_UPLOAD or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("PNG画像（1MB以下）を選んでください。")
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format != "PNG" or getattr(image, "n_frames", 1) != 1:
                raise ValueError("静止PNG画像を選んでください。")
            width, height = image.size
            if skin and (width, height) != (64, 64):
                raise ValueError("スキンは64×64のPNG画像を使用してください。")
            if not (1 <= width <= 1024 and 1 <= height <= 1024):
                raise ValueError("画像サイズは最大1024×1024です。")
            image.load()
            out = BytesIO()
            image.convert("RGBA").save(out, format="PNG")
            return out.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("PNG画像を読み込めませんでした。") from exc


def _vector(value, size, *, limit=512, positive=False):
    if (not isinstance(value, list) or len(value) != size
            or any(type(x) not in (int, float) or not math.isfinite(x)
                   or abs(x) > limit or (positive and x < 0) for x in value)):
        raise ValueError("モデルの座標・サイズが不正です。")
    return value


def geometry_bytes(data, texture):
    """Accept a bounded cube-only Bedrock model; never accept executable Molang."""
    if not isinstance(data, bytes) or len(data) > MAX_UPLOAD:
        raise ValueError("モデルは1MB以下にしてください。")
    try:
        value = json.loads(data)
        if not isinstance(value, dict) or set(value) - {"format_version", "minecraft:geometry"}:
            raise ValueError()
        if value.get("format_version") not in ("1.12.0", "1.16.0", "1.21.0"):
            raise ValueError()
        models = value["minecraft:geometry"]
        if not isinstance(models, list) or len(models) != 1:
            raise ValueError()
        model = models[0]
        if set(model) - {"description", "bones"}:
            raise ValueError()
        desc, bones = model["description"], model["bones"]
        if set(desc) - {"identifier", "texture_width", "texture_height", "visible_bounds_width", "visible_bounds_height", "visible_bounds_offset"}:
            raise ValueError()
        with Image.open(BytesIO(texture)) as image:
            if any(type(desc[k]) is not int for k in ("texture_width", "texture_height")) or (desc["texture_width"], desc["texture_height"]) != image.size:
                raise ValueError("モデルのテクスチャ寸法とPNG画像の寸法が違います。")
        if not isinstance(bones, list) or not 1 <= len(bones) <= 64:
            raise ValueError()
        names = set()
        cube_count = 0
        for bone in bones:
            if set(bone) - {"name", "parent", "pivot", "rotation", "cubes", "mirror", "inflate"}:
                raise ValueError()
            name = bone["name"]
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name) or name in names:
                raise ValueError()
            names.add(name)
            for key in ("pivot", "rotation"):
                if key in bone:
                    _vector(bone[key], 3)
            if "mirror" in bone and type(bone["mirror"]) is not bool:
                raise ValueError()
            if "inflate" in bone:
                _vector([bone["inflate"]], 1, limit=8)
            cubes = bone.get("cubes", [])
            if not isinstance(cubes, list):
                raise ValueError()
            cube_count += len(cubes)
            for cube in cubes:
                if set(cube) - {"origin", "size", "uv", "pivot", "rotation", "inflate", "mirror"}:
                    raise ValueError()
                _vector(cube["origin"], 3)
                _vector(cube["size"], 3, positive=True)
                for key in ("pivot", "rotation"):
                    if key in cube:
                        _vector(cube[key], 3)
                if "inflate" in cube:
                    _vector([cube["inflate"]], 1, limit=8)
                if "mirror" in cube and type(cube["mirror"]) is not bool:
                    raise ValueError()
                uv = cube["uv"]
                if isinstance(uv, list):
                    _vector(uv, 2, limit=2048)
                elif isinstance(uv, dict) and not set(uv) - {"north", "south", "east", "west", "up", "down"}:
                    for face in uv.values():
                        if set(face) - {"uv", "uv_size"}:
                            raise ValueError()
                        _vector(face["uv"], 2, limit=2048)
                        _vector(face["uv_size"], 2, limit=2048)
                else:
                    raise ValueError()
        if not 1 <= cube_count <= 2048:
            raise ValueError()
        parents = {bone["name"]: bone.get("parent") for bone in bones}
        for name in names:
            seen = set()
            while name is not None:
                if name in seen or name not in parents:
                    raise ValueError()
                seen.add(name)
                name = parents[name]
        # The exporter owns identifiers and bounding boxes; discard uploaded ones.
        model["description"] = {"identifier": "geometry.ichiyon.accessory", "texture_width": desc["texture_width"], "texture_height": desc["texture_height"], "visible_bounds_width": 4, "visible_bounds_height": 4, "visible_bounds_offset": [0, 1, 0]}
        normalized = json_bytes(value)
        if len(normalized) > MAX_UPLOAD:
            raise ValueError()
        return normalized
    except (KeyError, TypeError, AttributeError, RecursionError, UnicodeDecodeError, ValueError) as exc:
        message = str(exc) if isinstance(exc, ValueError) and str(exc).startswith("モデルのテクスチャ") else "キューブ形式のBedrockモデル（.geo.json）を指定してください。外部参照・スクリプト・循環ボーンは使用できません。"
        raise ValueError(message) from exc


def asset(kind, asset_id, key, name, texture, *, model="classic", slot="hat", geometry=None, icon=None):
    if type(asset_id) is not int or not 1 <= asset_id <= MAX_ID or not isinstance(key, str) or not KEY.fullmatch(key):
        raise ValueError("素材IDが不正です。")
    if kind not in ("skin", "accessory"):
        raise ValueError("素材の種類が不正です。")
    record = {"kind": kind, "id": asset_id, "key": key, "name": validate_name(name), "texture": png_bytes(texture, skin=kind == "skin")}
    if kind == "skin":
        if model not in ("classic", "slim"):
            raise ValueError("腕の種類を選んでください。")
        record["model"] = model
    else:
        if slot not in SLOTS or geometry is None or icon is None:
            raise ValueError("装着部位・モデル・アイコンを指定してください。")
        record.update(slot=slot, geometry=geometry_bytes(geometry, record["texture"]), icon=png_bytes(icon))
    return record


def fingerprint(record):
    data = {k: hashlib.sha256(v).hexdigest() if isinstance(v, bytes) else v for k, v in record.items()}
    return hashlib.sha256(json_bytes(data)).hexdigest()


def public_asset(record):
    return {k: v for k, v in record.items() if k not in ("texture", "geometry", "icon")} | {"digest": fingerprint(record)}
