# Garbage Molcar and Kuma Paw

- `ichiyon:garbage_molcar`: separate friendly Mob; Creative spawn egg or `/summon`.
- Empty hand: start following. Owner interacts again to withdraw individual stacks,
  withdraw all, or explicitly stop following. Stopping requires confirmation and
  permanently clears stored contents. Logging out does not clear them.
- Normal mode removes nearby item entities. Owner mode stores complete native
  ItemStacks in a persistent, private 54-slot inventory. A stack that cannot fit
  completely stays on the ground; withdrawals leave untransferred contents stored.
- Pickup runs every 10 ticks, querying only loaded garbage Molcars. Within 8
  blocks, up to 8 drops are checked for capacity and an unobstructed approach.
  Horizontal physics impulses approach a drop while native wandering/following
  pauses; this is a short direct detour, not navigation around walls. Collection
  occurs within 1.1 blocks (at most 16 drops). Normal wandering/owner navigation
  resumes afterward. A leash takes priority; detours remain within 8 blocks of
  the online owner. Offline ownership and stored ItemStacks remain intact.
- `ichiyon:bartholomew_kuma_paw`: Creative Equipment or `/give @s` with this ID.
  Interact with a living Mob to remove it without death, loot or XP. Players and
  non-Mob entities are excluded. The paw is not consumed.
- Mokuro's carry prompt is empty-hand-only; leads are handled by native leash
  interaction. Its mobile leash distances are explicitly 4 / 6 / 12 blocks.

The ordinary Molcar's BP, geometry, texture, animations and combat script are
unchanged. The new Mob shares only animation/sound references, with copied and
recolored geometry plus a separate generated texture and truck cargo cubes.
The generated paw icon is 64x64 RGBA; the generated Molcar atlas is 128x64 RGBA.

Regression: `python scripts/check_minecraft_garbage_molcar.py` and
`python scripts/check_minecraft_mokuro.py`. No migration or external command
endpoint is added. Runtime acceptance also requires a real Bedrock client for
the appearance and ActionForm layout.
