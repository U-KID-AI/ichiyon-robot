"""Read-only checks for the selected record assets, integration and runtime."""
from array import array
import hashlib
import json
import math
from pathlib import Path
import subprocess
import unittest

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
BP = ROOT / "minecraft/behavior_packs/ichiyon_avatar_bp"
RP = ROOT / "minecraft/resource_packs/ichiyon_avatar_rp"
ITEM = "ichiyon:record_lets_cooking_molcar"
SOUND = "ichiyon:record.lets_cooking_molcar"
SOURCE_SHA256 = "692eff62aa1d9566d3af7d5286cfeaaa65c2ce279c441c5c8a167b62d7aef113"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def command(*args):
    return subprocess.check_output(args, cwd=ROOT)


class MolcarRecordChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tracks = json.loads(command("node", "--input-type=module", "-e",
            "import { molcarRecords } from './minecraft/behavior_packs/import_structures/scripts/molcar_records_catalog.js'; "
            "console.log(JSON.stringify(molcarRecords));"))
        selected = [track for track in tracks if track["itemId"] == ITEM]
        if len(selected) != 1:
            raise AssertionError("Expected exactly one imported Let's Cooking Molcar track")
        cls.track = selected[0]
        cls.audio = RP / "sounds/records/lets_cooking_molcar.ogg"
        cls.icon = RP / "textures/items/record_lets_cooking_molcar.png"

    def test_exact_original_audio_and_catalog_hashes(self):
        digest = hashlib.sha256(self.audio.read_bytes()).hexdigest()
        self.assertEqual(digest, SOURCE_SHA256, "The selected original OGG must not be silently replaced or re-encoded")
        self.assertEqual(self.track["audioSha256"], digest)
        self.assertEqual(self.track["iconSha256"], hashlib.sha256(self.icon.read_bytes()).hexdigest())
        self.assertEqual(self.track["soundId"], SOUND)

    def test_audio_codec_duration_and_non_silent_samples(self):
        probe = json.loads(command("ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(self.audio)))
        streams = [stream for stream in probe["streams"] if stream["codec_type"] == "audio"]
        self.assertEqual(len(streams), 1)
        self.assertEqual(streams[0]["codec_name"], "vorbis")
        self.assertEqual(streams[0]["channels"], 2)
        self.assertEqual(int(streams[0]["sample_rate"]), 44100)
        self.assertAlmostEqual(float(probe["format"]["duration"]), self.track["durationSeconds"], places=5)
        self.assertAlmostEqual(self.track["durationSeconds"], 261.247710, places=5)
        pcm = command("ffmpeg", "-v", "error", "-ss", "30", "-i", str(self.audio), "-t", "5", "-ac", "1", "-ar", "44100", "-f", "s16le", "-")
        samples = array("h", pcm)
        self.assertGreater(len(samples), 44100)
        rms = math.sqrt(sum(int(sample) ** 2 for sample in samples) / len(samples))
        self.assertGreater(rms, 1, "Decoded sample must not be a silent placeholder")

    def test_selected_png_decodes_has_visible_content_and_transparency(self):
        with Image.open(self.icon) as icon:
            self.assertEqual(icon.format, "PNG")
            icon.verify()
        with Image.open(self.icon) as icon:
            rgba = icon.convert("RGBA")
            self.assertGreater(rgba.width, 0)
            self.assertGreater(rgba.height, 0)
            alpha = rgba.getchannel("A")
            low, high = alpha.getextrema()
            self.assertEqual(low, 0, "The selected fixed icon should retain its transparent background")
            self.assertGreater(high, 0)
            self.assertIsNotNone(alpha.getbbox())
            self.assertGreater(len(set(rgba.getdata())), 2, "Icon cannot be blank or a single-color placeholder")

    def test_item_sound_texture_and_language_registration(self):
        item = read(BP / "items/record_lets_cooking_molcar.json")["minecraft:item"]
        self.assertEqual(item["description"]["identifier"], ITEM)
        self.assertEqual(item["description"]["menu_category"]["category"], "items")
        components = item["components"]
        self.assertEqual(components["minecraft:max_stack_size"], 1)
        self.assertEqual(components["minecraft:icon"], ITEM)
        self.assertNotIn("minecraft:food", components)
        self.assertNotIn("minecraft:use_remainder", components)
        self.assertEqual(read(RP / "textures/item_texture.json")["texture_data"][ITEM]["textures"], "textures/items/record_lets_cooking_molcar")
        definition = read(RP / "sounds/sound_definitions.json")["sound_definitions"][SOUND]
        self.assertEqual(definition["category"], "record")
        self.assertEqual(definition["sounds"], [{"name": "sounds/records/lets_cooking_molcar", "stream": True}])
        for locale in ["ja_JP", "en_US"]:
            lines = (RP / f"texts/{locale}.lang").read_text(encoding="utf-8").splitlines()
            self.assertEqual([line for line in lines if line.startswith(f"item.{ITEM}.name=")],
                             [f"item.{ITEM}.name=Let's Cooking Molcar"])

    def test_runtime_regressions(self):
        subprocess.run(["node", str(ROOT / "scripts/check_molcar_records.mjs")], check=True, cwd=ROOT)


if __name__ == "__main__":
    unittest.main()
