# Akki screen and the exact existing map wall

Base: fetched `origin/main` `3c13bb8d705d0cc4829fdc8e07375c200cf3e0b2`,
including merged PR #92. Worktree/branch are dedicated to this request.

## A. Map refresh: failed acceptance, safe fallback retained

The target is only `WALL_DISPLAYS.map` in `minecraft:overworld`: leftX=15,
width=3, height=3, bottom search 90..98, planes 243/244/245, facing=2. No other
map wall or nearby substitute button is selected.

Read-only production inspection on 2026-09-25, BDS **1.26.51.1**, confirmed:

- Existing runtime: `bottom=95 wallPlaneZ=244 framePlaneZ=243`, **8 frames**.
- Frame columns: 15,14,13; rows: 95,96,97; existing upper-right omission retained.
- Exact control: **(16,95,243)**, a vanilla pale oak button.
- Deterministic prospective single-map candidate: **(15,95,243)**. It was **not
  removed, replaced, cloned, handed to a player, or otherwise modified**.

The public API preflight reviewed the version-matched
`@minecraft/server` **2.11.0-beta.1.26.51-stable** and
`@minecraft/server-gametest` **1.0.0-beta.1.26.51-stable** declarations.
`spawnSimulatedPlayer(DimensionLocation, name, gameMode)` exists at module level:
it is **not restricted to a Test context**. Production `help gametest` succeeds,
and the installed bridge already uses beta modules. No new experiment was
enabled. These facts do not constitute an end-to-end map update proof.

Reviewed routes: vanilla clone/structure of a frame, then moving its copied map
through a simulated player's normal held-map update path. The public Block API's
`getItemStack(withData)` creates a *block prototype item*; it does not document a
framed filled-map identity, coverage-center or terrain-pixel reader. The reviewed
public APIs expose no existing-filled-map recalculation operation or map-pixel
comparison. Therefore neither preservation of the copied map identity nor terrain
propagation back to the original display was established. There is no real-client
observation of that propagation in this run.

**The clone/held-map functional PoC was not executed and its success is not
claimed.** This is failed acceptance / an unproven route, not proof that all
vanilla cloning routes are impossible or that SimulatedPlayer is unavailable.
Without the required one-map proof, no eight-map refresh, guard or helper lifecycle
was introduced. `MAP_REFRESH_LIMITATION`, exact-target detection and honest rescan
notification remain. There is no exploratory runtime or GameTest dependency in
the commit. No map IDs were read from private NBT/LevelDB.

Original maps/frames/blocks: untouched. No temporary frame or helper was created.
One named ticking area was temporarily used to read the requested locations via
vanilla `testforblock`; it was removed, restoring the previous **4/10** area count.
No private world data, experiments, other ticking areas or world blocks were edited.

References (review version-matched declarations rather than assuming preview docs
describe the production binary):

- [GameTest module / top-level simulated player](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server-gametest/minecraft-server-gametest?view=minecraft-bedrock-experimental)
- [GameTest experiment requirement](https://learn.microsoft.com/en-us/minecraft/creator/documents/gametestbuildyourfirstgametest?view=minecraft-bedrock-stable)
- [Block public API](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/block?view=minecraft-bedrock-experimental)

## B. Akki video

Source: `C:\Users\syoub\Desktop\神社\mp4\悪鬼.mp4` (never copied into Git).
SHA-256: `bab5cb8f68e0ed19de85efa8379804cd23a69648baf451b2929d26277d106835`.
60,102,747 bytes; 3840x2160; nominal 30 fps, average 12390000/411769
(30.089686 fps); video 13.725633 seconds, container 13.738646 seconds.
Input audio: AAC, stereo, 48,000 Hz.

The pre-generation conservative RP estimate was 3,515,006 bytes. Production
baseline archive was 121,458,824 bytes, expanded 132,629,521 bytes / 1,006 files.
No limit or quality reduction was needed. Limits remain 201,326,592 archive bytes,
536,870,912 expanded bytes, 8,388,608 bytes/file and 16,384 files.

Output: **20 fps, 64x36, 275 frames, 13.75-second loop, 3 RGB atlases**.
Each atlas is 660x380 with one-pixel padding and edge extrusion. PNG atlas total:
1,193,395 bytes. Audio: 116,387 bytes, mono 44,100 Hz Vorbis quality 3, unstreamed
like small. Positional source is the screen center, min distance 1 / max 12.
Only listeners in the room's front rectangle receive it; rear/wrong-dimension/
out-of-range listeners are excluded. Audio failures are isolated with a 100-tick
per-recipient retry interval; frames/helpers/other displays keep running.

Dedicated RP: `ichiyon_video_akki_rp`, initial version **1.0.0**.
Header UUID: `c2de9f3f-7956-4c7a-b6a1-63b264a9a059`.
Module UUID: `4e49c6a4-2f67-4e31-9c5c-5946ec3ec437`.
Its manifest is maintained independently of the builder; rebuilding never
generates another UUID. Size/archive accounting is in
`minecraft/video_screen_akki/validation_report.json` (production estimate is
explicitly distinguished from the exact local builtin archive).

### Measured installation

Read-only vanilla block queries found **white concrete**, X=-26..-14,
Y=102..105, Z=232, exactly 13x4. The entire front plane Z=233 is air at those
52 cells; border cells do not continue the white material. The existing Z-plane,
south-facing convention is correct, so no cardinal-orientation rewrite is needed.

Runtime searches only left X=-27..-25, bottom Y=101..103, Z=231/232/233, with
`minecraft:white_concrete` as the sole material. It rejects larger/partial/
occluded/ambiguous/unloaded candidates. Detected helper origin is
(-19.5,102,233.02), yaw=0; sound center (-19.5,104,233.02). Black plane: 13x4.
Video plane: (64*16/9)/16 by 4 blocks, centered, no stretching to 13:4.

Opposite button: **(-20,104,237)**, polished blackstone button,
`facing_direction=2` (confirmed by six explicit state queries), solid coal-block
backing **(-20,104,238)**. Runtime requires a unique vanilla *wall* button within
radius 2 of the measured position, with the support block corresponding to its
facing. The existing big screen's *floor* mode remains unchanged. No wall/button
block is written or replaced.

### Managed deployment and verification boundaries

Akki is a direct RP in the partition/archive list. Existing Control API validation
already supports named `ichiyon_*_rp` packs and derives world references from
`cosmetics-build.json`; no control-plane update or raw-copy path is needed.
Only the two behavior packs' source versions advance for the new helper/runtime;
existing RP manifests, small/big bytes and UUIDs remain unchanged. Content-hash
apply preserves unchanged installed pack versions and production DB assets.

Focused checks cover all frame edges/final-frame inclusion, full original rebuild,
silent/audio builder fixtures, old small/big definition identity, geometry,
stable UUIDs, archive limits/membership, apply world references, exact wall/button
detection, three independent displays, audio failures and exact map fallback.
CI includes the new asset test and runs the existing Minecraft/DB suites.

Production success requires the normal exact-merge-SHA managed release, terminal
`succeeded`, catalog/active digest equality, installed Akki pack, healthy running
BDS and Bedrock response. Headless tests/logs do not prove visual rendering or
perceived A/V sync; those require the requested human client checks.
