# Death Prairie Dog: bake the evaluated Blockbench pose

The immutable source is SHA-256
`03a67110f3dae8c1ba2b86f6325438b32f49f613018471358032bfe279c3d4ad`.
Mokuro is not part of this change.

## Why matching the official export did not match the preview

Blockbench 5.2.1 initializes `Group.all` in the saved `groups` array order:
`body, regL, regR, head, armL, armR, tail, bone`. Loading the outliner sets parents
without sorting that list. `Animator.stackAnimations` visits this list in order.
`BoneAnimator.displayRotation` cancels the parent's world quaternion **at that
point in evaluation**, not its final animated quaternion. Here `bone` is last:
its children cancel an identity parent, then inherit its subsequent rotation.

The Bedrock `relative_to: entity` representation instead specifies global
orientation independently of the animated parent. Modeling that contract gives
a 90-degree head/tail difference from the actual Blockbench preview at the end
of transition and in prairie4walk. The previous 0.01-degree zero workaround does
not resolve this difference. This is a mechanically reproduced export/preview
mismatch; the test is not an instrumented measurement of the Minecraft renderer.

Upstream sources used by the executable probe:

- [bbmodel group initialization](https://github.com/JannisX11/blockbench/blob/v5.2.1/js/formats/bbmodel.js)
- [outliner loading](https://github.com/JannisX11/blockbench/blob/v5.2.1/js/outliner/outliner.js)
- [stackAnimations](https://github.com/JannisX11/blockbench/blob/v5.2.1/js/animations/animation_mode.js)
- [interpolate/displayRotation/displayFrame](https://github.com/JannisX11/blockbench/blob/v5.2.1/js/animations/timeline_animators.js)
- [default ZYX Euler order](https://github.com/JannisX11/blockbench/blob/v5.2.1/js/io/format.ts)

## Bake and output

Evaluate each source time in saved group order using unit quaternions. After all
groups have been animated, compute `inverse(final_parent_world) * final_world`.
Convert the resulting local quaternion to ZYX Euler angles, retaining the existing
`[-x, -y, z]` Bedrock conversion. Only global tracks use the bake; ordinary tracks
retain their existing export. The source SHA and zero rest-rotation assertion
bound this exporter to the verified model, not arbitrary future rigs.

For this specific evaluation order, the baked local angles equal the original
numeric local angles after coordinate conversion. They are calculated, not
guessed or replaced by +/-180 degrees:

| Clip/time | head local X | tail local X | body local X | root `bone` local X |
| --- | ---: | ---: | ---: | ---: |
| transition 0 | 0 | 0 | 0 | 0 |
| transition 0.5 | -90 | -90 | 0 | +90 |
| prairie4walk 0 | -90 | -90 | 0 (rest) | +90 |
| prairie2walk | 0 (rest) | 0 (rest) | 0 (rest) | 0 (rest) |

At transition end/four-walk start, Blockbench-coordinate world X rotations are
head=0, tail=0, body=-90, root=-90. Zero states are ordinary local zero; the
entity-global epsilon is no longer applicable. No output `relative_to` remains.
All 27 keyed entries, key times, .5/1/1-second lengths, loops, limb translations,
geometry, UVs and texture bytes are preserved. No client entity, AI or speed changes.

## Continuity and return

Raw transition end and four-walk start have identical world rotations for all
eight bones, and identical full world transforms for head/body/tail/root.
The four limb pivots differ by one model unit: these are the source walk's
initial translation keys. **Raw full-transform equality for those limbs is not
claimed.** Altering them would violate source-motion preservation.

All three controller states now cross-fade on exit over 0.1 seconds. This bridges
the preserved gait offsets and returns chase/aborted transition to normal without
an abrupt 90-degree reset. Transition clip duration and the 0.5-second chase gate
are unchanged. At the switch boundary, outgoing pose weight is one; at completion
it is zero. Wander references only prairie2walk; every frame starts from the rest
pose, so no chase rotation remains. See the
[Bedrock cross-fade contract](https://learn.microsoft.com/en-us/minecraft/creator/documents/animations/animationcontroller?view=minecraft-bedrock-stable).

## Independent evidence

`node scripts/probe_death_prairie_blockbench.mjs` prints an optional networked
oracle. It executes upstream 5.2.1 interpolation, display and stack methods with
THREE r129 against the real source, with numeric one-point Keyframe adapters and
UI/effect hooks stubbed. Downloads are executed only by this explicit probe,
not the exporter or CI. The committed oracle records source/upstream hashes,
saved group order, and all eight world quaternions and positions at 15 frames.

Offline tests use that recorded oracle plus an independent matrix evaluator at
303 times. Isolated A/B tests reconstruct the previous entity-global contract
only in test memory and prove its head/tail mismatch; production contains B only.
Tests compare whole-hierarchy world transforms, key counts/content, zero/rest
regression, geometry/PNG hashes, quaternion mixed-axis/gimbal conversions and
controller reachability. Boundary tests explicitly distinguish matching rotations,
preserved gait offsets and full-pose equality at cross-fade weight zero/one.

The source RP manifest and builder's default RP revision must advance together
for the repository's cache/version contract. Managed production assembly still
chooses installed split-pack versions and preserves DB assets through the normal
release transaction; no source pack is copied directly to BDS.
