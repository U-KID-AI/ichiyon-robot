# Death Prairie Dog (Section 12)

## Source Preservation

Original: `C:/Users/syoub/Desktop/神社/mp4/deathprairie.bbmodel`.
Byte-identical retained copy: `minecraft/source_assets/death_prairie_dog/deathprairie.bbmodel`.
SHA-256: `03a67110f3dae8c1ba2b86f6325438b32f49f613018471358032bfe279c3d4ad`.
The scoped `.gitattributes` disables source newline conversion. The exporter
checks this hash before generating anything and never writes either source.

`scripts/export_death_prairie_dog.py` independently applies the existing Mokuro
exporter's Blockbench-to-Bedrock coordinate, per-face UV and animation conversion.
The Mokuro exporter and source are untouched. All 52 cubes, 8 bones, parent
relationships, pivots, cube rotations, box UVs and per-face UVs are retained.
Geometry texture dimensions remain 64 x 64. Bounds affect culling only.

Green identification is pixel-based, not a hardcoded texture slot or ID:
992 of 1,034 opaque pixels are RGB (88, 217, 14), uniquely dominant green among
the four source textures. The selected source has ID `3`, array index `1`,
UUID `b6f63003-076c-39f0-aebb-f621d82f6216`.
The extracted PNG SHA-256 is
`2e93d3a2968a9897261b4e5b7c0a36fb07028539f2754b0a8798a1d210ad3315`.
Only this exact embedded PNG is exported/referenced by the game. The other
three textures remain solely in the immutable development source.

| Original clip | Exported suffix | Length | Loop |
| --- | --- | --- | --- |
| animation.model.new | transition | 0.5 s | hold_on_last_frame |
| prairie4walk | prairie4walk | 1 s | true |
| prairie2walk | prairie2walk | 1 s | true |

All 27 original position/rotation keyframes, times, values and global rotations
are preserved after coordinate conversion. No resampling, optimization, retiming,
epsilon or extra keyframes. One empty Blockbench effect animator has no runtime
content; nonempty unsupported effect tracks fail export rather than disappear.
Animation identifiers alone are namespaced to avoid global collisions.

## Behavior

- Entity: `ichiyon:death_prairie_dog`; generated egg:
  `ichiyon:death_prairie_dog_spawn_egg`. HP20, physical collision, persistent,
  nameable, no monster family and no natural-spawn rule.
- Outside inclusive world time 13000..23000: ordinary random stroll at native
  movement 0.22, no eligible target, `prairie2walk` while moving.
- At night: Script selects the nearest awake player within 32 blocks, same
  dimension, including Creative, Survival and Adventure (not Spectator).
  Starting pursuit holds movement for
  10 ticks and plays the original 0.5-second transition once. Chase then uses
  native `follow_mob` at multiplier 2.5 (nominal 0.55) and `prairie4walk`.
  Both the goal's search radius and the entity's `minecraft:follow_range`
  (`value` and `max`) are explicitly 32. Search radius alone did not enable
  native pursuit of a Creative player about 22 blocks away in BDS 1.26.51.1.
- There is no attack selector, attack attribute, melee/ranged goal, retaliation,
  damage call, projectile, explosion or monster runtime inheritance. Explicit
  `follow_mob` filters match only the selected player's private tags. Nonempty
  filters permit players, avoiding the default follow goal's player exclusion
  and avoiding attackable-target eligibility entirely. Stop distance: 1.5 blocks.
- Follow-goal removal/readdition on target change forces path reacquisition
  without replaying the client transition. No teleport, impulse, command,
  owner manipulation, invisible helper or replacement entity.
- Native `is_sleeping` filters continuously reject sleeping targets. Script
  polls real `Player.isSleeping` every tick. A sleeping previous target or any
  sleeper within 8 blocks removes follow, clears the Script target and enters
  release. There is no attack target to clear or read; removing the goal releases
  its internal follow candidate.
- Priority-1 `avoid_mob_type` uses native walking navigation to flee sleepers,
  ahead of priority-3 following. New pursuit waits at least 20 ticks and until
  all sleepers are beyond 8 blocks; the native flee goal may continue to 10.
  Its outer `max_dist` is explicitly 8 as well as the entity-type entry's
  `max_dist`. Leaving the outer search at its documented default of 3 prevented
  escape from a sleeping player about 4 blocks away in the real smoke.
  If an obstacle prevents escape there is no timeout that resumes pursuit next
  to a sleeper. Other awake players become eligible after clearance. A target
  that sleeps farther than 8 blocks is already clear, but still gets a release
  interval. Waking removes sleep avoidance.
- Dawn immediately restores normal wander. Native sleeper avoidance remains
  available even during daytime, but never enables daytime pursuit.

`scripts/build_death_prairie_dog_behavior.py` reproducibly generates the BP
definition. Each online player receives a unique transient slot (1..65535),
encoded in 16 `ichiyon:prairie_bit_N` tags plus `ichiyon:prairie_candidate`.
Each mob's 16 server-only bool properties encode its selected player's slot.
The native follow filter compares each bit using documented `bool_property`
and `has_tag` filters. This keeps one follow goal, supports many independently
targeting mobs, and avoids per-player duplicate component groups. Property
changes only occur when the chosen player changes. No player model is modified.
Stale private tags are rebuilt on first observation after restart/reconnect;
unrelated tags are preserved, offline slots are recycled. Reserved tags can
persist while offline but are cleared before that player becomes eligible again.

## Recovery And Limits

Spawn and entity-load recovery are deferred to a writable tick. A typed scan of
the three vanilla dimensions every 100 ticks discovers preexisting entities.
Normal scans do not restart already-tracked AI. Unload/death drops transient
tracking. Script/BDS restart resets saved groups to wander, then reevaluates
current time and players; identity, location, HP and source data never change.
Logout/death/dimension/range changes remove an unavailable player from eligibility.
One invalid mob does not abort updates for the others. Failed recovery is retried
by later scans; per-mob warnings are throttled.

Limitations are explicit:

- Creative is included in both selection and the native follow filters.
  Real BDS movement, including obstacle navigation and nearest-player changes,
  was verified with GameTest SimulatedPlayers; mocks alone are not proof.
  Spectator is intentionally excluded because
  it is non-interacting and invisible to mobs. Search is 32 blocks, not global.
- No `Entity.target` access remains, avoiding its pre-release read-only API.
  Existing `@minecraft/server` 2.11.0-beta dependency is unchanged; the code uses
  documented tags, entity properties, lifecycle APIs and inherited `isSleeping`.
- Slot encoding supports 65,535 simultaneously observed players; overflow fails
  explicitly. The match bits and candidate tag namespace are reserved to this
  mob. Other code must not mutate these tags/properties while it is running.
- Native navigation can fail on inaccessible terrain. There is intentionally no
  teleport fallback; this is harmless pursuit, not guaranteed arrival.
- A newly tracking client may play its local transition once on joining an
  already active chase. The original clip remains hold, not a looping animation.
- Local contract/mocked lifecycle tests do not execute Bedrock's native goals or
  render the client model. The isolated BDS acceptance below verifies native
  movement and sleep behavior. Client model rendering, animation appearance,
  localized inventory labels and spawn-egg artwork still need a client check.
  Real BDS unload/restart recovery was not exercised by that lifecycle smoke;
  it is covered by local lifecycle contracts only. No production operation was
  performed in this scoped task.

## Parent Integration

Machine-readable exact fragments: `docs/death_prairie_dog_integration.json`.
The parent has already integrated the main import and ja/en localization below.
Do not apply them again. The JSON is retained because tests depend on it.
The shared files below were deliberately not edited by this task.

Already integrated alongside other imports in
`minecraft/behavior_packs/import_structures/scripts/main.js`:

```javascript
import "./death_prairie_dog.js";
```

Already integrated in `minecraft/resource_packs/ichiyon_avatar_rp/texts/ja_JP.lang`:

```text
entity.ichiyon:death_prairie_dog.name=デスプレーリードッグ
item.spawn_egg.entity.ichiyon:death_prairie_dog.name=デスプレーリードッグのスポーンエッグ
```

Already integrated in `minecraft/resource_packs/ichiyon_avatar_rp/texts/en_US.lang`:

```text
entity.ichiyon:death_prairie_dog.name=Death Prairie Dog
item.spawn_egg.entity.ichiyon:death_prairie_dog.name=Death Prairie Dog Spawn Egg
```

No custom item, shared sound, atlas, cosmetics catalog or pack-compiler entry is
needed: `is_spawnable` plus client `spawn_egg` generates the egg. Existing
`pack_zip` includes all files under the three pack roots and preserves these
new files. The source model, exporter and docs are outside those pack roots.
The compiler preserves these localization lines while rebuilding cosmetic lines.
Parent should coordinate its normal BP/RP/bridge version increments and versioned
RP dependency in the batch; no fixed patch version or UUID is prescribed here.
Do not run a generator against shared source packs solely to register this mob.

Add these checks to the parent verification/CI batch:

```text
python scripts/export_death_prairie_dog.py --check
python scripts/build_death_prairie_dog_behavior.py --check
python scripts/check_minecraft_death_prairie_dog.py
node --check minecraft/behavior_packs/import_structures/scripts/death_prairie_dog.js
node --check minecraft/behavior_packs/import_structures/scripts/death_prairie_dog_core.js
```

The Python suite runs 6 source/asset/integration tests including the Node suite
of 26 lifecycle, native-definition and controller contracts. It verifies every
cube and keyframe, green selection independent of IDs/order, byte-exact PNG,
night boundaries, sleep release and blocked escape, nearest replacement,
no attack/movement injection, recovery, and controller reachability.

Remaining client/recovery acceptance: verify all three original clips and the
green-only model, localized spawn egg, and unload/reload plus restart recovery.
The headless smoke verifies movement, not rendered animation playback. This
document does not authorize deployment.

## Official API References

Checked 2026-09-24; all are Microsoft Learn Bedrock creator references.

- [Follow mob: explicit filters permit players, speed and stopping distance](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/entityreference/examples/entitygoals/minecraftbehavior_follow_mob?view=minecraft-bedrock-stable)
- [Follow range: native pursuit range, distinct from goal search radius](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/entityreference/examples/entitycomponents/minecraftcomponent_follow_range?view=minecraft-bedrock-stable)
- [Player tag filter](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/entityreference/examples/filters/has_tag?view=minecraft-bedrock-stable)
- [Sleeping filter](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/entityreference/examples/filters/is_sleeping?view=minecraft-bedrock-stable)
- [Boolean-property filter](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/entityreference/examples/filters/bool_property?view=minecraft-bedrock-stable)
- [Avoid mob type](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/entityreference/examples/entitygoals/minecraftbehavior_avoid_mob_type?view=minecraft-bedrock-stable)
- [Walking navigation](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/entityreference/examples/entitycomponents/minecraftcomponent_navigation.walk?view=minecraft-bedrock-stable)
- [Entity: isSleeping, tags, properties, triggerEvent](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/entity?view=minecraft-bedrock-stable)
- [GameMode values](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/gamemode?view=minecraft-bedrock-stable)
- [Entity removal event and removedEntityId](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/entityremoveafterevent?view=minecraft-bedrock-stable)

## Verified Isolated Smoke

Local-only harness, outside the checkout:
`C:/Users/syoub/.codex/tmp/death-prairie-smoke-20260924/prairie_entry.js`.
It uses the real `@minecraft/server-gametest` SimulatedPlayer API, ordinary
`Dimension.spawnEntity`, actual bed interaction and the actual runtime. It
never assigns sleep/state or moves the mob by Script. Only the test players
are repositioned between scenarios. The adjacent `run_isolated.py` validates
the sole test mount, network `none`, empty port bindings and stopped state,
copies scoped assets, and always stops the isolated container in `finally`.

Parent-authorized exclusive run `r12` on BDS **1.26.51.1** passed **10 checks**:
egg construction, real daytime wander/HP20, Creative wall navigation,
nearest-player retarget, dawn release, Survival approach, contact without
damage, real sleep, native flee and another awake Creative target after release.
The runtime uses server 2.11.0-beta; the test-only GameTest module is 1.0.0-beta.

| r12 measurement | Start | End |
| --- | --- | --- |
| Creative behind three-high wall | 20.64 blocks | 1.98 blocks |
| Nearest-player change | 7.36 blocks | 1.59 blocks |
| Survival approach | 11.00 blocks | 0.79 blocks |
| Separation from actually sleeping player | 4.36 blocks | 8.24 blocks |
| Awake Creative after sleep clearance | 14.28 blocks | 2.55 blocks |

Survival HP stayed 20 after 100 contact ticks and throughout sleep. The original
player remained asleep during escape and subsequent pursuit. Movement samples
were bounded (largest approximately 3.26 blocks per five ticks); no teleport,
attack target or attack goal was used.

Verified causes and fixes, not filter/API-enumeration speculation:

1. `follow_mob.search_range: 32` alone did not make a target around 22 blocks
   reachable. r9 failed; r10 added only `minecraft:follow_range` value/max 32
   and passed wall chase, nearest-player change, dawn and no-damage contact.
2. The avoid goal's outer search was still at default 3 even though its
   entity-type entry allowed 8. r10 remained still about 4 blocks from the
   sleeping player. r12 added only outer `max_dist: 8` to the corrected source
   and passed escape/reacquisition. No visibility override or owner API was
   needed. r11's broader diagnostic overrides were not shipped.
3. Early registration-harness runs overlapped/replaced test terrain, displaced
   spawned entities and sometimes lost initial HP. They do not establish a
   Creative exclusion in `follow_mob`. Behavior goals also need not appear in
   Script component enumeration; `hasComponent` was never the movement oracle.

The final r12 used byte-exact checkout assets, with no diagnostic BP overrides.
BP SHA-256: `c94b8bfc5d22c43bfe156b099f146448ba7e741ae801b5367fbd67d28640d137`.
Evidence: adjacent `r12.log` and `r12-release.json`.
Container `mokuro-smoke-20260923` was stopped and explicitly released:
`Running=false`, `ExitCode=0`, finished `2026-09-24T11:58:53.377336565Z`.
The test-only manifest entry remains `scripts/prairie_entry.js`; the next owner
must replace it before starting. No further container access is authorized by
this document. The known server NetherNet transport warning is unrelated to
these headless tests; no Prairie content or Script error was logged in r12.

## Owned Files

- `minecraft/source_assets/death_prairie_dog/{.gitattributes,deathprairie.bbmodel}`
- `scripts/{export_death_prairie_dog.py,build_death_prairie_dog_behavior.py}`
- `minecraft/behavior_packs/ichiyon_avatar_bp/entities/death_prairie_dog.json`
- `minecraft/behavior_packs/import_structures/scripts/death_prairie_dog{,_core}.js`
- `minecraft/resource_packs/ichiyon_avatar_rp/entity/death_prairie_dog.entity.json`
- `minecraft/resource_packs/ichiyon_avatar_rp/models/entity/death_prairie_dog.geo.json`
- `minecraft/resource_packs/ichiyon_avatar_rp/animations/death_prairie_dog.animation.json`
- `minecraft/resource_packs/ichiyon_avatar_rp/animation_controllers/death_prairie_dog.controller.json`
- `minecraft/resource_packs/ichiyon_avatar_rp/textures/entity/death_prairie_dog.png`
- `scripts/check_minecraft_death_prairie_dog.{py,mjs}`
- `docs/{MINECRAFT_DEATH_PRAIRIE_DOG.md,death_prairie_dog_integration.json}`

Parent reports the main import and ja/en localization fragments are integrated.
This task did not edit those shared files, manifests, sounds, catalog, pack
compiler, or Mokuro exporter. No staging, commit, push, deployment or delegation.
