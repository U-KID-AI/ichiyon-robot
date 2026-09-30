# Paused Big Video: offline subpacks and enable boundary

This change does not deploy, merge to production, add a world reference, start
Big's runtime, or distribute Big to any client. Client log schema v2 is code for
a subsequent normal managed release; production continues running PR #111 until
that release is explicitly requested.

## Observed production, 2026-10-01 JST

Read-only SSH inspection at the start of this task confirmed:

- App release `a563bc0a8b1a84d80dd484ac4169c546481637c5` (PR #111).
- BDS `1.26.52.3`, healthy, started `2026-10-01 03:44:18 JST`.
- Eight active RP references; Big UUID `a9689c00-b236-53ec-b952-a5fa64cb0cbc`
  absent from `world_resource_packs.json`; its live RP directory absent.
- Compiler `BIG_VIDEO_ENABLED = False`; live Script flag also `false`.
- Managed retirement identifies only Big's exact path/UUID (plus the pre-existing
  legacy avatar RP retirement). Startup/entityLoad/entitySpawn cleanup targets
  only `ichiyon:video_screen_big`; the BP definition is retained for stale helpers.

The new slow-scan retry covers transient helper removal failures without loading
chunks, spawning helpers, writing blocks or touching small/Akki entities. Fixtures
cover failure/retry and unrelated mobs. This is not an observation that all
persisted entities in unloaded production chunks have been counted or removed.

## Source layout and tier selection

Only the inactive Big manifest moves to V3, UUIDs unchanged, version `1.0.2`.
V3 header/module versions and min engine version are SemVer strings; metadata
authors are present. The eight active RP manifests remain V2, including PBR.
`min_engine_version: "1.26.52"` is an authoring minimum, not proof of compatibility.

```text
ichiyon_video_big_rp/
  manifest.json                       V3, subpack thresholds 1 and 3
  entity/, models/, render_controllers/, textures/   static black fallback
  subpacks/lightweight/               same lightweight definitions, one 1x1 PNG
  subpacks/full/                      unchanged full definitions/audio/26 atlases
```

| Engine RP performance tier | Default selected subpack | Video atlas textures in selected view |
| --- | --- | --- |
| 1, 2 | lightweight | 0 |
| 3, 4, 5 | full | 26 |

The engine chooses highest threshold <= device tier; users may select a lower
compatible subpack. Base fallback is also lightweight. No Script-side selection,
device-name table or Script memory enum conversion is involved. The official
table lists current Switch at tier 1 and does not name Switch 2 as of the review
date; both rely on the engine's choice. Future engine device classification needs
no code update. [Official subpack selection](https://learn.microsoft.com/en-us/minecraft/creator/documents/buildingsubpacks?view=minecraft-bedrock-stable).

Full media remains **510.15 seconds, 128x72, 20fps, 10,203 frames, 26 atlases**
(1950x1998 each). `full_media.lock.json` pins the pre-pause 26 PNGs and OGG by size
and SHA256. No transcoding occurred. Atlas RGBA estimate: 405,194,400 bytes;
with mipmaps: 540,259,200 bytes. These are estimates, not measured GPU residency.
The light view has one black pixel, no frame animation and no registered audio.

## Compatibility verdict: enable BLOCKED

Microsoft still documents `memory_performance_tier` as experimental and requiring
V3. The manifest reference describes V3 as preview and requires string versions.
Do not copy the subpack tutorial's inconsistent legacy-format example into V2.
[Manifest reference](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/addonsreference/packmanifest?view=minecraft-bedrock-stable).

Local version-check tooling understands the narrow release SemVer authoring
contract so CI can review the inactive source. Production pack generation and
Control API apply deliberately remain V2-only. They reject V3 or V2 containing
`memory_performance_tier` before BDS stop, world-ref writes or active-marker
changes. Existing apply version selection/world refs still use three integers;
rollback for the eight active packs and Big retirement remains covered.

**Subpack resource selection does not prove selective network delivery.** A
whole Big RP contains full atlases even when its lightweight overlay contains
none. The current compiler/Control API have no per-client archive selection or
observed RP-performance-tier input. The fixtures prove the local selected file
view is atlas-free; they cannot prove the client's download/cache lacks full
atlas bytes. The stricter “do not distribute to Tier 1–2” requirement therefore
remains blocked too. No automatic tier-filtered download is claimed.

## Build and check, with Big still paused

```sh
python scripts/build_minecraft_big_video_subpacks.py --check
python scripts/check_minecraft_big_video_subpacks.py
python scripts/check_minecraft_big_video.py
python scripts/build_minecraft_big_video_subpacks.py --check-enable
```

The last command is read-only and currently exits **2** with explicit blockers.
It is not an enable command and has no override flag. Without `--check`, the
subpack builder regenerates only local lightweight files and the offline Big
manifest. The existing video builder writes Big's media directly under
`subpacks/full`; small/Akki output paths are unchanged. External original MP4s
remain outside Git as before. The committed media and original builder/tests
are retained, including synthetic atlas-boundary generation checks.

## Separate task to enable, never in this change

1. A human first confirms the phone stutter outcome with Big paused. Keep PBR,
   location and client settings recorded; do not infer the outcome from a tier.
2. Verify V3 automatic selection, manual downgrade and cold/warm pack loading
   against the actual BDS/clients in an isolated world. Measure Tier 1–2 loaded
   textures and network downloads/cache, not just logical file paths. Resolve the
   no-heavy-download requirement before proceeding; a whole archive is insufficient.
3. Add reviewed V3 support through compiler, standalone Control API, version
   comparison, world-ref encoding, cache invalidation, backups and rollback. Test
   paused V2 -> Big V3 -> rollback on independent fixtures and the isolated world.
   Keep UUIDs and require a version above the actual last Big install.
4. Replace the readiness check's blockers only with the validated contract and
   evidence. Update compiler/Script flags and paused expectations together in a
   separately requested PR; run all managed/archive/runtime/CI checks.
5. Only after that PR and explicit enable authorization, update the standalone
   service as needed and use the normal managed release. Never copy raw packs or
   edit production world refs by hand. Keep the paused release available for rollback.

For public client log fields, null/error handling and join-history/sync access,
see [client performance diagnostics](minecraft-client-performance.md). Script
`clientSystemInfo.memoryTier` and RP `memory_performance_tier` are independent.
