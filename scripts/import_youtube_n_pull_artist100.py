from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from bot.db import get_connection
from bot.repositories.youtube_n_pull import YouTubeNPullRepository, normalize_command_name
from bot.services.youtube_n_pull import refresh_cache_if_needed
from scripts.discover_youtube_n_pull_artist_sources import DATASET_PATH, load_dataset, validate_dataset


DEFAULT_CACHE_TTL_SECONDS = 86400
DEFAULT_MAX_PULLS = 100
MIN_REFRESH_DELAY_SECONDS = 5
READY_STATUS = "ready"
HIGH_CONFIDENCE = "high"
STOP_REFRESH_ERROR_TERMS = ("429", "cookie", "cookies", "sign in", "bot", "unauthorized", "forbidden")


@dataclass
class ImportAction:
    action: str
    artist: Dict[str, Any]
    candidate: Optional[Dict[str, Any]]
    values: Dict[str, Any]
    sources: List[Dict[str, Any]]
    reason: str = ""
    preset_id: Optional[int] = None


def load_candidate_file(path: Optional[Path]) -> Dict[int, Dict[str, Any]]:
    if not path:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("results") if isinstance(payload, dict) else payload
    return {int(row["rank"]): row for row in rows or [] if row.get("rank") is not None}


def build_preset_values(artist: Dict[str, Any], enabled: bool = False) -> Dict[str, Any]:
    aliases = [name for name in artist.get("search_names") or [] if name and name != artist.get("command_name")]
    return {
        "display_name": artist["display_name"],
        "command_name": artist["command_name"],
        "aliases": "\n".join(dict.fromkeys(aliases)),
        "category": artist.get("category") or "Billboard 2026上半期",
        "enabled": bool(enabled),
        "max_pulls": DEFAULT_MAX_PULLS,
        "cache_ttl_seconds": DEFAULT_CACHE_TTL_SECONDS,
        "include_shorts": False,
        "include_live": False,
        "include_archived_live": False,
        "min_duration_seconds": None,
        "max_duration_seconds": None,
        "include_title_terms": "",
        "exclude_title_terms": "",
    }


def source_from_candidate(candidate: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not candidate:
        return []
    if candidate.get("status") != READY_STATUS or candidate.get("confidence") != HIGH_CONFIDENCE:
        return []
    source_url = str(candidate.get("selected_source_url") or "").strip()
    if not source_url:
        return []
    return [
        {
            "source_type": "channel",
            "source_url": source_url,
            "priority": 100,
            "enabled": True,
        }
    ]


def existing_command_keys(presets: Sequence[Dict[str, Any]]) -> set:
    keys = set()
    for preset in presets:
        keys.add(str(preset.get("command_key") or normalize_command_name(preset.get("command_name") or "")))
        for alias in str(preset.get("aliases") or "").splitlines():
            if alias.strip():
                keys.add(normalize_command_name(alias))
    return keys


def build_import_plan(
    dataset: Dict[str, Any],
    candidate_rows: Dict[int, Dict[str, Any]],
    existing_presets: Sequence[Dict[str, Any]],
    include_unresolved: bool = True,
) -> List[ImportAction]:
    keys = existing_command_keys(existing_presets)
    plan: List[ImportAction] = []
    for artist in dataset.get("artists") or []:
        command_key = normalize_command_name(artist.get("command_name") or "")
        values = build_preset_values(artist, enabled=False)
        candidate = candidate_rows.get(int(artist["rank"]))
        sources = source_from_candidate(candidate)
        if command_key in keys:
            plan.append(ImportAction("skip_existing", artist, candidate, values, [], "existing command or alias"))
            continue
        if not include_unresolved and not sources:
            plan.append(ImportAction("skip_unresolved", artist, candidate, values, [], "no high-confidence source"))
            continue
        plan.append(ImportAction("create", artist, candidate, values, sources))
    return plan


def summarize_plan(plan: Sequence[ImportAction]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for action in plan:
        key = action.action
        if action.action == "create" and action.sources:
            key = "create_with_source"
        elif action.action == "create":
            key = "create_without_source"
        counts[key] = counts.get(key, 0) + 1
    return counts


def print_plan(plan: Sequence[ImportAction]) -> None:
    print("plan summary: {0}".format(summarize_plan(plan)))
    for action in plan:
        candidate_status = (action.candidate or {}).get("status") or "no_candidate"
        print(
            "{0}: rank={1} name={2} candidate={3} sources={4}{5}".format(
                action.action,
                action.artist.get("rank"),
                action.artist.get("display_name"),
                candidate_status,
                len(action.sources),
                " reason={0}".format(action.reason) if action.reason else "",
            )
        )


def apply_import_plan(repository: YouTubeNPullRepository, guild_id: str, plan: Sequence[ImportAction]) -> List[ImportAction]:
    applied: List[ImportAction] = []
    for action in plan:
        if action.action != "create":
            continue
        preset = repository.create_preset(guild_id, action.values)
        action.preset_id = int(preset["id"])
        if action.sources:
            repository.replace_sources(action.preset_id, action.sources)
        applied.append(action)
    return applied


def _refresh_should_stop(error_text: str) -> bool:
    normalized = str(error_text or "").casefold()
    return any(term in normalized for term in STOP_REFRESH_ERROR_TERMS)


async def refresh_imported_presets(
    repository: YouTubeNPullRepository,
    guild_id: str,
    actions: Sequence[ImportAction],
    delay_seconds: int,
    enable_successful: bool = False,
) -> Dict[str, int]:
    delay = max(MIN_REFRESH_DELAY_SECONDS, int(delay_seconds))
    counts = {"success": 0, "failed": 0, "skipped": 0}
    for index, action in enumerate(actions):
        if not action.preset_id or not action.sources:
            counts["skipped"] += 1
            continue
        preset = repository.get_preset(guild_id, action.preset_id)
        if not preset:
            counts["failed"] += 1
            continue
        try:
            await refresh_cache_if_needed(repository, guild_id, preset)
            refreshed = repository.get_preset(guild_id, action.preset_id) or preset
            error = str(refreshed.get("last_cache_error") or "")
            if error:
                counts["failed"] += 1
                if _refresh_should_stop(error):
                    break
                continue
            counts["success"] += 1
            if enable_successful:
                values = dict(action.values)
                values["enabled"] = True
                repository.update_preset(guild_id, action.preset_id, values)
        except Exception as exc:
            counts["failed"] += 1
            if _refresh_should_stop(type(exc).__name__):
                break
        if index < len(actions) - 1:
            time.sleep(delay)
    return counts


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import Billboard JAPAN Artist 100 YouTube N-pull presets.")
    parser.add_argument("--bot-id", required=True)
    parser.add_argument("--guild-id", required=True)
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument("--candidate-file", type=Path)
    parser.add_argument("--apply", action="store_true", help="Write to DB. Without this flag, only prints a dry-run plan.")
    parser.add_argument("--skip-unresolved", action="store_true", help="Do not create OFF presets when no high-confidence source exists.")
    parser.add_argument("--refresh", action="store_true", help="Refresh cache for newly imported high-confidence presets after apply.")
    parser.add_argument("--enable-successful", action="store_true", help="Enable only presets whose refresh succeeded.")
    parser.add_argument("--delay-seconds", type=int, default=MIN_REFRESH_DELAY_SECONDS)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    dataset = load_dataset(args.dataset)
    errors = validate_dataset(dataset)
    if errors:
        for error in errors:
            print("[NG] {0}".format(error))
        return 1
    candidates = load_candidate_file(args.candidate_file)
    with get_connection() as connection:
        repository = YouTubeNPullRepository(connection, bot_id=args.bot_id)
        existing = repository.list_presets(args.guild_id)
        plan = build_import_plan(dataset, candidates, existing, include_unresolved=not args.skip_unresolved)
        print_plan(plan)
        if not args.apply:
            print("dry-run: no DB writes. Add --apply to import.")
            return 0
        try:
            applied = apply_import_plan(repository, args.guild_id, plan)
            refresh_counts: Optional[Dict[str, int]] = None
            if args.refresh:
                refresh_counts = asyncio.run(
                    refresh_imported_presets(
                        repository,
                        args.guild_id,
                        applied,
                        delay_seconds=args.delay_seconds,
                        enable_successful=args.enable_successful,
                    )
                )
            connection.commit()
            print("applied: {0}".format(len(applied)))
            if refresh_counts is not None:
                print("refresh: {0}".format(refresh_counts))
            return 0
        except Exception:
            connection.rollback()
            raise


if __name__ == "__main__":
    raise SystemExit(main())
