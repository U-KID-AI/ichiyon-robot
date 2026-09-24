"""Compile the built-in catalog, or additional local assets, without touching a server."""
import argparse
from io import BytesIO
import json
from pathlib import Path
import sys

from PIL import Image, UnidentifiedImageError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bot.services.minecraft_cosmetics import asset
from bot.services.minecraft_cosmetics_pack import builtin_assets, compile_files, pack_zip


def png_pixels_equal(actual, expected):
    """Ignore encoding/text metadata, but retain pixel and rendering semantics."""
    try:
        with Image.open(BytesIO(actual)) as existing, Image.open(BytesIO(expected)) as generated:
            if (existing.format != "PNG" or generated.format != "PNG"
                    or existing.size != generated.size or existing.mode != generated.mode
                    or getattr(existing, "n_frames", 1) != 1 or getattr(generated, "n_frames", 1) != 1):
                return False
            existing.load()
            generated.load()
            # Color interpretation and orientation can change rendering without changing samples.
            for key in ("icc_profile", "gamma", "srgb", "chromaticity"):
                if existing.info.get(key) != generated.info.get(key):
                    return False
            if existing.getexif().get(274, 1) != generated.getexif().get(274, 1):
                return False
            if existing.mode.startswith("I") and existing.tobytes() != generated.tobytes():
                return False  # RGBA conversion would discard high-bit-depth differences.
            return existing.convert("RGBA").tobytes() == generated.convert("RGBA").tobytes()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        return False


def generated_matches(name, actual, expected):
    if name.endswith(".png"):
        return png_pixels_equal(actual, expected)
    return actual.replace(b"\r\n", b"\n") == expected


def local_catalog(path):
    """File paths in the optional source catalog are confined to its directory."""
    base = path.resolve().parent
    def read(value):
        target = (base / value).resolve()
        if not target.is_relative_to(base) or not target.is_file():
            raise ValueError("素材ファイルはカタログと同じフォルダー内に置いてください。")
        return target.read_bytes()
    output = []
    for row in json.loads(path.read_text(encoding="utf-8")):
        options = {key: row[key] for key in ("model", "slot", "width", "height") if key in row}
        options.update({key: read(row[key]) for key in ("geometry", "icon") if key in row})
        output.append(asset(row["kind"], row["id"], row["key"], row["name"], read(row["texture"]), **options))
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="Update generated files in this checkout")
    mode.add_argument("--check", action="store_true", help="Check built-in generated files are current")
    mode.add_argument("--zip", type=Path, help="Write a complete pack archive to a NEW file")
    parser.add_argument("--assets", type=Path, help="Optional additional local asset catalog")
    parser.add_argument("--revision", type=int, help="Required for ZIP; choose a higher number than the last export")
    args = parser.parse_args()
    minecraft = ROOT / "minecraft"
    records = builtin_assets(minecraft)
    if args.assets:
        records.extend(local_catalog(args.assets))
    if args.zip:
        if args.revision is None:
            parser.error("--zip requires --revision")
        data = pack_zip(minecraft, records, args.revision)
        with args.zip.open("xb") as output:
            output.write(data)
        print("Cosmetics archive created")
        return
    generated = compile_files(minecraft, records)
    failed = []
    for name, data in generated.items():
        path = minecraft / name
        matches = path.is_file() and generated_matches(name, path.read_bytes(), data)
        if args.write:
            if name.endswith(".png") and matches:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        elif not matches:
            failed.append(name)
    if failed:
        raise SystemExit("Generated files differ: " + ", ".join(failed))
    print(f"Cosmetics compile {'written' if args.write else 'verified'}: {len(generated)} files")


if __name__ == "__main__":
    main()
