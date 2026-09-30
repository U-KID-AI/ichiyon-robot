"""Compare committed RP trees only; never loads environment or live packs."""
import argparse
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parent.parent
PREFIX = "minecraft/resource_packs/"


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def version(value, format_version=2):
    if format_version == 3:
        if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", value):
            raise ValueError("V3 pack version must be a release SemVer string")
        return tuple(map(int, value.split(".")))
    if format_version != 2:
        raise ValueError("unsupported manifest format")
    if (not isinstance(value, list) or len(value) != 3
            or any(type(v) is not int or v < 0 for v in value)):
        raise ValueError("invalid pack version")
    return tuple(value)


def validate(old, new):
    new_format = new.get("format_version", 2)
    current = version(new["header"]["version"], new_format)
    if not new.get("modules") or any(version(m["version"], new_format) != current for m in new["modules"]):
        raise ValueError("header/modules versions must match")
    if old is not None:
        if new["header"]["uuid"] != old["header"]["uuid"]:
            raise ValueError("existing pack UUID must be preserved")
        if current <= version(old["header"]["version"], old.get("format_version", 2)):
            raise ValueError("changed RP requires a higher manifest version")


def check(base, head):
    for ref in (base, head):
        if not re.fullmatch(r"[0-9a-f]{40}", ref):
            raise ValueError("comparison requires full commit SHAs")
    def tree(ref):
        return {row.split(b"\t", 1)[1].decode(): row.split(b"\t", 1)[0]
                for row in git("ls-tree", "-r", "-z", ref, "--", PREFIX).split(b"\0") if row}
    before, after = tree(base), tree(head)
    changed = {p.split("/")[2] for p in before.keys() | after.keys()
               if before.get(p) != after.get(p)}
    for pack in sorted(changed):
        prefix = PREFIX + pack + "/"
        if not any(p.startswith(prefix) for p in after):
            continue  # Entire pack removed; no remaining asset to cache.
        path = prefix + "manifest.json"
        old = json.loads(git("show", base + ":" + path)) if path in before else None
        new = json.loads(git("show", head + ":" + path))
        validate(old, new)
    print("Resource pack version check passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base")
    parser.add_argument("head")
    args = parser.parse_args()
    check(args.base, args.head)
