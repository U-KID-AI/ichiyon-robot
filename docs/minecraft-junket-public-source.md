# JUNKET public source and private build inputs

The public pack contains the seventeen game reducers, shared GameManager,
reception forms, decision resume, safe chunk loading, scoped fixture repair,
unanimous reset, and bank menu routing. `main.js` uses the independently placed
public venues. `all17_reference_main.js` is an alternate private-build entry;
its generated `all17_original_generated.js` is deliberately absent from Git.
The bank placement module similarly ships a disabled `BANK_HUB` stub.

## Public assets

`scripts/minecraft/build_junket_original_world.py` creates the substitute
structures, models and textures without reading an original archive.
`build_junket_pig_textures.py` and `build_junket_walker_textures.py` draw the
additional props and labels from code. The Hope judge client uses vanilla
resource bindings. No captured original pixels, original models, original
building structures, source-world files or recordings are included.

Walker coordinates in the reducer are a local logical board, generated from
row populations and spacing. The public venue places that board independently.
Private absolute source coordinates are not part of that reducer or generator.
The old `warp_row.mcstructure` is obsolete: routing now uses the authenticated
requester's explicit script event and the generated catalog.

Use the existing offline checks, for example:

```text
node scripts/check_minecraft_junket_complete.mjs
node scripts/check_minecraft_junket_safe_warp.mjs
node scripts/check_minecraft_junket_recovery.mjs
node scripts/check_junket_bank_ui.mjs
python scripts/minecraft/build_junket_original_world.py --check
python -m unittest discover -s scripts/minecraft -p test_junket_private_profile.py
```

These checks cover reducers, mock Script API/UI contracts, conversion math and
small synthetic world fixtures. They do not establish a new BDS deployment or
PC/mobile client playthrough. Optional private-manifest checks require their
explicit private input arguments; they are not claimed by the public fixtures.

## Private conversion configuration

Set `JUNKET_PRIVATE_PROFILE` to a separately protected local JSON file. It has
`{"schema": 1, "values": {...}}`; source material is never evaluated as code.
The profile is an input, not a generated public artifact. Obtain values from
the preserved local converter and verified source evidence, and retain the
profile with those private inputs. Do not place it in the public repository.

| Key | Retained private input |
| --- | --- |
| `all17.bounds` | Game ID to original extraction bounds |
| `all17.safe_arrivals` | Game ID to corrected original arrival |
| `all17.hope_reception`, `all17.hope_return` | Original-room guide anchors |
| `all17.floor_templates` | Clock/Walker source template bounds |
| `bank.bounds`, `bank.reception`, `bank.entrance`, `bank.arrival` | Original bank extraction and anchors |
| `bank.arrival_probe_heights` | Floor/feet/head probe elevations |
| `bank.head_positions`, `bank.structure_size` | Verified original head anchors and structure extent |
| `bank.production_offset` | Reviewed source-to-placement transform |
| `bank.bound_evidence` | Private extraction-bound evidence |
| `sparrow.bounds`, `sparrow.spawn`, `sparrow.reference` | Private isolated-trial extraction, spawn and complete room reference |
| `barrier.floor_policy` | Game ID to reviewed removable-floor policy |

Floor policies contain a `planes` list; each entry has integer `y` and optional
inclusive `x`/`z` bounds. Clock additionally requires its private layout's
`floor_template.source_target_offset`. Newly derived layouts retain the resolved
policy in `source_floor_policy`. Missing or malformed data fails closed; the
native migration validates it before stopping the server. Other ordinary blocks,
actors and player data remain subject to the existing transaction/rollback
checks. Do not infer private floor support from an approximate public venue.

The coordinate-heavy `build_junket_original_runtime.py` and
`build_junket_panel_layouts.py` remain in the preserved private source tree.
Their original-room calibration recipes have not been republished. Generic
model mapping, fixed-actor protection and source parsing helpers are retained
in `junket_original_runtime_support.py` with synthetic regression fixtures.

The public bundle builder therefore requires `--runtime-manifest` pointing to
the already reviewed private runtime JSON. It verifies all seventeen IDs,
private/native flags, bounds and arrivals against the selected private layouts
before creating output. It then binds repair tiles to the derived layouts and
records the input SHA-256 in its proof. A full native bundle still needs the
original private structures/models and those retained private calibration tools;
the public checkout alone cannot recreate the original rooms.

`junket_verified_dependencies.py` requires `--baseline-operation` explicitly.
Select a retained successful release; no user's historic operation ID is a
default in the public source. No pip installation or live-world modification is
part of dependency validation.

## Preservation and deployment boundary

Do not replace an installed private BP/RP with the public substitute pack to
make Git match production. The native release and static-overlay adapters must
retain installed private materials and validate their explicit candidate inputs.
Preserve the current world, previous successful recovery point, source profile,
private runtime JSON and archive provenance independently of Git. This source
integration changes neither an installed pack nor a running world.
