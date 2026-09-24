# Big video media

## Chosen Profile

`big-128`: 128 x 72 RGB PNG frames at **20 fps**, as explicitly permitted
by the FPS override. No client interpolation or seconds property is used.
Only this full profile was generated; the other resolutions used samples.

Source `3865.mp4` stays outside Git. FFprobe measured 24,332,100 bytes,
640 x 360, 24 fps, 12,244 frames, 510.166667 seconds of video and
510.188844 seconds of container duration; AAC stereo, 44,100 Hz.

Eight distributed 3-second samples produced 480 output frames per profile:

| Profile | Estimated PNG bytes | Atlases | Atlas dimensions | Decoded RGBA bytes | With mipmaps |
| --- | ---: | ---: | --- | ---: | ---: |
| 192 x 108 | 155,853,940 | 57 | 1940 x 1980 | 875,793,600 | 1,167,724,800 |
| 160 x 90 | 115,701,264 | 39 | 1944 x 2024 | 613,806,336 | 818,408,448 |
| 128 x 72 | 81,786,548 | 26 | 1950 x 1998 | 405,194,400 | 540,259,200 |

128 x 72 avoids the much higher resident texture cost of the larger profiles.
The sample PNG estimate is approximate: actual full PNG bytes were about 20%
higher. All atlas dimensions stay below 2048, with one-pixel extruded edges
and corners. Grid: 15 columns x 27 rows, 405 frames per atlas.

## Actual Output

- Frames: **10,203**, duration **510.15 seconds**, 20 fps. FFmpeg FPS rounding
  gives one fewer frame than the conservative 10,204-frame sample estimate.
- PNG atlases: **98,461,214 bytes**; black texture: 69 bytes.
- Audio: **3,899,402 bytes**, Ogg Vorbis mono, 44,100 Hz, 510.15 seconds.
- Media bytes: **102,360,685** (97.62 MiB).
- Generated RP bytes excluding the separately maintained manifest: **102,367,828**
  (97.63 MiB on the Windows build host; Git text-newline normalization can
  slightly change the JSON/material byte count).
- RGBA per atlas: 15,584,400 bytes; all atlases: **405,194,400 bytes**
  (386.42 MiB), or **540,259,200 bytes** (515.23 MiB) with full mipmaps.

Memory assumes all atlases resident and no GPU compression, excluding other
packs, engine overhead and audio buffers. Even 128 x 72 has substantial texture
memory cost; constrained-client performance requires the final in-game check.

## Runtime Contract

- Entity: `ichiyon:video_screen_big`.
- `ichiyon:frame` is the only custom property: synced integer, default -1
  for OFF, range -1 through 10,202. Existing runtime supplies the 20 fps clock.
- `video_big_media.generated.js` exports `VIDEO_BIG_MEDIA` containing only
  `fps`, `frameCount`, `duration`, `sound`. No `interpolated`,
  `ichiyon:media_seconds` or `pre_animation`.
- Video plane height 176 model units, width 176 * 16 / 9, z -0.5;
  black plane 384 x 176, z 0. Model north faces world +Z at yaw 0.
- Bounds: width 26, height 13, center Y 5.5.
- Private material `ichiyon_video_big_uv` extends `entity_alphatest` with
  `USE_UV_ANIM`, preserving the corrected small-screen render path.
- Sound: `ichiyon.video_screen_big.audio`, category `record`, `stream: true`,
  `is3D: true`, min distance 32, max distance 64; runtime gain 1 avoids a
  second manual attenuation curve. Runtime applies rectangular audience gating.
- Existing streamed Molcar records already use `SoundInstance.seekTo`,
  `setVolume` and `stop` with the installed 2.11.0-beta API. This is the local
  compatibility precedent; live seeking still needs an in-game check.

Assets are written directly into `resource_packs/ichiyon_video_big_rp`.
Only its big-video sound is registered there. The builder does not write a
manifest or modify pack versions/plumbing. Small-screen assets stay intact.

## Reproduction And Checks

```powershell
python scripts/build_minecraft_video.py <external-source-mp4> --sample-big-profiles
python scripts/build_minecraft_video.py <external-source-mp4> --profile big-128
python scripts/check_minecraft_big_video.py --source <external-source-mp4>
python scripts/check_minecraft_wall_displays.py
```

Omitting `--profile` preserves the existing small-screen output. A 5.15-second
fixture was generated with both the pre-change builder and the generalized
default: all 12 files (two atlases, audio, definitions, JS and report) matched
byte for byte. Focused big checks also cover every actual frame's extruded
edges/corners, atlas rollover, unused black cells, sound metadata, generated
JS, geometry, JSON/material parsing, output bytes and report memory figures.
No full-profile regeneration occurs in the checker. Final client checks:
picture/facing and near-screen quality, smooth 20 fps, independent buttons,
OFF/restart, loop audio sync, late-entry/re-entry seek, audience coverage,
silence behind the screen, and unchanged small-screen playback.
