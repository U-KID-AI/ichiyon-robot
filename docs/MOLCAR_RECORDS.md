# Molcar Records

The runtime retains one playback state per real Molcar and a separate
`SoundInstance` per nearby player. It never consumes the record, writes an
inventory, or stops another Molcar's handle. An outside player can operate a
stationary car (horizontal speed below 0.04); a rider can operate their moving
car. Reusing the current record stops it. Another track replaces it.

Listeners within 16 blocks receive distance-attenuated playback and seek to the
current position when entering range. Departure, dimension changes, death,
unload and reload stop playback. The runtime updates only active recordings.

## Source Asset Status

The requested fixed **Let's Cooking Molcar** OGG and icon were not present in
the supplied files, this PC's audio directories, or the inspected existing
Minecraft packs on 2026-09-24. The catalog therefore remains empty. No silence,
unrelated song, fabricated duration or substitute icon is published as the
requested record. This feature is not yet usable in production.

Once the actual assets are available, validate/import them with:

```powershell
python scripts/build_molcar_record.py --audio <original.ogg> --icon <original.png>
python scripts/build_molcar_record.py --audio <original.ogg> --icon <original.png> --write
node scripts/check_molcar_records.mjs
```

The importer measures duration with ffprobe, copies original bytes, records
SHA-256, and adds the item, texture registration, sound definition, translations
and runtime catalog. The existing managed pack builder includes these static
assets alongside database-managed cosmetics. Normal review/tests/deployment
are still required after importing real assets.

API reference: [SoundInstance](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/soundinstance?view=minecraft-bedrock-experimental).
