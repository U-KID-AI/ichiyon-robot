"""Synthetic ZIP fixture only; no redistributable JUNKET source is needed in CI."""
from pathlib import Path
import sys
import tempfile
import unittest
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts/minecraft'))
from analyze_junket import analyze, digest


class AnalysisChecks(unittest.TestCase):
    def fixture(self, path, *, drift=False):
        prefix = 'fixture/datapacks/test/data/'
        with ZipFile(path, 'w') as archive:
            archive.writestr('fixture/level.dat', b'not parsed without --world-details')
            archive.writestr(prefix + 'lobby/function/system/tp.mcfunction',
                             'execute if score $game lobby matches 1 run tp @s 1 2 3\n'
                             f'execute if score $game lobby matches 101 run tp @s 1 2 {4 if drift else 3}\n')
            archive.writestr(prefix + 'lobby/function/building/all.mcfunction',
                             'summon text_display 4 5 -6 {text:"CROW HOTEL"}\n'
                             'summon villager 7 8 -9 {Tags:["lobby-multi"]}\n')
            archive.writestr(prefix + 'game/function/sparrow/start.mcfunction',
                             'tp @p[tag=sparrow-player-1] 10 11 12\n'
                             'tp @p[tag=sparrow-player-2] 13 14 15\n'
                             'function game:sparrow/fixture\n# PRIVATE_SOURCE_MARKER\n')

    def test_inventory_anchors_no_source_extraction_or_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'fixture.zip'
            self.fixture(path)
            before, modified = digest(path), path.stat().st_mtime_ns
            report = analyze(path)
            self.assertEqual(report['mcfunctions'], 3)
            self.assertEqual(report['venues']['sparrow']['arrival'], [1, 2, 3])
            self.assertEqual(report['venues']['sparrow']['seats'], [[10, 11, 12], [13, 14, 15]])
            self.assertEqual(report['venues']['crow_hotel']['signs'], [[4., 5., -6.]])
            self.assertNotIn('PRIVATE_SOURCE_MARKER', str(report))
            self.assertEqual(report['sparrow']['function_dependencies'], ['game:sparrow/fixture'])
            self.assertEqual(report['source']['sha256'], before)
            self.assertEqual(digest(path), before)
            self.assertEqual(path.stat().st_mtime_ns, modified)
            self.assertEqual(list(Path(folder).iterdir()), [path])

    def test_changed_single_multi_anchor_is_reported_not_guessed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'fixture.zip'
            self.fixture(path, drift=True)
            with self.assertRaisesRegex(ValueError, 'anchors changed'):
                analyze(path)


if __name__ == '__main__':
    unittest.main()
