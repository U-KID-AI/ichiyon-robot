# Mokuro

## Source And Export

The friend's `minecraft/source_assets/mokuro/mocro.bbmodel` is immutable.
SHA-256: `eb0c2ab7afc70ad625bd87fd7ed63fe6a7d90c41e10b2e9635fba8703a0f2def`.
The scoped `.gitattributes` prevents line-ending conversion of this source.

`python scripts/export_mokuro.py --check` verifies the checked-in export.
Without `--check` it regenerates ONLY the three RP output files, never the source.
All 45 cubes and 7 bones, including the separate `regL` and `regR` roots, remain.
The PNG is decoded byte-for-byte from the embedded data URI, without re-encoding.
It remains 128x128; geometry UV units remain 64x64.

Blockbench and Bedrock use different X/rotation conventions. Export mirrors the
coordinate basis (cube X origin, pivot X, rotation X/Y, animation position X),
not the appearance. Box UV offsets and per-face UV rectangles are preserved in
the corresponding Bedrock representation. Global rotation becomes
`relative_to.rotation = entity`. No epsilon, optimization, re-timing, or extra
keyframes are inserted.

The four original animation names, lengths, and keyframes remain:

| Animation | Length | Loop |
| --- | --- | --- |
| mocro_walk | 0.5 s | true |
| mocro_flap | 0.5 s | true |
| mocro_open_wings | 0.5 s | hold_on_last_frame |
| mocro_roll | 0.25 s | true |

No new animation is required. Idle uses the original resting pose and random-look
AI yaw. After 4-8 seconds of continuous grounded idle, the controller plays the
original flap for 1 second (two untouched 0.5-second loops), then waits again.
Ground speed above 0.05 selects walk and interrupts flap. Carried/glide states
override both; head-jump ascent uses the unchanged roll clip, then glide uses
open-wings. No original animation data is edited.

## Gameplay

- Summon: `/summon ichiyon:mokuro`. The generated creative spawn egg is
  `ichiyon:mokuro_spawn_egg` and is named モクローのスポーンエッグ.
- Empty-hand interaction opens head / back / cancel. Release a leash first.
- To put down: on the ground, empty hand, sneak + jump; choose 降ろす.
  The owner can also interact directly with the carried Mob to open that menu.
- Head jump: jump from the ground for a strong upward assist and roll while
  rising; glide begins automatically at the apex. While already falling, jump
  also starts glide. Sneak cancels; landing ends it. Back carrying grants neither.
- Wearing an Elytra, actual Elytra gliding, Creative flying, water, climbing,
  sleeping, and riding another entity disable the approximation.

This is not native Elytra flight. It uses bounded impulses with horizontal
inertia, view-directed steering and a downward target speed. Ground jumps get
one upward impulse, never repeated midair. Fall protection covers head ascent,
active head glide and its
two-tick landing window. Other damage, including damage to Mokuro, is unchanged.
Detaching immediately revokes glide and fall protection.

## State And Recovery

Normal BP goals provide walking/navigation, random stroll, random look and
floating, with HP20, normal damage/death, leash support, and persistence. There
are no attack goals or natural spawn rules. Current format 1.26.30 uses
`pushable_by_entity` / `pushable_by_block`, not the removed `pushable` component.

The actual entity follows the player; no replacement, carrier, invisible Mob,
rideable, rider, health reset, scale or model transform is created.
The carried component group removes navigation and AI, disables gravity and
collision, and resists knockback. Its position/yaw is updated once per tick.
Back positioning reverses yaw 180 degrees; head positioning retains player yaw.

`mokuro:owner` and `mokuro:return` dynamic properties record ownership and a
last grounded recovery position. They are written before stopping AI. Runtime
maps identify active attachments. Logout/death, dimension change and long
teleports detach, restoring the mobile group first. Unloaded entities are
recovered on load. A Script/BDS restart intentionally detaches instead of
silently reattaching to an offline player. Health and entity identity persist.

Only attached entities run every tick. Every 100 ticks, a typed query of loaded
Mokuro entities in the three dimensions catches orphaned attachment state.
Spawn (deferred one tick), load and that existing scan also repair a normal
individual only if navigation or positive movement is missing. This reapplies
the detach/mobile event without moving it or resetting HP. Healthy navigation
is never restarted by a scan; tracked attachments are excluded. The mobile
group remains removable while carried. Random-stroll now explicitly uses
interval 40 (previously the default 120, a 1/interval selection chance) and
movement 0.22 with multiplier 1.0 (previously 0.16 with multiplier 0.8).
Recovery events that fail retain their marker for another attempt. Failure to
teleport to an unloaded/blocked recovery position leaves a normal, visible Mob
with physics at its current position rather than leaving it attached.

## Validation And Release

`python scripts/check_minecraft_mokuro.py` runs source, geometry/UV, exact
animation, BP/RP, localization, and executable mocked lifecycle checks.
`node --check` covers main.js, mokuro.js and mokuro_core.js. Existing cosmetics,
avatar, poster, leash, Pikachu and Taketumi checkers must be rerun.

An isolated BDS with the production version and no published ports should load
the BP/RP and script before live application. A separate smoke harness may
spawn/damage/attach/detach/kill a test Mob there. Do not include that harness,
its ticking area, or its world in the production package.

The 2026-09-23 isolated run on BDS 1.26.51.1 confirmed Script startup, generated
spawn egg resolution, HP20/leash/navigation, damage to HP18, carried state with
navigation absent and HP18 retained, detach restoring navigation/leash with
HP18 retained, random ground movement, and death after lethal damage. The test
instance needs Mojang's `server_library` and `server_ui_library` in addition to
vanilla packs; neither library is a new dependency of the production install.

The follow-up isolated BDS 1.26.51.1 run confirmed three freshly spawned
individuals wandering 39.19 / 59.44 / 62.81 cumulative horizontal blocks in
60 seconds, with both moving and idle samples. The same individual stopped
navigation while head/back carried, retained HP18 and the expected yaw, then
wandered 42.99 blocks in 45 seconds after detach. Missing normal AI was repaired
without resetting HP; lethal damage still removed the entity. The six Python
contracts and 24 executable lifecycle/controller checks pass. The controller
expression harness does not substitute for client-side animation rendering.

Confirmed defects were missing automatic flap transitions and missing recovery
for normal entities lacking mobile components. The old spawn event does work
in isolation; its failure on a particular live individual was not observed.
The low stroll selection chance and speed explain infrequent movement but
are not evidence of a universal spawn-event failure. NaritaBridge's existing
five static Taketumi checks still inspect main.js rather than the extracted
module; this change leaves both that checker and main.js untouched.

Live release MUST start with fresh live packs, preserve registered cosmetics,
overlay only Mokuro assets/scripts plus the main import/localization lines,
and use the existing cosmetics archive proof and Control API apply process.
Repository manifest patch numbers are not a license to downgrade live versions.
No world DB edits, migrations, external raw command interface or Discord command
are needed. Backups and operation IDs belong in the deployment report.

Client acceptance still requires checking appearance and all four original
clips, head/back fit, walk/idle, leash, normal damage/death, menu and detach
controls on keyboard/controller/touch, glide feel/landing, and reconnect/portal
recovery. Mock tests and a headless BDS cannot certify client rendering or feel.

## API References

- [Blockbench Bedrock exporters](https://github.com/JannisX11/blockbench/tree/master/js/formats/bedrock)
- [Entity teleport options](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/teleportoptions)
- [Player button events](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/playerbuttoninputafterevent)
- [Entity API](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/entity)
- [Pushable component format change](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/entityreference/examples/entitycomponents/minecraftcomponent_pushable)
- [Random stroll selection interval](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/entityreference/examples/entitygoals/minecraftbehavior_random_stroll)
- [Animation controllers](https://learn.microsoft.com/en-us/minecraft/creator/documents/animations/animationcontroller)

The deployment target uses BDS 1.26.51.1 and the already-installed Script module
`@minecraft/server` 2.11.0-beta, with `@minecraft/server-ui` 2.0.0. Dependencies
are unchanged.
