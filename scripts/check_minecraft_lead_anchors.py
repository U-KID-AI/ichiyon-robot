"""Offline lead locator checks; no settings, network, or world access."""

import json
import math
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent
RP = ROOT / "minecraft/resource_packs/ichiyon_avatar_rp"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


class LeadAnchorChecks(unittest.TestCase):
    def test_lead_locators_attach_to_body_surface(self):
        # Model-space points must be on actual body cubes, not collision bounds
        # or animated wheels/legs. Inherit body/root transforms through the bone.
        for name in ("molcar", "gonta"):
            with self.subTest(entity=name):
                client = read_json(RP / f"entity/{name}.entity.json")["minecraft:client_entity"]["description"]
                geometry = read_json(RP / f"models/entity/{name}.geo.json")["minecraft:geometry"][0]
                self.assertEqual(client["geometry"]["default"], geometry["description"]["identifier"])
                lead = client["locators"]["lead"]
                self.assertEqual(set(lead), {"body"})
                point = lead["body"]
                self.assertEqual(len(point), 3)
                self.assertTrue(all(isinstance(v, (int, float)) and math.isfinite(v) for v in point))
                body = next(b for b in geometry["bones"] if b["name"] == "body")
                def on_surface(cube):
                    low = cube["origin"]
                    high = [o + s for o, s in zip(low, cube["size"])]
                    return (all(lo <= p <= hi for p, lo, hi in zip(point, low, high))
                            and any(p in (lo, hi) for p, lo, hi in zip(point, low, high)))
                self.assertTrue(any(on_surface(c) for c in body["cubes"]))

    def test_all_pack_json_parses(self):
        for kind in ("behavior_packs", "resource_packs"):
            for path in (ROOT / "minecraft" / kind).rglob("*.json"):
                with self.subTest(path=path.relative_to(ROOT)):
                    read_json(path)


if __name__ == "__main__":
    unittest.main()
