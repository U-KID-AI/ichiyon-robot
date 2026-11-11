# Companion and equipment source / private asset boundary

The public repository saves the authored behavior, runtime, conversion code and
synthetic tests for Ikarun, Tumaini and Narita glasses. It does not contain the
supplied Ikarun/Tumaini structures, their generated meshes or textures, vanilla
material cache, private deployment candidates, or production evidence.

The public default pack does **not** register a companion whose visual assets are
absent. `scripts/templates/structure_companions/` holds optional BP definitions;
the Tumaini generator derives combat/home behavior from the current Taketsumi
definition. The template test detects divergence. Existing mannequin free roam,
garbage-Molcar ownership and Taketsumi behavior remain in the normal pack.

`ikarun.js` and `narita_glasses.js` are saved as runtime source, without adding
unconditional imports to the public `main.js`. Private/static overlay assembly
registers those imports when its complete assets are present. It must merge
registry additions into the current managed pack, preserve its existing DB
catalog, and pin/revalidate the assembled candidate through the existing managed
release workflow. Never replace a complete managed pack with this partial tree.

## Private generation

Use a new output directory in private storage, outside a public checkout. The
companion converter reads original NBT and verified material tiles. It refuses
an existing output and any output inside the public Minecraft pack directory.
It creates a partial `behavior_packs/` + `resource_packs/` tree and
`integration.json` with explicit import/language additions. It does not update
manifests, shared registries, the public pack, or a server.

```text
python scripts/build_structure_companions.py --source <private-structures> --vanilla-cache <private-tiles> --output <new-private-overlay> --report <private-proof.json>
python scripts/check_structure_companions.py --source <private-structures> --vanilla-cache <private-tiles> --output <private-overlay> --report <private-check.json>
python scripts/build_narita_glasses.py --output <new-private-glasses-overlay>
```

The Narita generator creates its own geometry and image pixels procedurally;
no supplied mesh or image is an input. This integration saves the generator and
tests, not its generated images. Its output includes registry additions and the
explicit runtime import in `integration.json`.

## Offline verification

```text
python -B scripts/test_structure_companions_overlay.py
node scripts/check_structure_companions.mjs
python -B scripts/check_narita_glasses.py
node scripts/check_narita_glasses.mjs
```

The first test uses a two-block synthetic NBT and synthetic material tile,
verifies conversion against that input, and checks original/public-file
preservation and overwrite refusal. Runtime fixtures cover exact owner
identity, bounded healing, home return, form contention, and night-vision lease
preservation. These checks do not claim PC/mobile rendering or live BDS results.

Production snapshots, native probes and proof-repair tools are operational
evidence and were not added as public data. No bot/admin replacement was needed:
the current main managed-cosmetics and Control API implementation stays canonical.
