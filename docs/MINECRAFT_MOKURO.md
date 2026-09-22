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

No new animation is required. Idle is the original resting pose with standard
random-look AI yaw; walk and glide select the existing walk/open-wings clips.
Flap and roll are retained and referenced but not automatically played.

## Gameplay

- Summon: `/summon ichiyon:mokuro`. The generated creative spawn egg is
  `ichiyon:mokuro_spawn_egg` and is named モクローのスポーンエッグ.
- Empty-hand interaction opens head / back / cancel. Release a leash first.
- To put down: on the ground, empty hand, sneak + jump; choose 降ろす.
  The owner can also interact directly with the carried Mob to open that menu.
- Head glide: while falling, press jump again. Sneak cancels glide; landing ends
  it. Back carrying never grants glide.
- Wearing an Elytra, actual Elytra gliding, Creative flying, water, climbing,
  sleeping, and riding another entity disable the approximation.

This is not native Elytra flight. It uses bounded impulses with horizontal
inertia, view-directed steering and a downward target speed (no powered ascent).
Fall damage cancellation applies only to the owner's active head glide and its
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

The deployment target uses BDS 1.26.51.1 and the already-installed Script module
`@minecraft/server` 2.11.0-beta, with `@minecraft/server-ui` 2.0.0. Dependencies
are unchanged.
