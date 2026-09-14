import json
import sys
from pathlib import Path

from PIL import Image


ROOT_DIR = Path(__file__).resolve().parent.parent

POSTERS = {
    "poster_irsia": {
        "source": ROOT_DIR / "assets" / "minecraft" / "posters" / "irsia_source.jpg",
        "texture": ROOT_DIR
        / "minecraft"
        / "resource_packs"
        / "ichiyon_avatar_rp"
        / "textures"
        / "blocks"
        / "poster_irsia.png",
        "geometry": ROOT_DIR
        / "minecraft"
        / "resource_packs"
        / "ichiyon_avatar_rp"
        / "models"
        / "blocks"
        / "poster_irsia.geo.json",
        "block": ROOT_DIR
        / "minecraft"
        / "behavior_packs"
        / "ichiyon_avatar_bp"
        / "blocks"
        / "poster_irsia.json",
        "loot": ROOT_DIR
        / "minecraft"
        / "behavior_packs"
        / "ichiyon_avatar_bp"
        / "loot_tables"
        / "blocks"
        / "poster_irsia.json",
        "identifier": "ichiyon:poster_irsia",
        "geometry_id": "geometry.poster_irsia",
        "command_text": "イルシアポスター",
        "label": "イルシアポスター",
    },
    "poster_raio": {
        "source": ROOT_DIR / "assets" / "minecraft" / "posters" / "raio_source.jpg",
        "texture": ROOT_DIR
        / "minecraft"
        / "resource_packs"
        / "ichiyon_avatar_rp"
        / "textures"
        / "blocks"
        / "poster_raio.png",
        "geometry": ROOT_DIR
        / "minecraft"
        / "resource_packs"
        / "ichiyon_avatar_rp"
        / "models"
        / "blocks"
        / "poster_raio.geo.json",
        "block": ROOT_DIR
        / "minecraft"
        / "behavior_packs"
        / "ichiyon_avatar_bp"
        / "blocks"
        / "poster_raio.json",
        "loot": ROOT_DIR
        / "minecraft"
        / "behavior_packs"
        / "ichiyon_avatar_bp"
        / "loot_tables"
        / "blocks"
        / "poster_raio.json",
        "identifier": "ichiyon:poster_raio",
        "geometry_id": "geometry.poster_raio",
        "command_text": "ライオポスター",
        "label": "ライオポスター",
    },
}


def check(name, ok, detail=""):
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


def read_json(path):
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def aspect_ratio(path):
    with Image.open(path) as image:
        return image.size, image.size[0] / image.size[1]


def check_manifest_version(results, label, path, expected):
    manifest = read_json(path)
    header_version = manifest["header"]["version"]
    module_versions = [module["version"] for module in manifest["modules"]]
    results.append(
        check(
            "{0} header version".format(label),
            header_version == expected,
            str(header_version),
        )
    )
    results.append(
        check(
            "{0} module versions match header".format(label),
            all(version == header_version for version in module_versions),
            str(module_versions),
        )
    )


def main():
    results = []
    check_manifest_version(
        results,
        "Avatar BP",
        ROOT_DIR / "minecraft" / "behavior_packs" / "ichiyon_avatar_bp" / "manifest.json",
        [1, 0, 4],
    )
    check_manifest_version(
        results,
        "Avatar RP",
        ROOT_DIR / "minecraft" / "resource_packs" / "ichiyon_avatar_rp" / "manifest.json",
        [1, 0, 3],
    )
    check_manifest_version(
        results,
        "Imported Structures",
        ROOT_DIR / "minecraft" / "behavior_packs" / "import_structures" / "manifest.json",
        [1, 0, 1],
    )
    terrain = read_json(
        ROOT_DIR
        / "minecraft"
        / "resource_packs"
        / "ichiyon_avatar_rp"
        / "textures"
        / "terrain_texture.json"
    )
    blocks = read_json(
        ROOT_DIR / "minecraft" / "resource_packs" / "ichiyon_avatar_rp" / "blocks.json"
    )
    service = (
        ROOT_DIR / "bot" / "services" / "minecraft_bridge.py"
    ).read_text(encoding="utf-8")
    repository = (
        ROOT_DIR / "bot" / "repositories" / "minecraft_bridge.py"
    ).read_text(encoding="utf-8")
    bridge_script = (
        ROOT_DIR
        / "minecraft"
        / "behavior_packs"
        / "import_structures"
        / "scripts"
        / "main.js"
    ).read_text(encoding="utf-8")
    migration = (ROOT_DIR / "migrations" / "052_add_minecraft_poster_commands.sql").read_text(
        encoding="utf-8"
    )
    ja_lang = (
        ROOT_DIR / "minecraft" / "resource_packs" / "ichiyon_avatar_rp" / "texts" / "ja_JP.lang"
    ).read_text(encoding="utf-8")
    en_lang = (
        ROOT_DIR / "minecraft" / "resource_packs" / "ichiyon_avatar_rp" / "texts" / "en_US.lang"
    ).read_text(encoding="utf-8")

    results.append(
        check(
            "existing Taketumi geometry is preserved",
            (
                ROOT_DIR
                / "minecraft"
                / "resource_packs"
                / "ichiyon_avatar_rp"
                / "models"
                / "entity"
                / "taketumi.geo.json"
            ).exists(),
        )
    )
    results.append(
        check(
            "existing Taketumi texture is preserved",
            (
                ROOT_DIR
                / "minecraft"
                / "resource_packs"
                / "ichiyon_avatar_rp"
                / "textures"
                / "entity"
                / "taketumi.png"
            ).exists(),
        )
    )
    results.append(
        check(
            "existing Taketumi animation is preserved",
            (
                ROOT_DIR
                / "minecraft"
                / "resource_packs"
                / "ichiyon_avatar_rp"
                / "animations"
                / "taketumi.animation.json"
            ).exists(),
        )
    )

    for command_type, spec in POSTERS.items():
        source_size, source_ratio = aspect_ratio(spec["source"])
        texture_size, texture_ratio = aspect_ratio(spec["texture"])
        block = read_json(spec["block"])["minecraft:block"]
        geometry = read_json(spec["geometry"])["minecraft:geometry"][0]
        loot = read_json(spec["loot"])
        components = block["components"]
        cube = geometry["bones"][0]["cubes"][0]
        conditions = components["minecraft:placement_filter"]["conditions"]
        permutations = block["permutations"]

        results.append(check("{0} source exists".format(command_type), spec["source"].exists()))
        results.append(check("{0} png exists".format(command_type), spec["texture"].exists()))
        results.append(
            check(
                "{0} png long side is 512".format(command_type),
                max(texture_size) == 512,
                str(texture_size),
            )
        )
        results.append(
            check(
                "{0} aspect ratio preserved".format(command_type),
                abs(source_ratio - texture_ratio) < 0.003,
                "{0} -> {1}".format(source_size, texture_size),
            )
        )
        results.append(
            check(
                "{0} block identifier".format(command_type),
                block["description"]["identifier"] == spec["identifier"],
            )
        )
        results.append(
            check(
                "{0} block geometry reference".format(command_type),
                components["minecraft:geometry"] == spec["geometry_id"],
            )
        )
        results.append(
            check(
                "{0} no collision".format(command_type),
                components["minecraft:collision_box"] is False,
            )
        )
        results.append(
            check(
                "{0} light dampening zero".format(command_type),
                components["minecraft:light_dampening"] == 0,
            )
        )
        results.append(
            check(
                "{0} has thin selection box".format(command_type),
                components["minecraft:selection_box"]["size"][2] <= 0.5,
            )
        )
        results.append(
            check(
                "{0} visual geometry is paper thin".format(command_type),
                cube["origin"][2] == 7.96875 and cube["size"][2] == 0.03125,
                "origin_z={0}, size_z={1}".format(cube["origin"][2], cube["size"][2]),
            )
        )
        results.append(
            check(
                "{0} wall-face placement filter".format(command_type),
                conditions == [{"allowed_faces": ["north", "south", "east", "west"]}],
            )
        )
        results.append(
            check(
                "{0} cardinal rotation permutations".format(command_type),
                sorted(
                    condition["condition"].split(" == ")[-1].strip("'")
                    for condition in permutations
                )
                == ["east", "north", "south", "west"],
            )
        )
        results.append(
            check(
                "{0} self drop loot".format(command_type),
                loot["pools"][0]["entries"][0]["name"] == spec["identifier"],
            )
        )
        results.append(
            check(
                "{0} terrain texture registered".format(command_type),
                terrain["texture_data"][command_type]["textures"]
                == "textures/blocks/{0}".format(command_type),
            )
        )
        results.append(
            check(
                "{0} blocks.json registered".format(command_type),
                blocks[spec["identifier"]]["textures"] == command_type,
            )
        )
        results.append(
            check(
                "{0} geometry identifier".format(command_type),
                geometry["description"]["identifier"] == spec["geometry_id"],
            )
        )
        results.append(
            check(
                "{0} geometry texture size matches PNG".format(command_type),
                geometry["description"]["texture_width"] == texture_size[0]
                and geometry["description"]["texture_height"] == texture_size[1],
            )
        )
        results.append(
            check(
                "{0} localized in ja_JP".format(command_type),
                "tile.{0}.name={1}".format(spec["identifier"], spec["label"]) in ja_lang,
            )
        )
        results.append(
            check(
                "{0} localized in en_US".format(command_type),
                "tile.{0}.name={1}".format(spec["identifier"], spec["label"]) in en_lang,
            )
        )
        results.append(
            check(
                "{0} Discord parser command".format(command_type),
                spec["command_text"] in service and command_type in service,
            )
        )
        results.append(
            check(
                "{0} repository allow-list".format(command_type),
                command_type in repository and command_type in migration,
            )
        )
        results.append(
            check(
                "{0} Bridge Script fixed item".format(command_type),
                command_type in bridge_script and spec["identifier"] in bridge_script,
            )
        )

    results.append(check("no arbitrary poster item input", "command.item" not in bridge_script))
    results.append(check("poster migration keeps previous taketumi command", "taketumi_remove_near_player" in migration))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
