"""Offline regression tests for pack version policy."""
import copy
import unittest
from unittest.mock import patch
from check_resource_pack_versions import validate, check


class VersionChecks(unittest.TestCase):
    def test_policy(self):
        old = {"header": {"uuid": "same", "version": [1, 0, 30]},
               "modules": [{"version": [1, 0, 30]}]}
        for value in ([1, 0, 30], [1, 0, 29], [True, 0, 31], [1, 0]):
            new = copy.deepcopy(old)
            new["header"]["version"] = value
            with self.assertRaises(ValueError):
                validate(old, new)
        new = copy.deepcopy(old)
        new["header"]["version"] = [1, 0, 31]
        with self.assertRaises(ValueError):
            validate(old, new)
        new["modules"][0]["version"] = [1, 0, 31]
        validate(old, new)
        validate(None, new)

    def test_asset_add_edit_delete_and_rename_require_bump(self):
        manifest = b'{"header":{"uuid":"same","version":[1,0,30]},"modules":[{"version":[1,0,30]}]}'
        prefix = b"minecraft/resource_packs/test/"
        def tree(files):
            return b"".join(b"100644 blob " + sha + b"\t" + prefix + path + b"\0" for path, sha in files)
        base = [(b"manifest.json", b"m"), (b"entity.json", b"a")]
        for assets in ([(b"entity.json", b"b")], [], [(b"renamed.json", b"a")],
                       [(b"entity.json", b"a"), (b"new.png", b"c")]):
            with patch("check_resource_pack_versions.git", side_effect=[tree(base), tree([(b"manifest.json", b"m"), *assets]), manifest, manifest]):
                with self.assertRaises(ValueError):
                    check("a" * 40, "b" * 40)


if __name__ == "__main__":
    unittest.main()
