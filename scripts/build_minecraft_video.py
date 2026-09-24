"""Regenerate the single-entity video assets; the source MP4 stays outside Git."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import tempfile

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
FPS, WIDTH, HEIGHT, COLS, ROWS = 20, 64, 36, 10, 10
CELL_W, CELL_H = WIDTH + 2, HEIGHT + 2
ATLAS_W, ATLAS_H = COLS * CELL_W, ROWS * CELL_H


def run(args):
    return subprocess.run(args, check=True, capture_output=True).stdout


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def probe(source):
    return json.loads(run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(source)]))


def make_atlases(frames, output):
    output.mkdir(parents=True, exist_ok=True)
    count = math.ceil(len(frames) / (COLS * ROWS))
    for index in range(count):
        atlas = Image.new("RGB", (ATLAS_W, ATLAS_H))
        for slot, path in enumerate(frames[index * 100:(index + 1) * 100]):
            with Image.open(path) as source:
                frame = source.convert("RGB")
            x, y = slot % COLS * CELL_W + 1, slot // COLS * CELL_H + 1
            atlas.paste(frame, (x, y))
            # Extruded edge pixels prevent adjacent frames bleeding under filtering.
            atlas.paste(frame.crop((0, 0, 1, HEIGHT)), (x - 1, y))
            atlas.paste(frame.crop((WIDTH - 1, 0, WIDTH, HEIGHT)), (x + WIDTH, y))
            atlas.paste(frame.crop((0, 0, WIDTH, 1)), (x, y - 1))
            atlas.paste(frame.crop((0, HEIGHT - 1, WIDTH, HEIGHT)), (x, y + HEIGHT))
            for dx, dy, sx, sy in [(-1, -1, 0, 0), (WIDTH, -1, WIDTH - 1, 0),
                                   (-1, HEIGHT, 0, HEIGHT - 1), (WIDTH, HEIGHT, WIDTH - 1, HEIGHT - 1)]:
                atlas.putpixel((x + dx, y + dy), frame.getpixel((sx, sy)))
        atlas.save(output / f"atlas_{index:03d}.png", optimize=True)
    Image.new("RGB", (1, 1)).save(output / "black.png")
    # Only generated atlas names owned by this builder are pruned.
    expected = {f"atlas_{i:03d}.png" for i in range(count)}
    for path in output.glob("atlas_*.png"):
        if path.name not in expected:
            path.unlink()
    return count


def geometry(identifier, width, z, texture_width, texture_height):
    return {"description": {"identifier": identifier, "texture_width": texture_width,
            "texture_height": texture_height, "visible_bounds_width": 12,
            "visible_bounds_height": 5, "visible_bounds_offset": [0, 2, 0]},
            "bones": [{"name": "screen", "pivot": [0, 0, 0], "cubes": [{
                "origin": [-width / 2, 0, z], "size": [width, 64, 0],
                "uv": {"south": {"uv": [0, 0], "uv_size": [texture_width, texture_height]}}}]}]}


def write_definitions(root, media, atlas_count):
    bp = root / "minecraft/behavior_packs/ichiyon_avatar_bp"
    rp = root / "minecraft/resource_packs/ichiyon_avatar_rp"
    scripts = root / "minecraft/behavior_packs/import_structures/scripts"
    write_json(bp / "entities/video_screen.json", {"format_version": "1.21.0", "minecraft:entity": {
        "description": {"identifier": "ichiyon:video_screen", "is_spawnable": False,
            "is_summonable": True, "is_experimental": False, "properties": {
                "ichiyon:frame": {"type": "int", "range": [-1, media["frameCount"] - 1],
                    "default": -1, "client_sync": True}}},
        "components": {"minecraft:type_family": {"family": ["ichiyon_video_screen"]},
            "minecraft:collision_box": {"width": 0, "height": 0},
            "minecraft:physics": {"has_gravity": False, "has_collision": False},
            "minecraft:pushable": {"is_pushable": False, "is_pushable_by_piston": False},
            "minecraft:damage_sensor": {"triggers": [{"cause": "all", "deals_damage": "no"}]},
            "minecraft:persistent": {}}}})
    textures = {f"atlas_{i}": f"textures/entity/video_screen/atlas_{i:03d}" for i in range(atlas_count)}
    textures["black"] = "textures/entity/video_screen/black"
    write_json(rp / "entity/video_screen.entity.json", {"format_version": "1.10.0", "minecraft:client_entity": {
        "description": {"identifier": "ichiyon:video_screen", "materials": {
            "default": "ichiyon_video_uv", "black": "entity_alphatest"}, "textures": textures,
            "geometry": {"default": "geometry.ichiyon_video_screen", "black": "geometry.ichiyon_video_black"},
            "render_controllers": ["controller.render.ichiyon_video_black", {
                "controller.render.ichiyon_video_screen": "q.property('ichiyon:frame') >= 0"}]}}})
    write_json(rp / "models/entity/video_screen.geo.json", {"format_version": "1.12.0", "minecraft:geometry": [
        geometry("geometry.ichiyon_video_screen", 64 * 16 / 9, 0.5, WIDTH, HEIGHT),
        geometry("geometry.ichiyon_video_black", 11 * 16, 0, 1, 1)]})
    write_json(rp / "materials/video_screen.material", {"materials": {"version": "1.0.0",
        "ichiyon_video_uv:entity_alphatest": {"+defines": ["USE_UV_ANIM"],
            "+states": ["DisableCulling"]}}})
    write_json(rp / "render_controllers/video_screen.render_controllers.json", {"format_version": "1.8.0", "render_controllers": {
        "controller.render.ichiyon_video_black": {"geometry": "Geometry.black", "materials": [{"*": "Material.black"}],
            "textures": ["Texture.black"], "ignore_lighting": True},
        "controller.render.ichiyon_video_screen": {"arrays": {"textures": {
            "Array.frames": [f"Texture.atlas_{i}" for i in range(atlas_count)]}},
            "geometry": "Geometry.default", "materials": [{"*": "Material.default"}], "ignore_lighting": True,
            "textures": ["Array.frames[math.floor(math.max(0, q.property('ichiyon:frame')) / 100)]"],
            "uv_anim": {"offset": [f"(math.mod(math.max(0, q.property('ichiyon:frame')), 10) * {CELL_W} + 1) / {ATLAS_W}",
                f"(math.floor(math.mod(math.max(0, q.property('ichiyon:frame')), 100) / 10) * {CELL_H} + 1) / {ATLAS_H}"],
                "scale": [WIDTH / ATLAS_W, HEIGHT / ATLAS_H]}}}})
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "video_media.generated.js").write_text(
        "// Generated by scripts/build_minecraft_video.py. Do not edit.\nexport const VIDEO_MEDIA = " +
        json.dumps(media, indent=2) + ";\n", encoding="utf-8")


def build(source, root=ROOT):
    source, root = Path(source).resolve(strict=True), Path(root).resolve()
    if source.is_relative_to(ROOT) or source.is_relative_to(root):
        raise ValueError("The original video must remain outside the repository/output tree")
    metadata = probe(source)
    video = next(stream for stream in metadata["streams"] if stream["codec_type"] == "video")
    has_audio = any(stream["codec_type"] == "audio" for stream in metadata["streams"])
    rp = root / "minecraft/resource_packs/ichiyon_avatar_rp"
    sound_dir = rp / "sounds/video_screen"
    sound_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ichiyon-video-") as temporary:
        temp = Path(temporary)
        run(["ffmpeg", "-v", "error", "-i", str(source), "-map", "0:v:0", "-an", "-vf",
             f"fps={FPS},scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:black,setsar=1",
             str(temp / "frame_%06d.png")])
        frames = sorted(temp.glob("frame_*.png"))
        if not frames:
            raise ValueError("No decoded video frames")
        frame_count, duration = len(frames), len(frames) / FPS
        atlas_count = make_atlases(frames, rp / "textures/entity/video_screen")
        sound, definitions = None, {}
        if has_audio:
            pcm = temp / "audio.wav"
            run(["ffmpeg", "-v", "error", "-i", str(source), "-map", "0:a:0", "-vn", "-af", "apad",
                 "-t", str(duration), "-ac", "1", "-ar", "44100", "-c:a", "pcm_s16le", str(pcm)])
            sound = "ichiyon.video_screen.audio"
            run(["ffmpeg", "-v", "error", "-i", str(pcm), "-c:a", "libvorbis", "-q:a", "3",
                 "-fflags", "+bitexact", "-flags:a", "+bitexact", "-y", str(sound_dir / "audio.ogg")])
            definitions[sound] = {"category": "record", "min_distance": 1, "max_distance": 16,
                "sounds": [{"name": "sounds/video_screen/audio", "stream": False, "is3D": True}]}
        # Remove only this builder's previous segmented implementation outputs.
        for path in sound_dir.glob("segment_*.ogg"):
            path.unlink()
        if not has_audio:
            (sound_dir / "audio.ogg").unlink(missing_ok=True)
    media = {"fps": FPS, "frameCount": frame_count, "duration": duration,
        "sound": sound}
    write_definitions(root, media, atlas_count)
    write_json(root / "minecraft/video_screen/sound_definitions.fragment.json", definitions)
    report = {"source": {"filename": source.name, "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "width": video["width"], "height": video["height"], "fps": video["avg_frame_rate"],
        "duration": float(metadata["format"]["duration"]), "videoDuration": float(video["duration"]), "hasAudio": has_audio},
        "output": {**media, "atlasCount": atlas_count, "atlasWidth": ATLAS_W, "atlasHeight": ATLAS_H,
            "frameWidth": WIDTH, "frameHeight": HEIGHT,
            "assetBytes": sum(p.stat().st_size for directory in [sound_dir, rp / "textures/entity/video_screen"] for p in directory.iterdir() if p.is_file())}}
    write_json(root / "minecraft/video_screen/build_report.json", report)
    print(json.dumps({"frames": frame_count, "duration": duration, "atlases": atlas_count,
                      "hasAudio": has_audio, "assetBytes": report["output"]["assetBytes"]}))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output-root", type=Path, default=ROOT)
    args = parser.parse_args()
    build(args.source, args.output_root)
