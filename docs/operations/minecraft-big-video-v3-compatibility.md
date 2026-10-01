# Isolated V3 BDS observations — 2026-10-02 JST

**Partial server-side evidence; production enable remains blocked.** No actual
Bedrock client connected. Automatic selection, cache invalidation, texture loading
and full/black-screen playback were not tested. No production world/pack/container
was modified or restarted by this investigation.

## Environment

- Production and test BDS: `1.26.52.3`, commit
  `54116bb5ee0b7aee0b5591efbcc8bc3cf64f168a`.
- Image: `itzg/minecraft-bedrock-server@sha256:d252669ce19d73718ef5167897235c5118e4ae1b7011cf6db3733b5d2cd2670b`.
- Separate new flat creative world `tier-probe`; no production data, BP, secrets,
  API credentials or player records copied. Empty allowlist; no published host
  ports. CPU limit 1, memory limit 2 GiB. The initial 768 MiB test allocation OOMed
  during world creation; it was raised for all recorded contract probes.
- Test container `ichiyon-big-v3-isolated-20261002`; retained stopped after tests.
  Data: `/home/ubuntu/ichiyon-big-v3-isolated-20261002/data` on the Minecraft host.
- Tiny separate probe from `scripts/build_minecraft_rp_tier_probe.py`: V3,
  versions as below, metadata authors, SemVer min-engine `1.26.52`, five small
  subpacks with performance tiers 1–5; no Big media.

## Direct server observations

[Machine-readable observations](evidence/big-video-v3-server-20261002.json) contain
UTC timestamps and filtered pack diagnostics, no identities/authentication data.
Each row used a graceful stop, isolated manifest/ref edit and startup.

| Start (JST) | Manifest version | World-ref version | Result |
| --- | --- | --- | --- |
| 01:13:20 | V3 `1.0.0` | `[1,0,0]` | Server started; no missing-pack/manifest warning |
| 01:13:38 | V3 `1.0.0` | `"1.0.0"` | Server started; no missing-pack/manifest warning |
| 01:13:55 | V3 `1.0.1` | `[1,0,1]` | Server started after update; no pack warning |
| 01:14:12 | V3 `1.0.0` | `[1,0,0]` | Server started after restoring old version; no pack warning |
| 01:14:29 | Deliberately invalid JSON | `[1,0,0]` | Server still started, but warned manifest invalid/deprecated and configured pack not found/ignored |

Refs remained byte-semantically unchanged in every case. No OOM occurred in the
recorded cases. After the negative control, valid `1.0.0` manifest/vector refs were
restored and the test container stopped.

The negative control supports the conclusion that valid V3 was discovered, rather
than every test silently omitting the RP. It also proves that **server startup
alone is insufficient**: even a missing/invalid RP permits startup. `Pack Stack -
None` logs only the absence of behavior packs here; production's corresponding
lines list its BPs, not its eight RPs. Do not use that line as an RP-load verdict.

The isolated server reported the default transport is unsuitable for actual
connections in this release (NetherNet required). This deliberately unconnected
test therefore supplies no network/client compatibility evidence. Setting up
client access must use an isolated transport/identity and must not copy the
production identity or weaken the production allowlist.

## What these tests do not certify

- Whether the actual Bedrock client accepts this manifest and automatically
  selects the expected performance-tier subpack, or honors manual downgrade.
- Whether vector/string refs result in the exact pack/version sent to clients.
- Whether the client replaces cached `1.0.0` with `1.0.1`, and correctly loads
  restored `1.0.0` after rollback. The observed server restart is not managed
  rollback or client cache proof.
- Whether a low selected view avoids full atlas decode/load/residency when the
  whole RP is downloaded. This probe contains no full media and cannot measure it.
- V3 managed archive/compiler/Control API safety. The live guard remains V2-only;
  existing V2 apply/retirement/rollback and rejection-before-mutation fixtures pass.

No V3 production contract is adopted from these partial results. Continue with
the [client probe and enable procedure](minecraft-big-video-subpacks.md), keeping
Big paused until the remaining conditions are resolved.
