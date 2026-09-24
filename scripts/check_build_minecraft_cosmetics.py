"""Builder checks use synthetic files in temporary directories, never generated repo assets."""
from contextlib import redirect_stdout
from io import BytesIO, StringIO
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, PngImagePlugin

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts import build_minecraft_cosmetics as builder
from bot.services.minecraft_cosmetics import png_bytes, poster_texture
from bot.services.minecraft_cosmetics_pack import builtin_assets, catalog_digest


def png(image=None, *, compression=6, metadata=None, exif=None):
    output = BytesIO()
    options = {"compress_level": compression}
    if metadata is not None:
        options["pnginfo"] = metadata
    if exif is not None:
        options["exif"] = exif
    if image is None:
        image = Image.new("RGBA", (64, 64), (80, 160, 240, 255))
    image.save(output, format="PNG", **options)
    return output.getvalue()


class BuilderChecks(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.minecraft = self.root / "minecraft"
        self.minecraft.mkdir()
        self.path = self.minecraft / "texture.png"
        self.old = png(compression=0)
        self.expected = png(compression=9)
        self.assertNotEqual(self.old, self.expected)

    def run_builder(self, generated, mode="--check"):
        with patch.object(builder, "ROOT", self.root), \
                patch.object(builder, "builtin_assets", return_value=[]), \
                patch.object(builder, "compile_files", return_value=generated), \
                patch.object(sys, "argv", ["build_minecraft_cosmetics.py", mode]), \
                redirect_stdout(StringIO()):
            builder.main()

    def test_check_accepts_compression_only_difference_without_writes(self):
        self.path.write_bytes(self.old)
        with patch.object(Path, "write_bytes", side_effect=AssertionError("check wrote a file")):
            self.run_builder({"texture.png": self.expected})
        self.assertEqual(self.path.read_bytes(), self.old)

    def test_write_preserves_compression_only_existing_bytes_without_writing(self):
        self.path.write_bytes(self.old)
        with patch.object(Path, "write_bytes", side_effect=AssertionError("identical pixels rewritten")):
            self.run_builder({"texture.png": self.expected}, "--write")
        self.assertEqual(self.path.read_bytes(), self.old)

    def test_changed_color_alpha_and_transparent_rgb_fail_check_and_are_written(self):
        for pixel in ((81, 160, 240, 255), (80, 160, 240, 254), (81, 160, 240, 0)):
            with self.subTest(pixel=pixel):
                image = Image.new("RGBA", (64, 64), (80, 160, 240, 255))
                image.putpixel((31, 42), pixel)
                different = png(image)
                self.path.write_bytes(different)
                with self.assertRaisesRegex(SystemExit, "Generated files differ: texture.png"):
                    self.run_builder({"texture.png": self.expected})
                self.assertEqual(self.path.read_bytes(), different)
                self.run_builder({"texture.png": self.expected}, "--write")
                self.assertEqual(self.path.read_bytes(), self.expected)
        left = Image.new("RGBA", (1, 1), (1, 2, 3, 0))
        right = Image.new("RGBA", (1, 1), (4, 5, 6, 0))
        self.assertFalse(builder.png_pixels_equal(png(left), png(right)))

    def test_dimensions_and_mode_must_match(self):
        for image in (Image.new("RGBA", (32, 128), (80, 160, 240, 255)),
                      Image.new("RGB", (64, 64), (80, 160, 240))):
            with self.subTest(mode=image.mode, size=image.size):
                self.path.write_bytes(png(image))
                with self.assertRaisesRegex(SystemExit, "texture.png"):
                    self.run_builder({"texture.png": self.expected})

    def test_descriptive_metadata_is_ignored_but_rendering_metadata_is_not(self):
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("Comment", "Different encoder, same pixels")
        self.assertTrue(builder.png_pixels_equal(png(metadata=metadata), self.expected))
        gamma = PngImagePlugin.PngInfo()
        gamma.add(b"gAMA", (45455).to_bytes(4, "big"))
        self.assertFalse(builder.png_pixels_equal(png(metadata=gamma), self.expected))
        orientation = Image.Exif()
        orientation[274] = 6
        self.assertFalse(builder.png_pixels_equal(png(exif=orientation), self.expected))

    def test_high_bit_depth_differences_are_not_lost_in_rgba_conversion(self):
        left = Image.new("I;16", (1, 1), 1000)
        right = Image.new("I;16", (1, 1), 1001)
        self.assertEqual(left.convert("RGBA").tobytes(), right.convert("RGBA").tobytes())
        self.assertFalse(builder.png_pixels_equal(png(left), png(right)))

    def test_corrupt_non_png_animated_and_missing_files_fail_check(self):
        animated = BytesIO()
        Image.new("RGBA", (64, 64), "red").save(animated, format="PNG", save_all=True,
            append_images=[Image.new("RGBA", (64, 64), "blue")], duration=100, loop=0)
        jpeg = BytesIO()
        Image.new("RGB", (64, 64)).save(jpeg, format="JPEG")
        for invalid in (b"invalid", self.expected[:40], jpeg.getvalue(), animated.getvalue()):
            with self.subTest(size=len(invalid)):
                self.path.write_bytes(invalid)
                with self.assertRaisesRegex(SystemExit, "texture.png"):
                    self.run_builder({"texture.png": self.expected})
        self.path.unlink()
        with self.assertRaisesRegex(SystemExit, "texture.png"):
            self.run_builder({"texture.png": self.expected})
        self.run_builder({"texture.png": self.expected}, "--write")
        self.assertEqual(self.path.read_bytes(), self.expected)

    def test_non_png_json_catalog_and_digest_checks_remain_exact(self):
        expected = b'{"digest":"expected","posters":[]}\n'
        for name in ("manifest.json", "catalog.lock.json", "cosmetics_catalog.js"):
            path = self.minecraft / name
            for different in (b'{ "digest": "expected", "posters": [] }\n',
                              b'{"digest":"changed","posters":[]}\n'):
                with self.subTest(name=name, different=different):
                    path.write_bytes(different)
                    with self.assertRaisesRegex(SystemExit, name):
                        self.run_builder({name: expected})
                    self.run_builder({name: expected}, "--write")
                    self.assertEqual(path.read_bytes(), expected)
            path.write_bytes(expected.replace(b"\n", b"\r\n"))
            self.run_builder({name: expected})

    def test_validated_canonical_blobs_are_preserved_before_fingerprinting(self):
        for compression in (0, 9):
            data = png(compression=compression)
            self.assertEqual(png_bytes(data, skin=True), data)
            self.assertEqual(poster_texture(data, 1, 1), data)
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("Comment", "Normalize this upload")
        original = png(metadata=metadata)
        normalized = png_bytes(original, skin=True)
        self.assertNotEqual(original, normalized)
        self.assertEqual(png_bytes(normalized, skin=True), normalized)
        with Image.open(BytesIO(normalized)) as image:
            self.assertFalse(image.info)
        with self.assertRaises(ValueError):
            png_bytes(png(Image.new("RGBA", (32, 32))), skin=True)
        with self.assertRaises(ValueError):
            png_bytes(self.expected[:40], skin=True)

    def test_committed_builtin_digest_is_independent_of_local_png_encoder(self):
        import json
        minecraft = ROOT / "minecraft"
        records = builtin_assets(minecraft)
        catalog = json.loads((minecraft / "cosmetics/catalog.lock.json").read_bytes())
        self.assertEqual(catalog_digest(records), catalog["digest"])
        # Canonical originals must be validated without asking the local encoder to replace them.
        with patch.object(Image.Image, "save", side_effect=AssertionError("canonical PNG reencoded")):
            self.assertEqual(builtin_assets(minecraft), records)


if __name__ == "__main__":
    unittest.main()
