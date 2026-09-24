"""Validate video assets, real FFmpeg regeneration and executable wall runtime tests."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from PIL import Image, ImageChops

from build_minecraft_video import ROOT, build, probe, run

RP = ROOT / "minecraft/resource_packs/ichiyon_avatar_rp"
BP = ROOT / "minecraft/behavior_packs/ichiyon_avatar_bp"
SOURCE = None


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


class WallDisplayChecks(unittest.TestCase):
    def test_runtime(self):
        subprocess.run(["node", str(ROOT / "scripts/check_minecraft_wall_displays.mjs")], check=True)

    def test_entity_and_geometry_contract(self):
        report = read(ROOT / "minecraft/video_screen/build_report.json")
        entity = read(BP / "entities/video_screen.json")["minecraft:entity"]
        prop = entity["description"]["properties"]["ichiyon:frame"]
        self.assertEqual(prop["range"], [-1, report["output"]["frameCount"] - 1])
        self.assertTrue(prop["client_sync"])
        self.assertEqual(prop["default"], -1)
        self.assertFalse(entity["components"]["minecraft:physics"]["has_gravity"])
        geometries = read(RP / "models/entity/video_screen.geo.json")["minecraft:geometry"]
        video, black = [g["bones"][0]["cubes"][0] for g in geometries]
        self.assertAlmostEqual(video["size"][0] / video["size"][1], 16 / 9)
        self.assertEqual(black["size"], [176, 64, 0])
        self.assertGreater(video["origin"][2], black["origin"][2])
        self.assertEqual(set(video["uv"]), {"south"})
        client = read(RP / "entity/video_screen.entity.json")["minecraft:client_entity"]["description"]
        self.assertEqual(len(client["textures"]), report["output"]["atlasCount"] + 1)
        for texture in client["textures"].values():
            self.assertTrue((RP / (texture + ".png")).is_file())
        material = read(RP / "materials/video_screen.material")["materials"]["ichiyon_video_uv:entity_alphatest"]
        self.assertIn("USE_UV_ANIM", material["+defines"])

    def test_atlas_frames_padding_and_nonblank(self):
        output = read(ROOT / "minecraft/video_screen/build_report.json")["output"]
        frames = output["frameCount"]
        self.assertEqual(output["duration"], frames / output["fps"])
        self.assertEqual(output["atlasCount"], (frames + 99) // 100)
        unique = set()
        for index in range(output["atlasCount"]):
            with Image.open(RP / f"textures/entity/video_screen/atlas_{index:03d}.png") as atlas:
                self.assertEqual(atlas.size, (660, 380))
                for slot in range(min(100, frames - index * 100)):
                    x, y = slot % 10 * 66 + 1, slot // 10 * 38 + 1
                    frame = atlas.crop((x, y, x + 64, y + 36))
                    unique.add(hashlib.sha256(frame.tobytes()).hexdigest())
                    self.assertIsNone(ImageChops.difference(atlas.crop((x - 1, y, x, y + 36)), frame.crop((0, 0, 1, 36))).getbbox())
        self.assertGreater(len(unique), 100)
        with Image.open(RP / "textures/entity/video_screen/black.png") as black:
            self.assertEqual(black.getpixel((0, 0)), (0, 0, 0))

    def test_real_audio_duration_mono_and_definition(self):
        report = read(ROOT / "minecraft/video_screen/build_report.json")
        audio = RP / "sounds/video_screen/audio.ogg"
        metadata = probe(audio)
        self.assertEqual(metadata["streams"][0]["channels"], 1)
        self.assertAlmostEqual(float(metadata["format"]["duration"]), report["output"]["duration"], places=3)
        pcm = run(["ffmpeg", "-v", "error", "-i", str(audio), "-f", "s16le", "-"])
        self.assertGreater(len(pcm), 100000)
        self.assertTrue(any(pcm))
        definitions = read(ROOT / "minecraft/video_screen/sound_definitions.fragment.json")
        self.assertEqual(set(definitions), {report["output"]["sound"]})
        definition = definitions[report["output"]["sound"]]
        self.assertEqual(definition["max_distance"], 16)
        self.assertFalse(definition["sounds"][0]["stream"])
        self.assertTrue(definition["sounds"][0]["is3D"])

    def test_builder_from_synthetic_video_and_silent_source(self):
        with tempfile.TemporaryDirectory(prefix="wall-video-test-") as temporary:
            temp = Path(temporary)
            source = temp / "fixture.mp4"
            run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=30:duration=1.25",
                 "-f", "lavfi", "-i", "sine=frequency=440:duration=1.25", "-c:v", "mpeg4", "-c:a", "aac", "-shortest", str(source)])
            result = build(source, temp / "output")
            self.assertTrue(result["source"]["hasAudio"])
            self.assertGreater(result["output"]["frameCount"], 20)
            self.assertEqual(result["output"]["atlasCount"], 1)
            silent = temp / "silent.mp4"
            run(["ffmpeg", "-v", "error", "-i", str(source), "-an", "-c:v", "copy", str(silent)])
            result = build(silent, temp / "output")
            self.assertFalse(result["source"]["hasAudio"])
            self.assertIsNone(result["output"]["sound"])
            self.assertFalse((temp / "output/minecraft/resource_packs/ichiyon_avatar_rp/sounds/video_screen/audio.ogg").exists())
            with self.assertRaises(ValueError):
                build(source, temp)

    def test_original_regeneration(self):
        if SOURCE is None:
            self.skipTest("Pass --source to verify the external original MP4")
        expected = read(ROOT / "minecraft/video_screen/build_report.json")
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(), expected["source"]["sha256"])
        with tempfile.TemporaryDirectory(prefix="wall-original-test-") as temporary:
            root = Path(temporary)
            actual = build(SOURCE, root)
            self.assertEqual(actual, expected)
            for path in (RP / "textures/entity/video_screen").glob("*.png"):
                with Image.open(path) as a, Image.open(root / path.relative_to(ROOT)) as b:
                    self.assertIsNone(ImageChops.difference(a, b).getbbox())
            audio = RP / "sounds/video_screen/audio.ogg"
            self.assertEqual(audio.read_bytes(), (root / audio.relative_to(ROOT)).read_bytes())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path)
    args, remainder = parser.parse_known_args()
    SOURCE = args.source
    unittest.main(argv=[__file__, *remainder])
