# Managed posters (batch 2026-09-24, section 6)

## Data and upload contract

- Migration `069_add_minecraft_managed_posters.sql` extends the existing catalog;
  it does not delete/rewrite skins, accessories, blobs, or deletion tombstones.
  IDs remain permanent and assigned under the existing table lock, scoped by kind.
- `/minecraft/cosmetics/assets` accepts authenticated, CSRF-protected skin and
  poster uploads only. Crafted accessory POSTs return 400 before database access.
  Existing accessory previews, catalog entries, export, and gameplay remain.
- Poster width and height are strict integers 1 through 10. Web uploads are bounded
  to 1 MiB per file. PNG, JPEG, and static WebP are decoded by Pillow, corrected for
  EXIF orientation, center-cropped, and resampled to `width*64` by `height*64` RGBA.
  Source dimensions are bounded to 4096 per axis. Animated images are rejected.
  Normalized PNGs may exceed the upload byte limit, so persisted textures accept
  the database's 4 MiB limit and can round-trip without a digest change.
- Texture bytes, dimensions, and immutable numeric IDs live in PostgreSQL.
  `poster_managed_<id>` is a separate identifier namespace from fixed posters.
  Dimensions and texture are included in the catalog digest. There is no poster
  deletion/replacement endpoint that could invalidate blocks in saved worlds.

## Compiler and runtime

- The compiler emits one Creative-visible base block plus width*height hidden
  segments, full texture, per-cell geometry/UVs, four direction permutations,
  terrain/block atlas entries, labels, Creative grouping, and catalog registry.
  Row zero is the bottom; image UV rows are inverted accordingly.
- Segment loot is empty. The shared script returns one base item when removing
  any segment, with a short duplicate-event guard. Creative/Spectator return none.
  Full inventories drop the remaining item in the poster's dimension.
- Placement validates every cell and wall before writing and rolls back partial
  expansion before refund. Unloaded cells/obstacles reject placement. Removal
  requires the exact segment ID and orientation, preserving neighboring posters.
  An unavailable removal cell fails without refund rather than duplicating items.
  The runtime reconstructs posters from segment IDs after script reload.
- The compiler reads current Molcar behavior files and changes cosmetics fields
  only. Ordinary Molcar movement `{ "value": 0.4, "max": 0.65 }`, component groups,
  and events are preserved. Garbage Molcar is not in the cosmetics compiler loop.
- Validated, metadata-free canonical RGBA PNGs retain their original bytes, so
  reading existing assets does not change fingerprints with the local encoder.
  Builder `--check` compares PNG dimensions, mode, exact RGBA pixels, and rendering
  metadata; `--write` preserves existing PNG bytes when those match. Compression
  and descriptive metadata differences alone do not require regeneration.
  Non-PNG output and catalog digest checks remain exact, apart from the existing
  CRLF normalization. No fingerprint algorithm migration was introduced.

## Integration status

- Bridge `main.js` integration is complete: managed catalog entries and the shared
  poster runtime are wired into the existing placement/removal callbacks without
  duplicate subscriptions. The temporary integration patch has been removed.
- Shared language and video sound integration is complete. The parent completed
  `build_minecraft_cosmetics.py --write` for 39 generated files after integration,
  with preservation of pixel-identical PNGs. Catalog output includes `posters: []`
  when no managed posters are registered. Batch versions are BP 1.0.38,
  RP 1.0.43, and bridge 1.0.35; static poster tests use that version contract.
- CI additions are complete. Builder checks also run through the existing
  cosmetics Python test entry point. The cosmetics database suite includes
  migration 069 and poster tests with the existing disposable PostgreSQL setup.
- The existing cosmetics router hosts the upload; no additional admin router
  registration is required. There is no remaining poster integration patch or
  deferred generated-file update from this implementation.

## Deployment contract

Migration 069 must be applied before the new repository writes. Dynamic poster
block/model/texture/loot files are emitted only when registered poster records
exist. Production pack creation must use current database rows, not a built-in-only
CLI catalog. Explicit revisions select `1.1.<revision>` through the existing
revision path. Local integration and generation do not establish deployment,
database migration, or live BDS validation success.

## Owned files

Modified:

```text
admin/minecraft_cosmetics.py
admin/templates/minecraft_cosmetics.html
bot/repositories/minecraft_cosmetics.py
bot/services/minecraft_cosmetics.py
bot/services/minecraft_cosmetics_pack.py
minecraft/behavior_packs/import_structures/scripts/cosmetics.js
scripts/build_minecraft_cosmetics.py
scripts/check_minecraft_cosmetics.py
scripts/check_minecraft_cosmetics_admin.py
scripts/check_minecraft_cosmetics_db.py
scripts/check_minecraft_posters.py
```

Added:

```text
bot/services/minecraft_cosmetics_posters.py
migrations/069_add_minecraft_managed_posters.sql
minecraft/behavior_packs/import_structures/scripts/poster_core.js
scripts/check_minecraft_managed_posters.py
scripts/check_minecraft_managed_posters.mjs
scripts/check_build_minecraft_cosmetics.py
docs/MINECRAFT_MANAGED_POSTERS.md
```

## Verification

Local commands (no generated pack writes):

```text
python scripts/build_minecraft_cosmetics.py --check
python scripts/check_build_minecraft_cosmetics.py
python scripts/check_minecraft_managed_posters.py
node scripts/check_minecraft_managed_posters.mjs
python scripts/check_minecraft_cosmetics_admin.py
node scripts/check_minecraft_cosmetics.mjs
python scripts/check_minecraft_cosmetics.py
python scripts/check_minecraft_cosmetics_db.py
```

The database test uses only fixed localhost disposable `cosmetics_test`, never
deployment environment variables. It includes real PostgreSQL migration
idempotence/legacy retention, shape constraints, blob reload, concurrent ID
assignment, and non-reuse of deleted IDs. The initial isolated local run could not
start because localhost PostgreSQL timed out and Docker's Linux engine was
unavailable; that attempt is not evidence of a successful database test run.
The JavaScript tests are offline state-machine tests, not evidence of a live BDS
run or of in-game rendering. Actual BDS visual/orientation testing remains with
the parent release validation.

Implementation validation passed 10 managed-poster Python checks, 9 managed-poster Node checks (including
400 size/direction combinations), 13 Web checks, 18 existing cosmetics Node checks,
and 13 existing cosmetics Python checks. Canonical PNG validation then passed the
same 33 focused checks on both Pillow 11.1.0 and 12.3.0, including 10 builder checks.
All generated non-PNG bytes and the committed built-in catalog digest matched
across those versions. Tests cover compression-only success, changed-pixel
failure, byte preservation on write, and exact JSON/catalog checks. Python compile
and whitespace checks also passed. Generation-current and manifest-version checks
remain enabled against the integrated outputs; no expected-failure exemption or
registry-check suppression is used.

After parent integration and regeneration, local reruns passed all 24 cosmetics
Python checks (including generation-current and builder checks), all 6 static
poster checks, and all 9 managed-poster Node checks. The static script contract
now checks the actual `posterRuntime` callbacks, managed registry imports, and
single place/break subscriptions while retaining the existing poster assertions.

Documented APIs used (no private or invented engine APIs):

- [Pillow ImageOps.fit and exif_transpose](https://pillow.readthedocs.io/en/stable/reference/ImageOps.html)
- [Block properties and setPermutation](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/block?view=minecraft-bedrock-stable)
- [BlockPermutation.resolve and getState](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/blockpermutation?view=minecraft-bedrock-stable)
- [Container.addItem remaining-item contract](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/container?view=minecraft-bedrock-stable)
- [PlayerBreakBlockAfterEvent.brokenBlockPermutation](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/playerbreakblockafterevent?view=minecraft-bedrock-stable)
