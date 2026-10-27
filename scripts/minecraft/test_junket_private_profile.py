"""Public synthetic fixtures for private source-configuration boundaries."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from junket_private_profile import private_value


class PrivateProfileTests(unittest.TestCase):
    def test_missing_profile_fails_closed_without_searching_personal_paths(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(ValueError, "JUNKET_PRIVATE_PROFILE"):
            private_value("fixture.bounds")

    def test_synthetic_profile_is_data_only_and_each_read_is_independent(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "profile.json"
            path.write_text(json.dumps({"schema": 1, "values": {
                "fixture.bounds": [[0, 64, 0], [2, 66, 2]],
                "fixture.label": "__import__('os').system('must not execute')",
            }}), encoding="utf-8")
            with patch.dict(os.environ, {"JUNKET_PRIVATE_PROFILE": str(path)}):
                value = private_value("fixture.bounds")
                value[0][0] = 999
                self.assertEqual(private_value("fixture.bounds")[0][0], 0)
                self.assertIn("must not execute", private_value("fixture.label"))
                with self.assertRaisesRegex(ValueError, "Missing private source profile key"):
                    private_value("absent")

    def test_bank_converter_uses_explicit_synthetic_bounds_without_private_input(self):
        from build_junket_bank_source import inside, needed_chunks
        box = [[0, 64, 0], [17, 66, 17]]
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(inside([1, 65, 1], box))
            self.assertFalse(inside([18, 65, 1], box))
            self.assertEqual(needed_chunks(box), {(0, 0), (0, 1), (1, 0), (1, 1)})
            with self.assertRaisesRegex(ValueError, "JUNKET_PRIVATE_PROFILE"):
                needed_chunks()

    def test_private_runtime_input_is_required_before_bundle_directory_creation(self):
        from build_junket_all17_bundle import build
        with TemporaryDirectory() as temporary:
            output = Path(temporary) / "must-not-be-created"
            with self.assertRaisesRegex(ValueError, "runtime-manifest"):
                build(Path("unused"), Path("unused"), Path("unused"), output)
            self.assertFalse(output.exists())

    def test_private_runtime_rejects_layout_mismatch_before_generation(self):
        from build_junket_all17_bundle import read_private_runtime
        from build_junket_warp import EXPECTED_IDS
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            games = [{"id": gid, "production_origin": [0, 64, 0], "size": [4, 4, 4],
                      "destination": [1, 65, 1]} for gid in EXPECTED_IDS]
            venues = [{"id": g["id"], "origin": g["production_origin"], "size": g["size"],
                       "destination": dict(zip("xyz", g["destination"])),
                       "distribution": "private-local-only", "installation": "native-preinstalled"}
                      for g in games]
            layout = root / "layouts.json"
            runtime = root / "runtime.json"
            layout.write_text(json.dumps({"games": games}), encoding="utf-8")
            runtime.write_text(json.dumps({"venues": venues}), encoding="utf-8")
            self.assertEqual(len(read_private_runtime(runtime, layout)), 17)
            venues[0]["destination"]["x"] = 3
            runtime.write_text(json.dumps({"venues": venues}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "arrival differs"):
                read_private_runtime(runtime, layout)


if __name__ == "__main__":
    unittest.main()
