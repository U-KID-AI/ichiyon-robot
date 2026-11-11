"""Verify real private audio plus public item/catalog metadata without publishing audio."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from build_molcar_record import ROOT, RECORD_GROUP, read_catalog


def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private', type=Path, required=True)
    args = parser.parse_args()
    proof = json.loads((args.private/'records-proof.json').read_text(encoding='utf-8'))
    source = Path('minecraft/behavior_packs/import_structures/scripts/molcar_records_catalog.js')
    records = read_catalog(args.private/'authoring'/source)
    baseline = read_catalog(ROOT/source)
    assert records == proof['registered_tracks']
    assert all(row in records for row in baseline), 'Existing public registrations must remain unchanged'
    assert len(records) == 4 and len({r['itemId'] for r in records}) == 4
    rp = args.private/'authoring/minecraft/resource_packs/ichiyon_avatar_rp'
    sounds = json.loads((rp/'sounds/sound_definitions.json').read_text(encoding='utf-8'))['sound_definitions']
    atlas = json.loads((rp/'textures/item_texture.json').read_text(encoding='utf-8'))['texture_data']
    for row in proof['tracks']:
        item_id, sound_id = 'ichiyon:record_'+row['key'], 'ichiyon:record.'+row['key']
        track = next(r for r in records if r['itemId'] == item_id)
        original, desktop = Path(row['source']), Path(row['ogg'])
        private_audio = rp/f"sounds/records/{row['key']}.ogg"
        assert digest(original) == row['source_sha256']
        assert digest(desktop) == digest(private_audio) == track['audioSha256'] == row['audio_sha256']
        assert private_audio.stat().st_size < 8*1024*1024
        # Retain complete decoded source, including encoder delay/padding semantics.
        assert abs(row['source_decoded_samples'] - row['output_decoded_samples']) <= 128
        assert abs(float(row['after']['input_i']) - float(proof['reference_loudness']['input_i'])) < 3
        assert float(row['after']['input_tp']) < 0
        probe = json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(desktop)]))
        streams = [s for s in probe['streams'] if s['codec_type']=='audio']
        assert len(streams)==1
        assert (streams[0]['codec_name'], streams[0]['channels'], int(streams[0]['sample_rate']), int(streams[0]['bit_rate'])) == ('vorbis',2,44100,128000)
        assert float(probe['format']['duration']) == track['durationSeconds'] == row['duration_seconds']
        assert sounds[sound_id] == {'category':'record','sounds':[{'name':f"sounds/records/{row['key']}",'stream':True}]}
        assert atlas[item_id]['textures'] == f"textures/items/record_{row['key']}"
        assert digest(rp/f"textures/items/record_{row['key']}.png") == track['iconSha256']
        item = json.loads((args.private/f"authoring/minecraft/behavior_packs/ichiyon_avatar_bp/items/record_{row['key']}.json").read_text(encoding='utf-8'))['minecraft:item']
        assert item['description']['identifier'] == item_id
        assert item['description']['menu_category'] == {'category':'items','group':RECORD_GROUP}
        for locale in ('ja_JP','en_US'):
            text=(rp/f'texts/{locale}.lang').read_text(encoding='utf-8')
            assert text.count(f'item.{item_id}.name=モルカーレコード：'+row['title'])==1
    old=ROOT/'minecraft/resource_packs/ichiyon_avatar_rp/sounds/records/lets_cooking_molcar.ogg'
    assert digest(old)==proof['reference_sha256']=='692eff62aa1d9566d3af7d5286cfeaaa65c2ce279c441c5c8a167b62d7aef113'
    old_item=json.loads((ROOT/'minecraft/behavior_packs/ichiyon_avatar_bp/items/record_lets_cooking_molcar.json').read_text(encoding='utf-8'))
    assert old_item['minecraft:item']['description']['menu_category']=={'category':'items','group':RECORD_GROUP}
    print('PASS 3 private tracks: hashes, complete decoded samples, loudness, Vorbis format, exact registrations; original preserved')


if __name__=='__main__':
    main()
