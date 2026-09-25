# Aquarium Glass rendering diagnostic

PR #95's registration path is valid: the 17 block IDs resolve, placement and
ItemStack identity work in isolated BDS 1.26.51.1, and the dedicated RP is
listed in the managed archive and `world_resource_packs.json`. That does not
prove client rendering, which is why this change is deliberately diagnostic.

## Evidence and likely cause

The committed geometry uses custom multi-bone geometry with many edge cubes whose
one axis is exactly zero (`[0, 0.25, 16]`, `[0.25, 0, 16]`, or `[0.25, 16, 0]`).
It also asks the client to resolve six face materials and four edge materials
through `minecraft:material_instances`, then applies a data-driven culling file
to those bones. Bedrock's culling documentation describes culling of a named
geometry part or cube face, but does not promise zero-volume cubes as renderable
surfaces. A zero-volume cube has no renderable area in the client geometry
pipeline; its neighboring culling rules cannot make that area visible.

The body faces are also nearly transparent (alpha 8/255 clear and 24/255 color),
so the failed/unsupported edge path leaves little or no visible coverage. This
is a strong combined cause, but it cannot be called the sole cause until the
client observes the isolated red variant. The material keys themselves match the
cube `material_instance` names, the terrain registry keys resolve to the same
texture stems, the `blend` method is the documented translucent method, and the
dedicated RP has its own UUID and active world reference. No BDS content log
reported a missing texture, missing geometry, unknown material, or culling/schema
error. These observations exclude registration, pack omission, and an obvious
registry typo; they do not exclude a client-side custom-geometry limitation.

Official references:

- [Block culling conditions and geometry parts](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/blockcullingreference/examples/blockcullingrules/block_culling?view=minecraft-bedrock-stable)
- [Geometry component and supported full-block identifier](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/blockreference/examples/blockcomponents/minecraftblock_geometry?view=minecraft-bedrock-stable)
- [Material instances and blend rendering](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/blockreference/examples/blockcomponents/minecraftblock_material_instances?view=minecraft-bedrock-experimental)

## Red-only diagnostic

Only `ichiyon:aquarium_glass_red` changes in this branch. It uses the Bedrock
`minecraft:geometry.full_block` identifier, no custom culling reference, one
wildcard `minecraft:material_instances` entry, and the existing documented
`render_method: blend`. Its body texture is red with alpha 235/255 and its edge
texture is alpha 255/255, although the full-block diagnostic intentionally uses
only the body texture. This removes zero-volume geometry, custom culling,
multi-material binding, and near-zero alpha from the test while preserving the
same block ID, terrain registry, dedicated RP and managed archive route.

The other 16 blocks retain their PR #95 definitions byte-for-byte apart from
the generated manifest version required by the red diagnostic release. The
red RP manifest advances from 1.0.0 to 1.0.1 with its UUID unchanged; the
behavior pack advances to 1.1.39. No vanilla texture is replaced.

If red is visible in the user's client, the failure is in the custom geometry /
zero-volume edge / culling / multi-material combination, with the diagnostic
isolating the minimum stable path. If red is also invisible, investigate client
pack activation and the client resource-pack/render log next; this branch does
not claim a rendering fix for all colors.

## Scope boundary

This branch does not execute `scripts/aquarium_glass_migration.py`, does not
modify the world or LevelDB, and does not replace any building glass. The
existing migration plan remains an unexecuted artifact. Production verification
checks only the managed pack, its digest, the running services and the installed
red diagnostic. Human validation must place one red block in an unrelated empty
area; do not test in the target building.
