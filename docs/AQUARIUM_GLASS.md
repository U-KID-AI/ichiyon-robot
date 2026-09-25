# Aquarium glass

Adds 17 independent full blocks: clear, white, light gray, gray, black, red,
orange, yellow, lime, green, light blue, cyan, blue, purple, magenta, pink, brown.
Identifiers are `ichiyon:aquarium_glass_<color>`. Construction inventory contains
one collapsible Aquarium Glass group in that order, with Japanese/English labels.
Vanilla glass textures and existing resource packs are not overwritten.

## Original visual design

The textures are original, deterministic solid RGBA samples, not extracted from
Ultra Modern or another commercial pack. They follow the requested aesthetic of
an almost clear center and restrained perimeter. The center is alpha 8/255 for
clear and 24/255 for colors. Each face has four separate, pale edge strips,
1/64 block wide at alpha 104/255; there are no diagonal scratches or center bars.
A 0.002 model-unit separation prevents coplanar texture overlap.

Native culling removes faces against the same glass block, and removes an edge
strip against the same glass in its lateral direction. Lateral opaque framing
does not remove that rim. A face neighbor can also cull its four strips. This is
neighbor-aware geometry, not a texture replacement, a world scan, or a tick
script. Differently colored blocks deliberately keep their color boundary.
No new experimental toggle is needed. Format 1.26.0 is below production BDS
1.26.51.1. Client rendering, transparency sorting through multiple layers, and
perceived edge thickness still require an actual client check.

References:
- [Official culling conditions and geometry parts](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/blockcullingreference/examples/blockcullingrules/block_culling?view=minecraft-bedrock-stable)
- [Official geometry/culling component](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/blockreference/examples/blockcomponents/minecraftblock_geometry?view=minecraft-bedrock-stable)
- [Official connection rule](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/blockreference/examples/blockcomponents/minecraftblock_connection_rule?view=minecraft-bedrock-stable)

The blocks retain full-cube collision/selection, zero light damping, glass sound,
0.3-second mining, 1.5 explosion resistance, and a self-drop with Silk Touch only.
Vanilla fences/panes may connect to them. No special vanilla beacon/tinted-glass
behavior is promised. No custom panes are added: the target building contains
none, and panes in the neighboring building must remain untouched.

## Build and pack ownership

`python scripts/build_aquarium_glass.py` writes only owned glass assets plus its
group in the existing behavior-pack item catalog. `--check` is read-only.
`python scripts/check_aquarium_glass.py` covers deterministic generation, native
culling topology, alpha, collision/loot, inventory order, compiler preservation,
and the exact bounded replacement/verification contract.

`ichiyon_aquarium_glass_rp` is a direct managed resource pack, initial version
1.0.0, header UUID `fe55c87e-d7a2-4e3c-88fb-43009f65df75`, module UUID
`a3d30f1b-8445-4e46-a670-e53433ec8d7a`. The existing avatar BP owns the blocks,
loot and creative group. Managed archive limits remain unchanged. The normal
exact-merge-SHA release builds with production DB-managed assets and applies via
Control API; Git packs are never copied into production by SSH.

## Building survey and bounded migration

The requested screenshots were not present in the received attachment inventory.
The two supplied observation positions (105,65,266) and (25,65,206) bracket the
large smooth-quartz/pale-pink building at X=30..92, Z=212..260. A consistent
`save hold` / `save query` / length-truncated copy / `save resume` snapshot was
read offline. Material/coordinate projections identify its outer shell and
water-filled aquarium interiors. The neighboring tall building at X<=18 is
separate and contains the panes; it is excluded.

Inside X=30..92, Y=64..114, Z=212..260, the survey found exactly:
- 1,558 `minecraft:glass` -> `ichiyon:aquarium_glass_clear`;
- 317 `minecraft:light_blue_stained_glass` -> corresponding light-blue glass;
- zero panes or other stained-glass colors.

The actual glass coordinate bounds are X=33..90, Y=66..92, Z=212..260.
`minecraft/world_migrations/aquarium_glass_20260925.json` records 502 contiguous
X runs covering only those 1,875 original blocks. It is an operator plan, not an
automatically executed behavior-pack function. `scripts/aquarium_glass_migration.py`
validates the plan and emits vanilla `execute in overworld run fill ... replace`
commands, always guarded by the exact old identifier. `--reverse` emits guarded
restoration commands limited to the same cells and new identifiers.

Before executing: complete managed deployment, obtain a fresh consistent backup,
and confirm every planned original coordinate/type/state is unchanged. Execute
only the listed runs in loaded chunks, then take another consistent snapshot.
Compare all surveyed block IDs/states: exactly the 1,875 allowlisted cells may
change. Include neighboring buildings in the comparison. Stop on partial/missing
counts; keep the backup and reverse plan. Never restore a whole world merely to
undo glass, and never write LevelDB directly. Remove only the temporary inspection
ticking area after verification. Preserve all pre-existing ticking areas.

Headless BDS tests prove registration, placement and item identity, not the
appearance of the resource pack in the player's renderer. Final human checks:
clear window center, perimeter-only rims on same-color panes of blocks, natural
color tint, and a contiguous creative inventory group.

## Native validation, 2026-09-25

An isolated BDS 1.26.51.1 container (`network=none`, no published ports, only a
disposable copied-data mount) ran the exact 502 guarded fill commands on a copy
of production. Public Script API counts matched the offline survey. All 1,875
glass cells changed correctly; the other 155,562 cells in the building's 157,437
cell bounding volume, including water, remained unchanged after a settling wait.
All 17 new block IDs resolved, placed and produced the matching ItemStack.
The fixture used a simulated player only in this disconnected test container;
no GameTest module or simulated-player code is added to production. The container
was stopped after the test. Native inspection yielded across ticks to respect
the watchdog. No glass block/content validation error occurred; the isolated
server's missing profiler config and existing NetherNet startup warnings are
unrelated to the block definitions. Automated rendering is not claimed.
