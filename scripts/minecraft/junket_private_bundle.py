"""Private converted-venue packaging shared by isolated JUNKET game trials.

The caller explicitly selects structures, entities and RP files. No directory
copy can accidentally include another game's source artwork or public pack.
These helpers never install packs, edit a world or grant redistribution rights.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import uuid
from zipfile import ZipFile

from build_junket_trial import write_entry


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def selected_files(root: Path, names):
    """Read only explicit, contained POSIX paths; reject duplicate selections."""
    root = root.resolve()
    result = {}
    for name in names:
        relative = PurePosixPath(name)
        if not name or "\\" in name or relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Unsafe selected asset path: {name}")
        if name in result:
            raise ValueError(f"Duplicate selected asset: {name}")
        path = root.joinpath(*relative.parts).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"Selected asset escapes conversion directory: {name}")
        result[name] = path.read_bytes()
    return result


def select_conversion(assets: Path, source: Path, *, structures, entities, resources):
    """Bind an explicit asset subset to the original ZIP and conversion report.

    structures maps a converted structure name to its original source bounds.
    entities and resources are allowlists, not globs. Adaptations remain visible
    in the source converter's report instead of silently recreating the hall.
    """
    report_path = assets / "conversion-report.json"
    report_bytes = report_path.read_bytes()
    conversion = json.loads(report_bytes)
    source_hash = sha256(source.read_bytes())
    if source_hash != conversion["source_sha256"]:
        raise ValueError("The original ZIP does not match the existing conversion provenance")
    for name, bounds in structures.items():
        if conversion["structures"][name]["source_bounds"] != bounds:
            raise ValueError(f"Existing conversion is not the requested original venue: {name}")
    bp_names = [f"structures/junket/{name}.mcstructure" for name in structures]
    bp_names += [f"entities/{name}.json" for name in entities]
    bp = selected_files(assets / "ichiyon_junket_assets_bp", bp_names)
    rp = selected_files(assets / "ichiyon_junket_assets_rp", resources)
    evidence = {
        "source_archive": source.name,
        "source_sha256": source_hash,
        "existing_conversion_report_sha256": sha256(report_bytes),
        "selected_source_structures": {name: conversion["structures"][name] for name in structures},
        "original_asset_sha256": {f"bp/{name}": sha256(data) for name, data in bp.items()}
        | {f"rp/{name}": sha256(data) for name, data in rp.items()},
    }
    return bp, rp, evidence


def private_manifests(current, *, entry, version, label):
    """Create independent IDs and retain only Script API module dependencies."""
    engine = current["header"]["min_engine_version"]
    description = "Private original venue conversion. DO NOT REDISTRIBUTE."
    rp_id, bp_id = str(uuid.uuid4()), str(uuid.uuid4())
    rp = {"format_version": 2, "header": {
        "name": f"{label} Models (PRIVATE)", "description": description,
        "uuid": rp_id, "version": version, "min_engine_version": engine},
        "modules": [{"type": "resources", "uuid": str(uuid.uuid4()), "version": version}],
        "capabilities": ["pbr"]}
    bp = {"format_version": 2, "header": {
        "name": f"{label} Trial (PRIVATE)", "description": description,
        "uuid": bp_id, "version": version, "min_engine_version": engine},
        "modules": [{"type": "data", "uuid": str(uuid.uuid4()), "version": version},
                    {"type": "script", "language": "javascript", "entry": entry,
                     "uuid": str(uuid.uuid4()), "version": version}],
        "dependencies": [dict(dependency) for dependency in current.get("dependencies", [])
                         if "module_name" in dependency] + [{"uuid": rp_id, "version": version}]}
    return bp, rp


def write_zip(path, files):
    """Create a new archive exclusively and return its integrity evidence."""
    with ZipFile(path, "x") as archive:
        for name, data in sorted(files.items()):
            write_entry(archive, name, data)
    with ZipFile(path) as archive:
        failure = archive.testzip()
        if failure is not None:
            raise ValueError(f"Invalid ZIP entry in {path.name}: {failure}")
    return {"file": path.name, "bytes": path.stat().st_size,
            "sha256": sha256(path.read_bytes()), "zip_crc": "PASS"}
