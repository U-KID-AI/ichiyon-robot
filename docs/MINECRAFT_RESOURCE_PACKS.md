# Managed resource packs

`ichiyon_avatar_rp` remains an authoring input for the existing asset tools. It is
not included in a production managed archive. `pack_zip` first merges the current
database skins/accessories/posters, then partitions the resulting assets without
re-encoding textures or audio:

| Pack | Content |
| --- | --- |
| ichiyon_core_rp | Shared resources and static mobs |
| ichiyon_mannequin_skins_rp | Player/mannequin bindings, models and skin textures |
| ichiyon_accessories_rp | Molcar accessory bindings, models, textures and icons |
| ichiyon_posters_rp | Static and registered poster geometry/textures |
| ichiyon_video_rp | Video atlases, audio, geometry, material and controller |
| ichiyon_records_rp | Long record audio, icon and sound definition |

Each asset or registry entry has one owner. Registry container files and language
lists can occur in multiple packs, but their resource entries are not duplicated.
The manifests have fixed distinct UUIDs. Database revision is not an RP version.
The Control API compares normalized content with the installed pack, retains an
unchanged pack's version, and increments only a changed pack. It updates world
references together with the pack installation while BDS is stopped.

The archive declares the legacy RP as retired. New resources and references are
installed before retirement. The existing application backup/rollback covers the
old pack, new directories and world references. Never deploy the authoring tree
over DB-generated production packs. Deploy application code, then generate the
managed archive from production DB through the existing release API.

The Control API implementation on the Minecraft host must be upgraded before
applying the first split archive. Existing three-pack archives remain accepted.
