# Molcar / Mokuro runtime integration (sections 2-5)

## Integration status

- Parent confirmed integration complete on 2026-09-24: the main entry point
  imports `./molcar_boost.js`, the cosmetics compiler retains movement maximum
  0.65, and CI coverage has been registered. These are no longer pending actions.
- Existing `molcar_combat.js`, `garbage_molcar.js` and `mokuro.js` imports remain;
  boost must be registered only once, in the import_structures pack.
- The three ordinary generated Molcar definitions retain
  `"minecraft:movement": {"max": 0.65, "value": 0.4}` in regenerated packs.
  The runtime owner changed BP outputs; parent handled compiler integration.
- Runtime work did not change manifests, main entry points, catalogs,
  localization, sound definitions, source models, textures, geometry or original
  animations. Parent owns shared integration, pack versioning and deployment.
- Implementation and isolated BDS verification are handed off. Parent owns
  commit/publication; this handoff does not claim production deployment.

## Behavior

- Garbage: native base speed 0.34, owner follow multiplier 1.25, horizontal
  item pursuit cap 0.38. Both combat copies reject only garbage ram victims;
  health, physical collisions, inventory, disposal, leash and follow remain.
- Ordinary three types: mounted carrot use or direct interaction with the
  player's current mount cancels native item use, defers mutations, rechecks
  the slot/mount, consumes exactly one carrot (including Creative), sets native
  movement to 0.65 and plays the existing `ichiyon:molcar.pui` sound. Reuse resets
  a single 100-tick timer. Other types/items and unmounted interactions pass through.
- Expiry, dismount, death, logout and dimension changes reset to 0.4. Recovery
  on entity load and a loaded-entity scan clear stale attributes after reload.
  Inventory write failure does not extend an existing timer; failed resets retry.
- Head glide uses amplifier-zero slow falling for 8 ticks, refreshed every
  6 ticks. Horizontal target is 0.32-0.50, interpolated by 0.2 with a shortest-path
  12-degree/tick turn cap; gain 0.08, deadband 0.02 and vector correction cap 0.04.
  Every glide impulse has Y=0; the existing head jump remains 1.15.
- Glide termination removes only a matching short owned effect. External effects
  are retained. On script reload any orphaned glide effect expires within 8 ticks.
- Head/back pose offsets and rotations are unchanged. Horizontal velocity predicts
  0.75 tick ahead, capped to a vector length of 0.25. Actual displacement rejects
  stale velocity when stopped or reversing; no previous velocity is accumulated.

## Verification

Run from the repository root:

```powershell
python scripts/check_minecraft_garbage_molcar.py
python scripts/check_minecraft_molcar_boost.py
python scripts/check_minecraft_mokuro.py
git diff --check
```

The Node entry points can also run independently. Garbage tests execute both
actual combat sources with mocked world events; boost tests exercise native
attribute range checks, deferred events, independent cars, rollback and recovery;
Mokuro tests cover movement bounds, effects, prediction and existing lifecycle.
The Mokuro Python checks verify the preserved source/export/animation contract.

Offline results: garbage 16 checks plus pack JSON parsing; boost 18 checks;
Mokuro 36 runtime checks plus 6 Python preservation/entity checks. JS syntax,
focused Python compilation and whitespace checks passed.

## Real BDS verification

Final isolated run `home-batch-runtime-20260924-a-r5` completed on 2026-09-24
at 11:22:57 UTC (20:22:57 JST): **80 PASS / 0 FAIL**. The server was BDS
1.26.51.1 with `@minecraft/server` 2.11.0-beta and GameTest 1.0.0-beta.
The test used real spawned entities, native inventories and simulated players,
not mocked engine APIs. Impulse instrumentation recorded and forwarded every
call to the real BDS player.

- All three Molcars accepted native boost, consumed one carrot, reset their
  timer to 100 ticks, and returned to base speed on expiry and dismount.
  A newly created runtime scan repaired a stale boost attribute and marker.
- Actual `itemUse` events fired at hunger 20. Two uses consumed two carrots;
  holding the second use for 30 ticks did not consume additional carrots.
- Native steering speed increased from 0.8810024261 to 1.4316291809 blocks/tick,
  a measured ratio of 1.62500027, while retaining native rider control.
- Garbage collected a drop six blocks away and discarded it in normal mode;
  owner mode retained a named stack of three items. Owner following, far-owner
  pickup suspension and follow-OFF disposal passed.
- Real moving ram left garbage HP at 30 through 15 qualifying proximity samples;
  the vanilla Mob control lost HP from 10 to 0 under the unchanged ram code.
- Head jump applied Y=1.15 and reached 13.151 blocks above its initial position,
  transitioned through roll to glide, and received amplifier-zero slow falling
  in all 52 sampled glide ticks. Refresh gaps were exactly 6 ticks.
- All 53 forwarded glide impulses had Y=0; maximum horizontal correction was
  0.0256. Detach cleared the owned effect and restored the same bird.
- Both head/back modes recorded 46 moving and 14 stationary samples. Maximum
  prediction offset was 0.161894 blocks, positional prediction error below
  2.4e-7 blocks, and stationary offset effectively zero. Back carry retained the
  ordinary 1.252-block jump without roll or glide.

The first real run exposed float32 rounding: a configured maximum of 0.65 is
returned as 0.6499999761581421. The runtime now tolerates the representational
difference and clamps writes to the native maximum; base-speed recovery scans
also tolerate float32 rounding. A focused regression reproduces this issue.
Only the owned boost core and its focused test changed to fix this runtime bug.

Evidence on the home PC, outside the repository and production packs:

```text
C:/Users/syoub/.codex/tmp/home-batch-runtime-20260924-a/r5.log
C:/Users/syoub/.codex/tmp/home-batch-runtime-20260924-a/release.json
C:/Users/syoub/.codex/tmp/home-batch-runtime-20260924-a/smoke.js
C:/Users/syoub/.codex/tmp/home-batch-runtime-20260924-a/setup.py
```

The final log contains no Script runtime exception. The isolated fixture's
NetherNet transport advisory remains; network/transport settings were not changed.
Client rendering and perceived smoothness still require visual gameplay review.
GameTest did not emit a direct mounted entity-interaction event, so that optional
input route is covered offline, not claimed as a real-event result. Cold scan
recovery was exercised against real attributes; a saved-world restart during an
active boost was not part of this smoke run.

## Container release

`mokuro-smoke-20260923` was stopped and explicitly released at
2026-09-24 11:23:08 UTC (20:23:08 JST): `Running=false`, `ExitCode=0`.
Recorded isolation was `network=none`, no published ports, and the sole bind mount
`/home/ubuntu/mokuro-smoke-20260923/data` to `/data`. Remote test writes were limited
to that data directory and uniquely named `/tmp/home-batch*` files. Production
data and the live BDS container were not accessed or changed.

Parent confirmed handoff to Hubble, with Epicurus next. The runtime owner has
released exclusive ownership and will make no further container operations.
The test entry at release was `scripts/home_batch_runtime_smoke.js`; the next
owner controls replacing the test entry for their smoke suite. No smoke pack was
installed into production.

## Official API references checked

- [Movement component schema](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/entityreference/examples/entitycomponents/minecraftcomponent_movement?view=minecraft-bedrock-stable): base value and maximum are distinct.
- [EntityAttributeComponent](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/entityattributecomponent?view=minecraft-bedrock-stable): `effectiveMax`, `setCurrentValue`, write restrictions.
- [ItemUseBeforeEvent](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/itemusebeforeevent?view=minecraft-bedrock-stable): cancel before deferred inventory/movement writes.
- [EntityRidingComponent](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/entityridingcomponent?view=minecraft-bedrock-stable): `entityRidingOn`.
- [Container](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/container?view=minecraft-bedrock-stable): `getItem` and `setItem`.
- [Entity](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/entity?view=minecraft-bedrock-stable): effects, dynamic properties and impulse methods.
- [Effect](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/effect?view=minecraft-bedrock-stable): amplifier and duration inspection.

## Changed paths

```text
minecraft/behavior_packs/ichiyon_avatar_bp/entities/garbage_molcar.json
minecraft/behavior_packs/ichiyon_avatar_bp/entities/molcar.behavior.json
minecraft/behavior_packs/ichiyon_avatar_bp/entities/molcar2.behavior.json
minecraft/behavior_packs/ichiyon_avatar_bp/entities/molcar3.behavior.json
minecraft/behavior_packs/ichiyon_avatar_bp/scripts/molcar_combat.js
minecraft/behavior_packs/import_structures/scripts/garbage_molcar_core.js
minecraft/behavior_packs/import_structures/scripts/mokuro_core.js
minecraft/behavior_packs/import_structures/scripts/molcar_combat.js
minecraft/behavior_packs/import_structures/scripts/molcar_boost.js
minecraft/behavior_packs/import_structures/scripts/molcar_boost_core.js
scripts/check_minecraft_garbage_molcar.mjs
scripts/check_minecraft_mokuro.mjs
scripts/check_minecraft_molcar_boost.mjs
scripts/check_minecraft_molcar_boost.py
docs/minecraft-molcar-mokuro-runtime-20260924.md
```
