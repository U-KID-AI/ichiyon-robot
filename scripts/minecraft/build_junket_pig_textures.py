"""Generate consistently styled, locally authored Peaky Pig Palace props."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
TEXTURES = ROOT / "minecraft/resource_packs/ichiyon_junket_rp/textures/entity/junket"
SIZE = 64
INK = (32, 37, 43, 255)
CREAM = (242, 232, 210, 255)
GOLD = (217, 164, 54, 255)


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def card(path: Path, label: str, accent: tuple[int, int, int, int],
         face: tuple[int, int, int, int] = CREAM, symbol: str = "") -> None:
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((3, 3, 60, 60), radius=5, fill=INK)
    draw.rounded_rectangle((6, 6, 57, 57), radius=3, fill=face, outline=accent, width=3)
    draw.rectangle((9, 9, 54, 21), fill=accent)
    draw.text((31, 15), label, fill=INK, anchor="mm", font=font(9))
    if symbol:
        draw.text((31, 38), symbol, fill=accent, anchor="mm", font=font(27))
    draw.rectangle((9, 50, 54, 53), fill=accent)
    image.save(path)


def target(path: Path, label: str, accent: tuple[int, int, int, int],
           ready: bool) -> None:
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((2, 7, 61, 56), radius=6, fill=INK)
    draw.rounded_rectangle((5, 10, 58, 53), radius=4,
                           fill=(226, 205, 161, 255) if ready else (90, 84, 72, 255),
                           outline=accent, width=3 if ready else 1)
    draw.ellipse((20, 17, 44, 41), fill=accent if ready else (115, 106, 91, 255))
    draw.ellipse((26, 23, 38, 35), fill=CREAM if ready else (60, 57, 51, 255))
    draw.text((31, 48), label, fill=CREAM if ready else (188, 178, 156, 255),
              anchor="mm", font=font(9))
    image.save(path)


def television(path: Path) -> None:
    image = Image.new("RGBA", (126, 72), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((1, 1, 124, 70), radius=6, fill=INK)
    draw.rectangle((7, 7, 118, 64), fill=(21, 52, 61, 255), outline=GOLD, width=2)
    for y in range(11, 61, 5):
        draw.line((10, y, 115, y), fill=(31, 92, 102, 255), width=1)
    draw.text((62, 24), "PIG PALACE", fill=(255, 222, 129, 255),
              anchor="mm", font=font(16))
    draw.text((62, 47), "4 PLAYERS / TEAMS 1+4 vs 2+3", fill=CREAM,
              anchor="mm", font=font(7))
    image.save(path)


def main() -> None:
    TEXTURES.mkdir(parents=True, exist_ok=True)
    card(TEXTURES / "pig_card_available.png", "CHOOSE", (47, 104, 128, 255), symbol="?")
    card(TEXTURES / "pig_card_hidden.png", "SEALED", (86, 70, 45, 255),
         face=(184, 166, 127, 255), symbol="◆")
    card(TEXTURES / "pig_card_straw.png", "STRAW", (198, 154, 48, 255), symbol="≈")
    card(TEXTURES / "pig_card_wood.png", "WOOD", (121, 76, 39, 255), symbol="▥")
    card(TEXTURES / "pig_card_brick.png", "BRICK", (139, 60, 47, 255), symbol="▦")
    card(TEXTURES / "pig_card_wolf.png", "WOLF", (72, 77, 88, 255), symbol="W")
    card(TEXTURES / "pig_card_used.png", "USED", (91, 91, 91, 255),
         face=(150, 150, 150, 255), symbol="×")
    target(TEXTURES / "pig_target_idle.png", "HOLD", (119, 112, 97, 255), False)
    target(TEXTURES / "pig_target_ready.png", "ATTACK", (188, 52, 44, 255), True)
    target(TEXTURES / "pig_target_spent.png", "HIT", (73, 69, 63, 255), False)
    television(TEXTURES / "pig_tv.png")


if __name__ == "__main__":
    main()
