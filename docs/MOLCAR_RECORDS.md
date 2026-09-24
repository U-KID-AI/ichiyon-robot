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

The source `C:/Users/syoub/Desktop/神社/ogg/lets_cooking.ogg` was located during
the final asset search. Its identical Downloads copy confirms the same bytes.
The original OGG is copied without re-encoding: Vorbis stereo, 44.1 kHz,
261.247710 seconds, SHA-256
`692eff62aa1d9566d3af7d5286cfeaaa65c2ce279c441c5c8a167b62d7aef113`.

The office-PC icon was unavailable. A new fixed transparent PNG was generated
with the built-in image tool, not represented as the missing original: a black
pixel-style vinyl record with an orange/white Molcar chef label, no lettering.
Prompt: "Minecraft inventory music record icon, transparent background, crisp
pixel-art, black vinyl, orange and white guinea-pig-car face label with a tiny
chef hat, front-on flat sprite, no text or scene." The selected image is stored
at `minecraft/resource_packs/ichiyon_avatar_rp/textures/items/record_lets_cooking_molcar.png`.
No existing model, texture or source image was changed.

Creative item / give identifier: `ichiyon:record_lets_cooking_molcar`.
Use it on an ordinary Molcar; while riding, item use also controls that car.

Validate/import explicitly selected assets with:

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
