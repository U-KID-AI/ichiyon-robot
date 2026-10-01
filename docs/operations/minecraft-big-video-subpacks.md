# Big Video: paused, measured-tier enable preparation

PR #112 includes main through `f28c116` (#113/#114 storage changes). Big remains
retired from the managed catalog, and the Script flag stays false. This PR does
not add a production world reference or deploy Big. Client diagnostics v2 remain
included for a subsequent release.

## Observed production, 2026-10-02 01:02 JST

Read-only SSH inspection, independently of GitHub main, confirmed:

- App release `f28c11614174d535edd53a213f5b515980a51b0b`.
- BDS `1.26.52.3`, healthy, started **2026-10-02 00:24:09 JST**.
- Eight RP references. Big UUID `a9689c00-b236-53ec-b952-a5fa64cb0cbc` and its
  live RP directory are absent; live Script `BIG_VIDEO_ENABLED = false`.
- Latest managed application succeeded at **00:24:28 JST**.

Retirement uses Big's exact path/UUID plus the pre-existing avatar retirement.
Only `ichiyon:video_screen_big` helpers are removed on startup, entity load/spawn
and slow-scan retry. Unloaded chunks are never forced to load. This does not
claim that every persisted helper has been counted or removed.

## No adopted full threshold

The earlier fixed tier-3 choice is withdrawn. `FULL_MIN_TIER = None` and the
inactive V3 manifest lists **only lightweight at tier 1**. Full assets remain
under `subpacks/full`, but that folder is not selectable. All current RP tiers
1 through 5 resolve to lightweight in the offline selection model.
Big itself remains absent from production.

`threshold_from_observed_rp_tiers(pixel9a, es, sourui)` models the requested
decision: full starts at Pixel's observed automatic RP tier only when both
protected players and Switch tier 1 are strictly below it. Unknown/equal/higher
protected tiers return `None`, keeping *all* tiers lightweight. The helper does
not certify its inputs, ingest logs or enable anything. Tests cover all
combinations of tiers 1–5 and each possible future threshold 2–5.

Never use Script memoryTier as those inputs. The user identified Pixel 9a as
`FellYapper4649`. Its available production samples and Sourui3's samples both
show `Mobile`, Script memoryTier `3`, maxRenderDistance `22`, `Deferred`, `Touch`.
Equal diagnostics establish neither equal nor different RP tiers. Es6741 has no
client-performance sample in the inspected September 30–October 2 logs.
Guessing that Sourui is below Pixel has no supporting discriminator here.

The documented selector has no player-name predicate. The current compiler
distributes one world RP stack, not per-player archives. Hiding a player's
screen/audio would not prevent atlas loading. Named-player load exclusion is
**not implemented or claimed**. If tiers overlap, keep that whole tier light;
this may prevent Pixel playback too.

Microsoft documents highest threshold <= device with manual downgrade and lists
current Switch at tier 1. No Switch 2 or other device lookup is encoded.
[Official subpack selection](https://learn.microsoft.com/en-us/minecraft/creator/documents/buildingsubpacks?view=minecraft-bedrock-stable).

## Assets and download versus loading

```text
ichiyon_video_big_rp/
  manifest.json                       V3; only tier-1 lightweight registered
  entity/, models/, render_controllers/, textures/   static black fallback
  subpacks/lightweight/               same definitions; one 1x1 black PNG
  subpacks/full/                      retained, unregistered full assets
```

UUIDs stay unchanged and Big's offline version is `1.0.2`. Eight active RPs stay
V2 with PBR. Base/lightweight have zero atlases, no OGG/sound registration,
no animation/frame switching and no full definitions. Layout validation rejects
even an unreferenced atlas outside full.

Full remains **510.15 seconds, 128x72, 20fps, 10,203 frames, 26 atlases**.
`minecraft/video_screen_big/full_media.lock.json` locks all 26 PNGs and the OGG
against pre-pause bytes by size/SHA256. RGBA 405,194,400 bytes / mipmap estimate
540,259,200 bytes are estimates, not measured residency.

A logical selected view proves placement, **not client behavior**. Whole-pack
download/cache may contain full media. The current request permits that if low
clients demonstrably do not decode/load or make full atlases resident. Record
network/cache bytes separately. A black screen, small overlay or unchanged FPS
alone cannot prove texture residency.

## Tiny RP-tier probe (isolated clients only)

```sh
python scripts/build_minecraft_rp_tier_probe.py --output /tmp/rp-tier-probe.mcpack
```

Separate UUID `301f1595-9d5b-569a-991a-c6b5f5d6a842`, version `1.0.0`.
Five tiny overlays at thresholds 1–5; no video/audio/Script or production
connection. Only cobblestone texture/name changes inside the test world.
Base shows `0` / `SELECTION UNVERIFIED`; overlays show `RP TIER 1`–`RP TIER 5`
and a block number. English/Japanese labels are included. Do not add to global
resources or production.

1. Record client version/date/player name. Import into a fresh disposable
   creative world with no other RPs or cached manual subpack choice.
2. Activate the probe, leave the selector untouched, enter the world and
   hold/place cobblestone. Record the label, block number and selected-subpack
   screenshot. Base `0`, import failure or disagreement means inconclusive.
3. Record automatic selection **before** manual downgrade. Repeat a fresh
   import/world to exclude saved overrides. A displayed choice alone does not
   prove that this exact stable client supports V3 automatic selection.
4. Record Pixel, Es and Sourui separately. Currently only Pixel is available.
   Missing protected-device results remain unknown; Script memoryTier cannot
   establish their RP tiers.

## Compatibility and enable boundary

Official docs label performance subpacks experimental/V3 and manifest V3 preview.
[Manifest reference](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/addonsreference/packmanifest?view=minecraft-bedrock-stable).
An authoring minimum of `1.26.52` does not prove stable support.

Compiler/Control API remain V2-only, rejecting V3 or V2 containing performance
tiers **before** BDS stop, ref writes or marker changes. Do not remove the guard
based on server startup or offline fixtures alone. The offline version checker
supports V3 SemVer for inactive authoring only. Existing V2 apply/retirement/
rollback tests preserve UUID/version, foreign refs, backup, health, active
marker and failure recovery.

See the [isolated compatibility record](minecraft-big-video-v3-compatibility.md).
Client selection, cache invalidation and low-client texture loading remain
separate requirements even if BDS accepts a manifest/reference.

## Validation and subsequent enable

```sh
python scripts/build_minecraft_big_video_subpacks.py --check
python scripts/check_minecraft_big_video_subpacks.py
python scripts/check_minecraft_big_video.py
python scripts/build_minecraft_big_video_subpacks.py --check-enable
```

Read-only `--check-enable` exits **2** with blockers, flags, null full threshold
and all-light selection; no override switch. `manifest_for_threshold` is for
future isolated fixtures; generated source stays all-light. The normal video
builder writes media into full; small/Akki paths stay unchanged.

Resolve observed/protected tiers, then test actual BDS + clients for automatic/
manual selection, refs, update, rollback and cache invalidation. Low clients
must complete cold join/download/cache/world join/near-screen/ON-OFF with
texture-load evidence. Implement only the successfully measured V3 contract.
Run tests/CI, take a verified production backup, use managed apply and verify
health plus real low/full behavior. Never hand-edit production refs or substitute
render-only player exclusion.

[Client diagnostics v2](minecraft-client-performance.md) remain diagnostic only;
these logs never drive RP selection.
