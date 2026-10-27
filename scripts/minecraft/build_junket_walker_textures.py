"""Generate independently authored Walker floor labels and bomb texture.

The Java source uses green-wool item displays labelled by kana groups.  The
labels change when a tile is left (the final vowel of each row) and bombs are
shown on selected tiles.  These PNGs are drawn from original stroke data in
this repository: no Java resource-pack pixels, models, or system font glyph
outlines are copied or redistributed.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "minecraft/resource_packs/ichiyon_junket_rp/textures/entity/junket"

# Pen coordinates are expressed on a 16x16 design grid.  Each stroke is drawn
# with round joins so the result remains legible when mapped onto a 4.4-block
# floor tile.
GLYPHS: dict[str, list[list[tuple[float, float]]]] = {
    "a": [
        [(3, 5), (13, 5)],
        [(9, 2), (9, 7), (7.5, 10), (6, 12)],
        [(4, 8), (12, 7), (13, 9), (11, 11), (7, 12), (4, 11)],
        [(10.5, 11), (10.5, 14), (8, 15)],
    ],
    "ka": [
        [(3, 4), (11, 4)],
        [(9, 2), (9, 7), (12, 8)],
        [(11, 5), (5, 14)],
        [(4, 8), (7, 10)],
    ],
    "sa": [
        [(4, 4), (12, 4)],
        [(11, 5), (5, 12)],
        [(4, 10), (10, 9), (12.5, 11), (10, 14), (5, 14)],
    ],
    "ta": [
        [(3, 4), (12, 4)],
        [(5, 2), (5, 9)],
        [(4, 8), (10, 8)],
        [(12, 5), (9, 9), (10, 13)],
    ],
    "na": [
        [(3, 4), (12, 4)],
        [(8, 2), (8, 8)],
        [(9, 7), (5, 12)],
        [(5, 9), (12, 9), (13, 11), (11, 13), (7, 13), (6, 11)],
        [(10, 12), (10, 15)],
    ],
    "ha": [
        [(5, 3), (5, 14), (7, 13)],
        [(4, 6), (12, 6)],
        [(9, 9), (12, 9), (13, 11), (11, 13), (8, 13), (9, 10)],
        [(11, 13), (11, 15)],
    ],
    "ma": [
        [(4, 4), (12, 4)],
        [(4, 8), (12, 8)],
        [(8, 2), (8, 14), (10, 13)],
        [(5, 11), (12, 11), (12.5, 13), (8, 14), (5, 12)],
    ],
    "ya": [
        [(5, 3), (8, 7), (10, 10)],
        [(4, 7), (9, 6), (12, 7)],
        [(10, 6), (10, 14), (12, 13)],
    ],
    "ra": [
        [(6, 3), (10, 3)],
        [(4, 6), (12, 6)],
        [(11, 7), (7, 12), (11, 14), (13, 12)],
    ],
    "one": [
        [(5, 6), (9, 3), (9, 14)],
        [(5, 14), (13, 14)],
    ],
    "six": [
        [(10, 3), (6, 7), (5, 12)],
        [(5, 10), (9, 8), (13, 10), (12, 14), (7, 14), (4, 11), (5.5, 8)],
    ],
    "o": [
        [(3, 4), (13, 4)],
        [(8, 2), (8, 8), (6, 13)],
        [(5, 7), (12, 7), (13, 9), (11, 12), (7, 12)],
        [(3, 7), (4, 7)],
    ],
    "ko": [
        [(11, 4), (6, 5), (5, 6)],
        [(5, 10), (12, 9), (12, 12), (5, 13)],
    ],
    "so": [
        [(11, 3), (5, 9)],
        [(5, 8), (12, 8)],
        [(12, 9), (8, 12), (11, 14), (13, 12)],
    ],
    "to": [
        [(7, 4), (8, 6)],
        [(9, 5), (7, 8), (6, 12), (12, 13)],
    ],
    "no": [
        [(6, 4), (10, 6)],
        [(10, 7), (13, 10), (9, 14), (5, 12), (5, 8), (9, 6)],
    ],
    "ho": [
        [(5, 3), (5, 14), (7, 13)],
        [(4, 6), (12, 6)],
        [(4, 9), (12, 9)],
        [(9, 11), (12, 10), (13, 12), (10, 14), (8, 12)],
        [(11, 13), (11, 15)],
    ],
    "mo": [
        [(5, 4), (11, 4)],
        [(4, 8), (12, 8)],
        [(9, 2), (9, 14), (11, 13)],
        [(9, 8), (6, 12), (9, 14), (12, 13)],
    ],
    "yo": [
        [(5, 7), (12, 7)],
        [(8, 2), (8, 14), (10, 13)],
        [(10, 8), (13, 9), (11, 12), (8, 12)],
    ],
    "ro": [
        [(6, 3), (11, 3)],
        [(4, 6), (12, 6)],
        [(11, 7), (7, 12), (11, 14), (13, 12)],
    ],
    "five": [
        [(11, 3), (5, 3)],
        [(5, 4), (5, 8)],
        [(5, 8), (12, 8), (13, 11), (10, 14), (5, 13)],
    ],
    "zero": [
        [(5, 4), (10, 3), (13, 6), (13, 12), (9, 14), (5, 12), (4, 7), (5, 4)],
    ],
}

ORDER = (
    "a", "ka", "sa", "ta", "na", "ha", "ma", "ya", "ra", "one", "six",
    "o", "ko", "so", "to", "no", "ho", "mo", "yo", "ro", "five", "zero",
)


def design_to_pixel(x: float, y: float) -> tuple[float, float]:
    # Leave a 6px margin around the 64px canvas and keep stroke ends clear.
    return 5 + x * 3.25, 5 + y * 3.25


def draw_glyph(image: Image.Image, name: str) -> None:
    draw = ImageDraw.Draw(image)
    strokes = [
        [design_to_pixel(x, y) for x, y in stroke]
        for stroke in GLYPHS[name]
    ]
    # Shadow/outline uses the same authored geometry, never a font rasterizer.
    for offset, color, width in (
        ((1.4, 2.0), (10, 16, 28, 220), 10),
        ((0, 0), (255, 247, 206, 255), 6),
    ):
        for stroke in strokes:
            shifted = [(x + offset[0], y + offset[1]) for x, y in stroke]
            if len(shifted) > 1:
                draw.line(shifted, fill=color, width=width, joint="curve")
            for x, y in shifted:
                radius = width / 2
                draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)


def label(name: str) -> None:
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    # A subtle original tile inset helps the label read against every floor
    # colour while remaining transparent outside the 4.4-block centre.
    draw.rounded_rectangle((2, 2, 61, 61), radius=9, fill=(20, 34, 31, 72))
    draw_glyph(image, name)
    image.save(OUT / f"walker_label_{name}.png")


def bomb() -> None:
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((9, 14, 55, 60), fill=(28, 30, 38, 255), outline=(3, 4, 7, 255), width=5)
    draw.ellipse((18, 23, 31, 36), fill=(108, 112, 126, 255))
    draw.ellipse((22, 27, 27, 32), fill=(174, 179, 191, 255))
    draw.line((38, 15, 47, 5), fill=(202, 160, 70, 255), width=6)
    draw.ellipse((45, 1, 56, 12), fill=(255, 168, 36, 255), outline=(112, 61, 9, 255), width=2)
    draw.ellipse((49, 4, 53, 8), fill=(255, 247, 186, 255))
    image.save(OUT / "walker_bomb.png")


def main() -> None:
    if set(ORDER) != set(GLYPHS):
        raise ValueError("Walker label order and glyph definitions differ")
    OUT.mkdir(parents=True, exist_ok=True)
    for name in ORDER:
        label(name)
    bomb()
    print(f"generated {len(ORDER) + 1} Walker textures from repository stroke data")


if __name__ == "__main__":
    main()
