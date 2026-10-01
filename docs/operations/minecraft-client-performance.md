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
[NaritaBridge:client_performance] {"schema":"ichiyon.client_performance.v2","timestamp":"2026-10-01T00:00:00.000Z","player_name":"TestMobile","clientSystemInfo":{"platformType":"Mobile","memoryTier":0,"maxRenderDistance":12,"locale":"ja_JP"},"graphicsMode":"Deferred","inputInfo":{"lastInputModeUsed":"Touch"},"unavailable":{}}
```

- Timestamp is UTC ISO 8601; add nine hours to compare with JST reports.
- `clientSystemInfo.memoryTier` is the API enum (0 SuperLow, 1 Low, 2 Mid, 3 High, 4 SuperHigh),
  **not measured RAM capacity or free RAM**. Preserve zero as a valid value.
- Schema v2 groups fields by their actual API source. Older v1 lines remain valid
  historical logs, with flat keys. Do not convert Script `memoryTier` into RP
  `memory_performance_tier` (1..5), a selected subpack, GPU/SoC, RAM or model name.
  `Console` identifies no particular console. No Script API observation of the
  selected RP tier/subpack is available here, so neither is fabricated or logged.
- `clientSystemInfo.locale` is the selected language, safely read independently.
  The API added it in [1.26.20 / server 2.7.0](https://learn.microsoft.com/en-us/minecraft/creator/documents/update1.26.20).
  Missing/throwing locale does not affect the remaining readings or join processing.
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
  entity load/spawn events. Failed removal is retried in the existing 100-tick
  scan over loaded entities only. No chunks are force-loaded. The helper BP definition
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

Big's future V3 source now keeps all full media in `subpacks/full`. **Do not flip
the flags to restore Big:** the production V2 contract intentionally rejects it.
Use the separate-task prerequisites and read-only readiness checker in
[Big Video subpacks](minecraft-big-video-subpacks.md). PBR remains enabled.

## PR #111 deployment history and baseline

PR #111 was applied as `a563bc0a8b1a84d80dd484ac4169c546481637c5` on
2026-10-01, with BDS restarting at 03:44:18 JST. Its standalone Control API
retirement allowlist was updated first. Subsequent changes remain separate
deployments; the subpack PR does not change production. Use the normal
[managed Minecraft release](managed-minecraft-release.md), which compiles current
DB assets and owns backup, stop/start, version selection, reference updates and
rollback. Production DB assets must remain in the generated catalog.

Observed before/after PR #111 on 2026-10-01:

| Item | Production before | Verified after PR #111 |
| --- | --- | --- |
| Active RP count | 9 | 8 |
| Sum of active RP file sizes | 125,784,348 bytes (125.78 MB) | 23,416,424 bytes (23.42 MB) |
| Big RP size | 102,367,924 bytes | 0 distributed bytes |
| Big runtime | Enabled | Disabled |
| PBR declarations | All 9 active RPs | Preserved on all 8 remaining active RPs |
| Behavior packs | 2 | Same 2; bridge Script content updated |

The after-size and byte-identical contents of the remaining eight RPs were
verified on the live files after apply. These sizes are not GPU memory measurements.
Future exports depend on the DB catalog at deployment.
An RP's PBR declaration does not prove a client's active graphics mode.

Observed installed versions after PR #111:
core stays 1.0.5; skins/posters stay 1.0.3;
accessories/small/records/Akki stay 1.0.1; aquarium stays 1.2.1.
Bridge BP changed 1.1.51 -> 1.1.64 (compiled catalog revision 64).
Unchanged avatar BP retained 1.1.51.
The service chooses versions above actual installed content versions, including
when the source manifest version is lower; never overwrite with guessed versions.

Fixture coverage includes nine installed PBR RPs -> eight unchanged PBR RPs, preservation
of foreign references and world bytes, exact UUID checks before shutdown, retained
original Big files, removal failure and post-install health failure restoring the
original packs/references/active marker byte-for-byte, and idempotent reapplication.
Original Big asset/builder/runtime tests remain runnable even while paused.

After approval/deployment, compare the same players, location, view direction and
client settings. Record join capability logs and a short client FPS/frame-time
sample alongside server load. Big restoration remains blocked by the
[subpack compatibility prerequisites](minecraft-big-video-subpacks.md).
