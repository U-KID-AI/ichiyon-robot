"""Akki assets, builder fixtures, full managed archive and public apply contracts."""
import argparse
from io import BytesIO
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile

from PIL import Image, ImageChops
from build_minecraft_video import AKKI, SMALL, BIG_PROFILES, ROOT, build, probe, run, write_definitions

sys.path.insert(0, str(ROOT))
from bot.services.minecraft_cosmetics_pack import builtin_assets, pack_zip
from bot.services.minecraft_resource_packs import VIDEO_AKKI, DIRECT_RESOURCE_PACKS, MAX_ARCHIVE, MAX_EXPANDED, MAX_FILE, MAX_FILES
from scripts.minecraft.minecraft_cosmetics_apply import unpack

RP = ROOT / 'minecraft' / VIDEO_AKKI
SOURCE = None
def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


class AkkiVideoChecks(unittest.TestCase):
    def test_source_profile_and_generated_frame_contract(self):
        report = read(ROOT/'minecraft/video_screen_akki/build_report.json')
        self.assertEqual(report['source']['sha256'], 'bab5cb8f68e0ed19de85efa8379804cd23a69648baf451b2929d26277d106835')
        self.assertEqual((AKKI.fps, AKKI.width, AKKI.height, AKKI.stem, AKKI.rp_name),
                         (20,64,36,'video_screen_akki','ichiyon_video_akki_rp'))
        self.assertEqual((report['output']['frameCount'], report['output']['atlasCount']), (275,3))
        self.assertEqual(report['output']['duration'], 275/20)
        self.assertLess(abs(report['output']['duration']-report['source']['videoDuration']), .05)
        bp = read(ROOT/'minecraft/behavior_packs/ichiyon_avatar_bp/entities/video_screen_akki.json')['minecraft:entity']
        self.assertEqual(bp['description']['properties']['ichiyon:frame']['range'], [-1,274])
        script = (ROOT/'minecraft/behavior_packs/import_structures/scripts/video_akki_media.generated.js').read_text()
        data = json.loads(script.split(' = ',1)[1].strip().rstrip(';'))
        self.assertEqual(data, {k:report['output'][k] for k in ('fps','frameCount','duration','sound')})
        self.assertFalse(list((ROOT/'minecraft').rglob('*.mp4')))

    def test_centered_south_facing_13_by_4_black_and_16_by_9_video(self):
        geos = read(RP/'models/entity/video_screen_akki.geo.json')['minecraft:geometry']
        video, black = [g['bones'][0]['cubes'][0] for g in geos]
        self.assertEqual(black['size'], [208,64,0])
        self.assertEqual(video['size'], [64*16/9,64,0])
        for cube in (video,black):
            self.assertEqual(cube['origin'][0], -cube['size'][0]/2)
            self.assertEqual(cube['origin'][1], 0)
            self.assertEqual(set(cube['uv']), {'north'})
        self.assertLess(video['origin'][2], black['origin'][2])

    def test_all_275_frames_have_extruded_edges_including_final_frame(self):
        p=AKKI
        for atlas_index in range(3):
            with Image.open(RP/f'textures/entity/{p.stem}/atlas_{atlas_index:03d}.png') as atlas:
                self.assertEqual(atlas.mode,'RGB');self.assertEqual(atlas.size,(660,380))
                for slot in range(min(100,275-atlas_index*100)):
                    x,y=(slot%10)*66+1,(slot//10)*38+1
                    for a,b in [((x-1,y,x,y+36),(x,y,x+1,y+36)),
                                ((x+64,y,x+65,y+36),(x+63,y,x+64,y+36)),
                                ((x,y-1,x+64,y),(x,y,x+64,y+1)),
                                ((x,y+36,x+64,y+37),(x,y+35,x+64,y+36))]:
                        self.assertIsNone(ImageChops.difference(atlas.crop(a),atlas.crop(b)).getbbox())
                if atlas_index==2:
                    self.assertIsNotNone(atlas.crop((265,267,329,303)).getbbox()) # frame 274: row 7, col 4
        client=read(RP/f'entity/{p.stem}.entity.json')['minecraft:client_entity']['description']
        self.assertEqual(len(client['textures']),4)
        for texture in client['textures'].values():self.assertTrue((RP/(texture+'.png')).is_file())

    def test_manifest_audio_and_no_cross_pack_assets(self):
        manifest=read(RP/'manifest.json')
        self.assertEqual(manifest['header']['uuid'],'c2de9f3f-7956-4c7a-b6a1-63b264a9a059')
        self.assertEqual(manifest['modules'][0]['uuid'],'4e49c6a4-2f67-4e31-9c5c-5946ec3ec437')
        self.assertEqual(manifest['header']['version'],[1,0,0])
        self.assertIn(VIDEO_AKKI,DIRECT_RESOURCE_PACKS)
        sound=read(RP/'sounds/sound_definitions.json')['sound_definitions']['ichiyon.video_screen_akki.audio']
        self.assertEqual((sound['min_distance'],sound['max_distance']),(1,12))
        self.assertEqual(sound['sounds'][0], {'name':'sounds/video_screen_akki/audio','stream':False,'is3D':True})
        audio=probe(RP/'sounds/video_screen_akki/audio.ogg')['streams'][0]
        self.assertEqual((audio['codec_name'],audio['channels'],audio['sample_rate']),('vorbis',1,'44100'))
        for name in ('ichiyon_avatar_rp','ichiyon_video_rp','ichiyon_video_big_rp'):
            self.assertFalse(list((ROOT/'minecraft/resource_packs'/name).rglob('*akki*')))

    def test_small_big_definitions_are_unchanged_by_profile_refactor(self):
        for profile in (SMALL,BIG_PROFILES['big-128']):
            report=read(ROOT/'minecraft'/profile.stem/'build_report.json')['output']
            media={k:report[k] for k in ('fps','frameCount','duration','sound')}
            with tempfile.TemporaryDirectory() as tmp:
                write_definitions(Path(tmp),media,report['atlasCount'],profile)
                for p in Path(tmp).rglob('*'):
                    if p.is_file():
                        self.assertEqual(p.read_bytes().replace(b'\r\n',b'\n'), (ROOT/p.relative_to(tmp)).read_bytes().replace(b'\r\n',b'\n'),str(p))

    def test_builder_with_and_without_audio(self):
        for sound in (False,True):
            with tempfile.TemporaryDirectory() as tmp:
                tmp=Path(tmp);source=tmp/'fixture.mp4';output=tmp/'generated'
                args=['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=96x54:rate=30:duration=0.55']
                if sound:args+=['-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=0.55','-shortest']
                run(args+['-c:v','libx264','-pix_fmt','yuv420p',str(source)])
                report=build(source,output,AKKI);out=output/'minecraft/resource_packs'/AKKI.rp_name
                self.assertEqual(report['source']['hasAudio'],sound)
                self.assertEqual(report['output']['fps'],20)
                self.assertEqual((out/'sounds/video_screen_akki/audio.ogg').exists(),sound)
                self.assertEqual(bool(read(out/'sounds/sound_definitions.json')['sound_definitions']),sound)

    def test_managed_archive_includes_pack_limits_and_apply_world_refs(self):
        root=ROOT/'minecraft';data=pack_zip(root,builtin_assets(root),1)
        self.assertLess(len(data),MAX_ARCHIVE)
        with ZipFile(BytesIO(data)) as z:
            self.assertLess(sum(i.file_size for i in z.infolist()),MAX_EXPANDED)
            self.assertLess(max(i.file_size for i in z.infolist()),MAX_FILE)
            self.assertLess(len(z.infolist()),MAX_FILES)
            proof=json.loads(z.read('cosmetics-build.json'))
            self.assertIn(VIDEO_AKKI.rstrip('/'),proof['packs'])
            for p in RP.rglob('*'):
                if p.is_file():self.assertEqual(z.read(VIDEO_AKKI+p.relative_to(RP).as_posix()),p.read_bytes())
        with tempfile.TemporaryDirectory() as tmp:
            tmp=Path(tmp);live=tmp/'live';live.mkdir()
            unpack(data,tmp/'stage',live)
            self.assertEqual(read(tmp/'stage'/VIDEO_AKKI/'manifest.json')['header']['uuid'],'c2de9f3f-7956-4c7a-b6a1-63b264a9a059')

    def test_original_rebuild_when_source_is_available(self):
        if SOURCE is None:self.skipTest('external source is not committed; provide --source for full rebuild')
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(),'bab5cb8f68e0ed19de85efa8379804cd23a69648baf451b2929d26277d106835')
        with tempfile.TemporaryDirectory() as tmp:
            build(SOURCE,Path(tmp),AKKI)
            generated=Path(tmp)/'minecraft/resource_packs'/AKKI.rp_name
            for p in generated.rglob('*'):
                if p.is_file():
                    # Independent decode/encode verifies the final frame too.
                    self.assertEqual(p.read_bytes().replace(b'\r\n',b'\n') if p.suffix=='.json' else p.read_bytes(),
                                     (RP/p.relative_to(generated)).read_bytes().replace(b'\r\n',b'\n') if p.suffix=='.json' else (RP/p.relative_to(generated)).read_bytes())


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path);args,rest=parser.parse_known_args();SOURCE=args.source
    unittest.main(argv=[sys.argv[0],*rest])
