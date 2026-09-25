"""Regenerate the single-entity video assets; the source MP4 stays outside Git."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
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


@dataclass(frozen=True)
class VideoProfile:
    name: str = "small"
    width: int = WIDTH
    height: int = HEIGHT
    cols: int = COLS
    rows: int = ROWS
    fps: int = FPS
    stem: str = "video_screen"
    rp_name: str = "ichiyon_avatar_rp"
    plane_height: int = 64
    black_width: int = 176
    bounds_width: int = 12
    bounds_height: int = 5
    bounds_y: float = 2
    material: str = "ichiyon_video_uv"
    export_name: str = "VIDEO_MEDIA"
    generated_name: str = "video_media.generated.js"
    stream: bool = False
    min_distance: int = 8
    max_distance: int = 64
    direct_pack: bool = False
    black_stem: str = "video_black"

    @property
    def cell_width(self):
        return self.width + 2

    @property
    def cell_height(self):
        return self.height + 2

    @property
    def atlas_width(self):
        return self.cols * self.cell_width

    @property
    def atlas_height(self):
        return self.rows * self.cell_height

    @property
    def capacity(self):
        return self.cols * self.rows


SMALL = VideoProfile()
BIG_PROFILES = {f"big-{width}": VideoProfile(
    name=f"big-{width}", width=width, height=height,
    cols=2048 // (width + 2), rows=2048 // (height + 2),
    stem="video_screen_big", rp_name="ichiyon_video_big_rp", plane_height=176,
    black_width=384, bounds_width=26, bounds_height=13, bounds_y=5.5,
    material="ichiyon_video_big_uv", export_name="VIDEO_BIG_MEDIA",
    generated_name="video_big_media.generated.js", stream=True, min_distance=32,
    direct_pack=True, black_stem="video_big_black",
) for width, height in [(192, 108), (160, 90), (128, 72)]}
AKKI = VideoProfile(name="akki", stem="video_screen_akki", rp_name="ichiyon_video_akki_rp",
    black_width=208, bounds_width=14, material="ichiyon_video_akki_uv",
    export_name="VIDEO_AKKI_MEDIA", generated_name="video_akki_media.generated.js",
    direct_pack=True, black_stem="video_akki_black", min_distance=1, max_distance=12)
PROFILES = {SMALL.name: SMALL, **BIG_PROFILES, AKKI.name: AKKI}


def run(args):
    return subprocess.run(args, check=True, capture_output=True).stdout


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def probe(source):
    return json.loads(run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(source)]))


def make_atlases(frames, output, profile=SMALL):
    width, height = profile.width, profile.height
    cols, capacity = profile.cols, profile.capacity
    cell_w, cell_h = profile.cell_width, profile.cell_height
    output.mkdir(parents=True, exist_ok=True)
    count = math.ceil(len(frames) / capacity)
    for index in range(count):
        atlas = Image.new("RGB", (profile.atlas_width, profile.atlas_height))
        for slot, path in enumerate(frames[index * capacity:(index + 1) * capacity]):
            with Image.open(path) as source:
                frame = source.convert("RGB")
            x, y = slot % cols * cell_w + 1, slot // cols * cell_h + 1
            atlas.paste(frame, (x, y))
            # Extruded edge pixels prevent adjacent frames bleeding under filtering.
            atlas.paste(frame.crop((0, 0, 1, height)), (x - 1, y))
            atlas.paste(frame.crop((width - 1, 0, width, height)), (x + width, y))
            atlas.paste(frame.crop((0, 0, width, 1)), (x, y - 1))
            atlas.paste(frame.crop((0, height - 1, width, height)), (x, y + height))
            for dx, dy, sx, sy in [(-1, -1, 0, 0), (width, -1, width - 1, 0),
                                   (-1, height, 0, height - 1), (width, height, width - 1, height - 1)]:
                atlas.putpixel((x + dx, y + dy), frame.getpixel((sx, sy)))
        atlas.save(output / f"atlas_{index:03d}.png", optimize=True)
    Image.new("RGB", (1, 1)).save(output / "black.png")
    # Only generated atlas names owned by this builder are pruned.
    expected = {f"atlas_{i:03d}.png" for i in range(count)}
    for path in output.glob("atlas_*.png"):
        if path.name not in expected:
            path.unlink()
    return count


def video_filter(profile):
    return (f"fps={profile.fps},scale={profile.width}:{profile.height}:force_original_aspect_ratio=decrease,"
            f"pad={profile.width}:{profile.height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1")


def sample_profiles(source, root=ROOT):
    """Compare lossless atlas PNG sizes without generating every full profile."""
    source = Path(source).resolve(strict=True)
    metadata = probe(source)
    video = next(s for s in metadata["streams"] if s["codec_type"] == "video")
    duration = float(video["duration"])
    sample_seconds = 3
    starts = [round((duration - sample_seconds) * i / 7, 6) for i in range(8)]
    results = []
    with tempfile.TemporaryDirectory(prefix="ichiyon-video-sample-") as temporary:
        temp = Path(temporary)
        for profile in BIG_PROFILES.values():
            frames = []
            for index, start in enumerate(starts):
                target = temp / profile.name / str(index)
                target.mkdir(parents=True)
                run(["ffmpeg", "-v", "error", "-ss", str(start), "-i", str(source),
                     "-map", "0:v:0", "-an", "-t", str(sample_seconds), "-vf", video_filter(profile),
                     str(target / "frame_%06d.png")])
                frames.extend(sorted(target.glob("frame_*.png")))
            output = temp / profile.name / "atlases"
            make_atlases(frames, output, profile)
            sample_bytes = sum(p.stat().st_size for p in output.glob("atlas_*.png"))
            estimated_frames = math.ceil(duration * profile.fps)
            atlas_count = math.ceil(estimated_frames / profile.capacity)
            rgba_bytes = atlas_count * profile.atlas_width * profile.atlas_height * 4
            result = {"profile": profile.name, "fps": profile.fps, "frameWidth": profile.width,
                "frameHeight": profile.height, "sampleFrames": len(frames), "samplePngBytes": sample_bytes,
                "estimatedFrameCount": estimated_frames, "estimatedPngBytes": round(sample_bytes / len(frames) * estimated_frames),
                "atlasCount": atlas_count, "atlasWidth": profile.atlas_width, "atlasHeight": profile.atlas_height,
                "decodedRgbaBytes": rgba_bytes, "decodedRgbaWithMipmapsBytes": math.ceil(rgba_bytes * 4 / 3)}
            results.append(result)
            print(json.dumps(result), flush=True)
    report = {"source": {"filename": source.name, "bytes": source.stat().st_size,
        "width": video["width"], "height": video["height"], "fps": video["avg_frame_rate"],
        "frameCount": int(video["nb_frames"]), "videoDuration": duration,
        "containerDuration": float(metadata["format"]["duration"])},
        "method": "Eight distributed 3-second samples at output 20fps; RGB PNG atlases with 1-pixel extruded edges. PNG estimate is sample bytes/frame times estimated full frame count; audio and definitions excluded.",
        "sampleStartsSeconds": starts, "sampleSecondsPerWindow": sample_seconds,
        "memoryNote": "Decoded RGBA assumes every atlas resident, no GPU compression. Mipmaps add approximately one third. Engine overhead and the separate small-screen pack are excluded; actual device residency is implementation-dependent.",
        "profiles": results}
    write_json(Path(root) / "minecraft/video_screen_big/profile_estimates.json", report)
    return report


def geometry(identifier, width, z, texture_width, texture_height, profile=SMALL):
    return {"description": {"identifier": identifier, "texture_width": texture_width,
            "texture_height": texture_height, "visible_bounds_width": profile.bounds_width,
            "visible_bounds_height": profile.bounds_height, "visible_bounds_offset": [0, profile.bounds_y, 0]},
            "bones": [{"name": "screen", "pivot": [0, 0, 0], "cubes": [{
                "origin": [-width / 2, 0, z], "size": [width, profile.plane_height, 0],
                "uv": {"north": {"uv": [0, 0], "uv_size": [texture_width, texture_height]}}}]}]}


def write_definitions(root, media, atlas_count, profile=SMALL):
    stem = profile.stem
    black = profile.black_stem
    video_geometry, black_geometry = f"geometry.ichiyon_{stem}", f"geometry.ichiyon_{black}"
    video_controller, black_controller = f"controller.render.ichiyon_{stem}", f"controller.render.ichiyon_{black}"
    frame = "math.max(0, q.property('ichiyon:frame'))"
    bp = root / "minecraft/behavior_packs/ichiyon_avatar_bp"
    rp = root / "minecraft/resource_packs" / profile.rp_name
    scripts = root / "minecraft/behavior_packs/import_structures/scripts"
    write_json(bp / f"entities/{stem}.json", {"format_version": "1.21.0", "minecraft:entity": {
        "description": {"identifier": f"ichiyon:{stem}", "is_spawnable": False,
            "is_summonable": True, "is_experimental": False, "properties": {
                "ichiyon:frame": {"type": "int", "range": [-1, media["frameCount"] - 1],
                    "default": -1, "client_sync": True}}},
        "components": {"minecraft:type_family": {"family": [f"ichiyon_{stem}"]},
            "minecraft:collision_box": {"width": 0, "height": 0},
            "minecraft:physics": {"has_gravity": False, "has_collision": False},
            "minecraft:pushable": {"is_pushable": False, "is_pushable_by_piston": False},
            "minecraft:damage_sensor": {"triggers": [{"cause": "all", "deals_damage": "no"}]},
            "minecraft:persistent": {}}}})
    textures = {f"atlas_{i}": f"textures/entity/{stem}/atlas_{i:03d}" for i in range(atlas_count)}
    textures["black"] = f"textures/entity/{stem}/black"
    write_json(rp / f"entity/{stem}.entity.json", {"format_version": "1.10.0", "minecraft:client_entity": {
        "description": {"identifier": f"ichiyon:{stem}", "materials": {
            "default": profile.material, "black": "entity_alphatest"}, "textures": textures,
            "geometry": {"default": video_geometry, "black": black_geometry},
            "render_controllers": [black_controller, {
                video_controller: "q.property('ichiyon:frame') >= 0"}]}}})
    write_json(rp / f"models/entity/{stem}.geo.json", {"format_version": "1.12.0", "minecraft:geometry": [
        # Entity yaw 0 maps model north (-Z) toward the audience (world +Z).
        geometry(video_geometry, profile.plane_height * 16 / 9, -0.5, profile.width, profile.height, profile),
        geometry(black_geometry, profile.black_width, 0, 1, 1, profile)]})
    write_json(rp / "materials/entity.material", {"materials": {"version": "1.0.0",
        f"{profile.material}:entity_alphatest": {"+defines": ["USE_UV_ANIM"]}}})
    write_json(rp / f"render_controllers/{stem}.render_controllers.json", {"format_version": "1.8.0", "render_controllers": {
        black_controller: {"geometry": "Geometry.black", "materials": [{"*": "Material.black"}],
            "textures": ["Texture.black"], "ignore_lighting": True},
        video_controller: {"arrays": {"textures": {
            "Array.frames": [f"Texture.atlas_{i}" for i in range(atlas_count)]}},
            "geometry": "Geometry.default", "materials": [{"*": "Material.default"}], "ignore_lighting": True,
            "textures": [f"Array.frames[math.floor({frame} / {profile.capacity})]"],
            "uv_anim": {"offset": [f"(math.mod({frame}, {profile.cols}) * {profile.cell_width} + 1) / {profile.atlas_width}",
                f"(math.floor(math.mod({frame}, {profile.capacity}) / {profile.cols}) * {profile.cell_height} + 1) / {profile.atlas_height}"],
                "scale": [profile.width / profile.atlas_width, profile.height / profile.atlas_height]}}}})
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / profile.generated_name).write_text(
        f"// Generated by scripts/build_minecraft_video.py. Do not edit.\nexport const {profile.export_name} = " +
        json.dumps(media, indent=2) + ";\n", encoding="utf-8")


def build(source, root=ROOT, profile=SMALL):
    source, root = Path(source).resolve(strict=True), Path(root).resolve()
    if source.is_relative_to(ROOT) or source.is_relative_to(root):
        raise ValueError("The original video must remain outside the repository/output tree")
    metadata = probe(source)
    video = next(stream for stream in metadata["streams"] if stream["codec_type"] == "video")
    has_audio = any(stream["codec_type"] == "audio" for stream in metadata["streams"])
    rp = root / "minecraft/resource_packs" / profile.rp_name
    sound_dir = rp / "sounds" / profile.stem
    texture_dir = rp / "textures/entity" / profile.stem
    sound_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ichiyon-video-") as temporary:
        temp = Path(temporary)
        run(["ffmpeg", "-v", "error", "-i", str(source), "-map", "0:v:0", "-an", "-vf",
             video_filter(profile),
             str(temp / "frame_%06d.png")])
        frames = sorted(temp.glob("frame_*.png"))
        if not frames:
            raise ValueError("No decoded video frames")
        frame_count, duration = len(frames), len(frames) / profile.fps
        atlas_count = make_atlases(frames, texture_dir, profile)
        sound, definitions = None, {}
        if has_audio:
            pcm = temp / "audio.wav"
            run(["ffmpeg", "-v", "error", "-i", str(source), "-map", "0:a:0", "-vn", "-af", "apad",
                 "-t", str(duration), "-ac", "1", "-ar", "44100", "-c:a", "pcm_s16le", str(pcm)])
            sound = f"ichiyon.{profile.stem}.audio"
            run(["ffmpeg", "-v", "error", "-i", str(pcm), "-c:a", "libvorbis", "-q:a", "3",
                 "-fflags", "+bitexact", "-flags:a", "+bitexact", "-y", str(sound_dir / "audio.ogg")])
            definitions[sound] = {"category": "record", "min_distance": profile.min_distance, "max_distance": profile.max_distance,
                "sounds": [{"name": f"sounds/{profile.stem}/audio", "stream": profile.stream, "is3D": True}]}
        # Remove only this builder's previous segmented implementation outputs.
        for path in sound_dir.glob("segment_*.ogg"):
            path.unlink()
        if not has_audio:
            (sound_dir / "audio.ogg").unlink(missing_ok=True)
    media = {"fps": profile.fps, "frameCount": frame_count, "duration": duration,
        "sound": sound}
    write_definitions(root, media, atlas_count, profile)
    report_dir = root / "minecraft" / profile.stem
    write_json(report_dir / "sound_definitions.fragment.json", definitions)
    if profile.direct_pack:
        write_json(rp / "sounds/sound_definitions.json", {"format_version": "1.20.20", "sound_definitions": definitions})
    report = {"source": {"filename": source.name, "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "width": video["width"], "height": video["height"], "fps": video["avg_frame_rate"],
        "duration": float(metadata["format"]["duration"]), "videoDuration": float(video["duration"]), "hasAudio": has_audio},
        "output": {**media, "atlasCount": atlas_count, "atlasWidth": profile.atlas_width, "atlasHeight": profile.atlas_height,
            "frameWidth": profile.width, "frameHeight": profile.height,
            "assetBytes": sum(p.stat().st_size for directory in [sound_dir, texture_dir] for p in directory.iterdir() if p.is_file())}}
    if profile.direct_pack:
        rgba_bytes = atlas_count * profile.atlas_width * profile.atlas_height * 4
        audio = next((s for s in metadata["streams"] if s["codec_type"] == "audio"), None)
        report["source"].update({"bytes": source.stat().st_size, "frameCount": int(video.get("nb_frames", 0)),
            "audio": {"codec": audio["codec_name"], "channels": audio["channels"],
                      "sampleRate": int(audio["sample_rate"])} if audio else None})
        report["output"].update({"profile": profile.name, "gridColumns": profile.cols, "gridRows": profile.rows,
            "framesPerAtlas": profile.capacity, "paddingPixels": 1,
            "atlasPngBytes": sum(p.stat().st_size for p in texture_dir.glob("atlas_*.png")),
            "audioBytes": (sound_dir / "audio.ogg").stat().st_size if has_audio else 0,
            "decodedRgbaBytes": rgba_bytes, "decodedRgbaWithMipmapsBytes": math.ceil(rgba_bytes * 4 / 3),
            "decodedRgbaPerAtlasBytes": profile.atlas_width * profile.atlas_height * 4,
            "audio": {"codec": "vorbis", "channels": 1, "sampleRate": 44100, "stream": profile.stream,
                "duration": duration, "minDistance": profile.min_distance, "maxDistance": profile.max_distance,
                "runtimeGain": 1, "unstreamedPcm16Bytes": round(duration * 44100) * 2,
                "unstreamedFloat32Bytes": round(duration * 44100) * 4} if has_audio else None,
            "resourcePack": profile.rp_name,
            "generatedPackBytesExcludingManifest": sum(p.stat().st_size for p in rp.rglob("*")
                if p.is_file() and p.name != "manifest.json")})
        report["memoryNote"] = ("RGBA assumes all atlases resident with no GPU compression; mipmaps add about one third. "
            "Engine overhead, other packs and streaming audio buffers are excluded. Streaming audio avoids requiring "
            "the full decoded PCM buffer; actual device memory and stream seeking still require an in-game check.")
        if not profile.stream:
            report["memoryNote"] = ("RGBA assumes all atlases resident with no GPU compression; mipmaps add about one third. "
                "Audio uses the small profile's unstreamed Vorbis mode; decoded audio buffer estimates are listed separately. "
                "Engine overhead, other packs and actual device residency require an in-game check.")
    write_json(report_dir / "build_report.json", report)
    print(json.dumps({"frames": frame_count, "duration": duration, "atlases": atlas_count,
                      "hasAudio": has_audio, "assetBytes": report["output"]["assetBytes"]}))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output-root", type=Path, default=ROOT)
    parser.add_argument("--sample-big-profiles", action="store_true")
    parser.add_argument("--profile", choices=PROFILES, default="small")
    args = parser.parse_args()
    if args.sample_big_profiles:
        sample_profiles(args.source, args.output_root)
    else:
        build(args.source, args.output_root, PROFILES[args.profile])
