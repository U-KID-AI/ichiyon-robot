"""Read separately retained source coordinates without publishing them in code.

Set JUNKET_PRIVATE_PROFILE to a local JSON file with schema=1 and a ``values``
object. Keys are documented at each call site. This module never downloads,
executes or writes source material; missing inputs fail explicitly.
"""
from copy import deepcopy
import json
import os
from pathlib import Path


def private_value(key):
    path = os.environ.get("JUNKET_PRIVATE_PROFILE")
    if not path:
        raise ValueError("Set JUNKET_PRIVATE_PROFILE to the separately retained private source profile")
    document = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if document.get("schema") != 1 or not isinstance(document.get("values"), dict):
        raise ValueError("Unsupported JUNKET private source profile")
    if key not in document["values"]:
        raise ValueError("Missing private source profile key: " + key)
    return deepcopy(document["values"][key])
