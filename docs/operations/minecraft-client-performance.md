# Client performance diagnosis and temporary video profile

This release records public client capability information and pauses Big Video.
Vibrant Visuals/PBR declarations remain unchanged, keeping that variable constant
for the Big Video comparison. Display Script scheduling is also optimized.
The diagnostics do not measure FPS, actual RAM use, GPU time, packet loss or
historical TPS.

## Diagnostics

The production bridge currently requests `@minecraft/server 2.11.0-beta`.
The fields below were verified in the official npm type definitions for
`2.11.0-beta.1.26.51-stable`, matching the BDS 1.26.52.3 release family; no API
dependency upgrade is needed. See the official [Player](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/player),
[ClientSystemInfo](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/clientsysteminfo)
and [InputInfo](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/inputinfo) references.

Each initial spawn emits one line with the prefix
`[NaritaBridge:client_performance]` and a JSON payload. Example below is
synthetic, not an observation of a production player:

```text
[NaritaBridge:client_performance] {"schema":"ichiyon.client_performance.v1","timestamp":"2026-10-01T00:00:00.000Z","player_name":"TestMobile","platformType":"Mobile","memoryTier":0,"maxRenderDistance":12,"graphicsMode":"Deferred","lastInputModeUsed":"Touch","unavailable":{}}
```

- Timestamp is UTC ISO 8601; add nine hours to compare with JST reports.
- `memoryTier` is the API enum (0 SuperLow, 1 Low, 2 Mid, 3 High, 4 SuperHigh),
  **not measured RAM capacity or free RAM**. Preserve zero as a valid value.
- `maxRenderDistance` is the device maximum in chunks, not the selected setting.
- Graphics/input values are the API's observations at sampling time. A graphics
  mode change after join is visible on the next `同期診断 <player>` request.
- A missing/throwing/invalid field is `null`; `unavailable` records only the
  affected field and `unavailable`, `error` or `invalid`. No guessed defaults,
  exception text, API-object serialization, device ID, IP or Xbox credentials.
- `同期診断 <player>` includes `client_performance` near the beginning of its
  existing payload. The existing delayed inventory join probes also sample it.
- `join履歴` includes the latest join/performance events first, as valid JSON
  bounded to 1700 characters. It reports truncation. Verbose inventory probes
  stay in the in-memory ring; detailed inventory/HTTP/tick information is in
  sync diagnostics. The ring still holds at most 300 events and resets on restart.
- Docker/BDS logs supply persistence subject to the existing retention policy;
  this change does not introduce a telemetry DB or enable extra log collectors.

Use a narrow read-only log query, for example:

```sh
docker logs --timestamps --since 2026-09-30T15:00:00Z minecraft-bedrock-creative 2>&1 | grep -F '[NaritaBridge:client_performance]'
```

## Pack and runtime profile

- Python `minecraft_resource_packs.BIG_VIDEO_ENABLED = False` excludes the Big
  RP from the managed archive and puts its exact path/UUID in `retired_packs`.
- `wall_displays_config.js` has the matching flag. No Big runtime instance,
  wall detection, button handler, frame clock or helper creation runs.
- Existing Big helpers are removed by exact entity type
  `ichiyon:video_screen_big` from loaded dimensions on startup and on subsequent
  entity load/spawn events. No chunks are force-loaded. The helper BP definition
  stays available for identifying old persisted helpers.
- The physical wall/button, world blocks, mobs, maps, cosmetics and other
  helpers are untouched. Small and Akki video retain their clocks, audio, wall
  detection, buttons and recovery. Aquarium block definitions are unchanged.
- Active displays lazily share one player snapshot per tick. While all are OFF,
  their tick callbacks do not enumerate players or run video/audio processing.
  Stale active-display audio cleanup runs on startup/join; normal playback still
  retries failed audio cleanup. The existing wall/map scan remains every 100 ticks.
- Keep the PBR declarations introduced by PR #109, all RP manifests/UUIDs and the
  aquarium generator unchanged. The eight remaining RPs keep their contents and
  installed versions when the DB catalog has not changed.

To restore Big, set both flags to true, update the paused-profile expectations,
and validate the archive/runtime tests. Its retained manifest is the observed
pre-pause 1.0.1; compare against the actual last deployed Big version before
restoring and bump it if its contents have changed. Re-enable via an ordinary
managed release; do not copy raw pack directories or manually edit world refs.
PBR remains enabled throughout; no PBR restoration is necessary.

## Deployment boundary and observed baseline

**This PR does not deploy or restart production.** Before applying it, update the
standalone Control API's `minecraft_cosmetics_apply.py` from this release and
reload that service through its normal controlled deployment. It must recognize
Big's exact retirement UUID. An older service rejects the archive during staging
before stopping BDS; do not bypass that rejection or manually remove world refs.
Then deploy the immutable app revision and use the normal
[managed Minecraft release](managed-minecraft-release.md), which compiles current
DB assets and owns backup, stop/start, version selection, reference updates and
rollback. Production DB assets must remain in the generated catalog.

Read-only observation on 2026-10-01 before this change:

| Item | Production before | Proposed managed release |
| --- | --- | --- |
| Active RP count | 9 | 8 |
| Sum of active RP file sizes | 125,784,348 bytes (125.78 MB) | 23,416,424 bytes (23.42 MB), projected with the same DB assets |
| Big RP size | 102,367,924 bytes | 0 distributed bytes |
| Big runtime | Enabled | Disabled |
| PBR declarations | All 9 active RPs | Preserved on all 8 remaining active RPs |
| Behavior packs | 2 | Same 2; bridge Script content updated |

The after-size is a projection from the observed live files, excluding only Big.
It is not an observed deployed result or GPU
memory measurement. The actual export depends on the DB catalog at deployment.
An RP's PBR declaration does not prove a client's active graphics mode.

Observed versions and expected managed transitions if production is unchanged:
core stays 1.0.5; skins/posters stay 1.0.3;
accessories/small/records/Akki stay 1.0.1; aquarium stays 1.2.1.
Bridge BP changes 1.1.51 -> 1.1.52. Unchanged avatar BP should retain 1.1.51.
The service chooses versions above actual installed content versions, including
when the source manifest version is lower; never overwrite with guessed versions.

Fixture coverage includes nine installed PBR RPs -> eight unchanged PBR RPs, preservation
of foreign references and world bytes, exact UUID checks before shutdown, retained
original Big files, removal failure and post-install health failure restoring the
original packs/references/active marker byte-for-byte, and idempotent reapplication.
Original Big asset/builder/runtime tests remain runnable even while paused.

After approval/deployment, compare the same players, location, view direction and
client settings. Record join capability logs and a short client FPS/frame-time
sample alongside server load. If needed, restore only Big in a separately
approved test while keeping PBR and client settings constant.
