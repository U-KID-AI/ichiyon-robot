from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


DATASET_PATH = ROOT_DIR / "data" / "youtube_n_pull_artist100_2026_h1.json"
DEFAULT_OUTPUT_JSON = ROOT_DIR / "artifacts" / "youtube_n_pull_artist100_candidates.json"
DEFAULT_OUTPUT_CSV = ROOT_DIR / "artifacts" / "youtube_n_pull_artist100_candidates.csv"
DEFAULT_SEARCH_LIMIT = 15
HIGH_CONFIDENCE_SCORE = 90
MEDIUM_CONFIDENCE_SCORE = 70
AMBIGUOUS_MARGIN = 15
QUERY_PATTERNS = (
    "{name} Topic",
    "{name} official audio",
    "{name} official music video",
)
EXISTING_PRESET_NAMES = {"しゃろう", "油粘土マン", "ペルソナ5"}
REJECT_CHANNEL_TERMS = (
    "fan",
    "fanpage",
    "unofficial",
    "archive",
    "reaction",
    "reacts",
    "cover",
    "karaoke",
    "lyrics",
    "lyric",
    "翻訳",
    "切り抜き",
    "まとめ",
    "非公式",
)
LABEL_CHANNEL_TERMS = (
    "records",
    "record",
    "label",
    "music japan",
    "tv",
    "テレビ",
    "news",
    "ニュース",
    "ranking",
    "ランキング",
    "playlist",
    "hits",
)


try:
    import yt_dlp
except ImportError:  # pragma: no cover - local dependency is checked by runtime scripts.
    yt_dlp = None


def normalize_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    normalized = normalized.casefold()
    normalized = re.sub(r"[\s\u3000]+", " ", normalized).strip()
    return normalized


def compact_text(value: Any) -> str:
    return re.sub(r"[^0-9a-zぁ-んァ-ン一-龥ー&]+", "", normalize_text(value))


def load_dataset(path: Path = DATASET_PATH) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_dataset(dataset: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    artists = list(dataset.get("artists") or [])
    ranks = [int(artist.get("rank")) for artist in artists]
    display_names = [str(artist.get("display_name") or "") for artist in artists]
    command_names = [str(artist.get("command_name") or "") for artist in artists]
    if len(artists) != 99:
        errors.append("artist count must be 99")
    if 30 in ranks:
        errors.append("rank 30 must be excluded")
    if sorted(ranks) != [rank for rank in range(1, 101) if rank != 30]:
        errors.append("ranks must be 1-100 with only rank 30 missing")
    if len(set(ranks)) != len(ranks):
        errors.append("rank values must be unique")
    if len(set(display_names)) != len(display_names):
        errors.append("display_name values must be unique")
    if len(set(normalize_text(name) for name in command_names)) != len(command_names):
        errors.append("command_name values must be unique after normalization")
    forbidden = EXISTING_PRESET_NAMES.intersection(command_names).union(EXISTING_PRESET_NAMES.intersection(display_names))
    if forbidden:
        errors.append("existing preset names must not be included: {0}".format(", ".join(sorted(forbidden))))
    for artist in artists:
        if not artist.get("display_name") or not artist.get("command_name"):
            errors.append("rank {0} is missing display_name or command_name".format(artist.get("rank")))
        search_names = artist.get("search_names") or []
        if not isinstance(search_names, list) or not search_names:
            errors.append("rank {0} must have search_names".format(artist.get("rank")))
    return errors


def build_search_queries(artist: Dict[str, Any]) -> List[str]:
    names = [str(name).strip() for name in artist.get("search_names") or [] if str(name).strip()]
    seen = set()
    queries: List[str] = []
    for name in names:
        for pattern in QUERY_PATTERNS:
            query = pattern.format(name=name)
            key = normalize_text(query)
            if key not in seen:
                seen.add(key)
                queries.append(query)
    return queries


def build_search_ytdl_options(search_limit: int = DEFAULT_SEARCH_LIMIT) -> Dict[str, Any]:
    return {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "ignoreerrors": True,
        "playlistend": int(search_limit),
    }


def canonical_channel_url(channel_id: str) -> str:
    return "https://www.youtube.com/channel/{0}/videos".format(channel_id)


def extract_channel_candidate(entry: Dict[str, Any], query: str) -> Optional[Dict[str, Any]]:
    if not isinstance(entry, dict):
        return None
    channel_id = str(entry.get("channel_id") or entry.get("uploader_id") or "").strip()
    channel = str(entry.get("channel") or entry.get("uploader") or "").strip()
    channel_url = str(entry.get("channel_url") or entry.get("uploader_url") or "").strip()
    if not channel_id and "/channel/" in channel_url:
        channel_id = channel_url.rstrip("/").split("/channel/", 1)[1].split("/", 1)[0]
    if not channel_id or not channel:
        return None
    if not re.fullmatch(r"UC[A-Za-z0-9_-]{20,}", channel_id):
        return None
    return {
        "channel_id": channel_id,
        "channel_url": channel_url or canonical_channel_url(channel_id),
        "source_url": canonical_channel_url(channel_id),
        "channel": channel,
        "uploader": str(entry.get("uploader") or channel),
        "uploader_id": str(entry.get("uploader_id") or channel_id),
        "uploader_url": str(entry.get("uploader_url") or channel_url or ""),
        "channel_is_verified": bool(entry.get("channel_is_verified") or entry.get("uploader_is_verified")),
        "queries": [query],
        "hit_count": 1,
        "sample_titles": [str(entry.get("title") or "").strip()][:1],
    }


def _artist_name_variants(artist: Dict[str, Any]) -> List[str]:
    values = [artist.get("display_name"), artist.get("command_name")]
    values.extend(artist.get("search_names") or [])
    variants = []
    seen = set()
    for value in values:
        normalized = normalize_text(value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            variants.append(normalized)
    return variants


def _artist_compact_variants(artist: Dict[str, Any]) -> List[str]:
    values = []
    for value in _artist_name_variants(artist):
        compact = compact_text(value)
        if compact and compact not in values:
            values.append(compact)
    return values


def _is_exact_topic(channel_norm: str, artist: Dict[str, Any]) -> bool:
    return any(channel_norm == "{0} - topic".format(name) for name in _artist_name_variants(artist))


def _is_exact_artist(channel_norm: str, artist: Dict[str, Any]) -> bool:
    return channel_norm in _artist_name_variants(artist)


def _artist_in_channel(channel_norm: str, artist: Dict[str, Any]) -> bool:
    channel_compact = compact_text(channel_norm)
    return any(name and name in channel_compact for name in _artist_compact_variants(artist))


def rejected_reason(artist: Dict[str, Any], candidate: Dict[str, Any]) -> str:
    channel_norm = normalize_text(candidate.get("channel"))
    if _is_exact_topic(channel_norm, artist) or _is_exact_artist(channel_norm, artist):
        return ""
    for term in REJECT_CHANNEL_TERMS:
        if normalize_text(term) in channel_norm:
            return "excluded_channel_term:{0}".format(term)
    if any(normalize_text(term) in channel_norm for term in LABEL_CHANNEL_TERMS) and not _artist_in_channel(channel_norm, artist):
        return "label_or_multi_artist_channel"
    return ""


def score_candidate(artist: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    channel_norm = normalize_text(candidate.get("channel"))
    exact_topic = _is_exact_topic(channel_norm, artist)
    exact_artist = _is_exact_artist(channel_norm, artist)
    artist_name_match = _artist_in_channel(channel_norm, artist)
    official_name_match = artist_name_match and any(term in channel_norm for term in ("official", "vevo", "公式"))
    verified = bool(candidate.get("channel_is_verified"))
    repeated_hits = int(candidate.get("hit_count") or 0)
    reason = rejected_reason(artist, candidate)
    score = 0
    if exact_topic:
        score += 100
    elif exact_artist:
        score += 88
    elif official_name_match:
        score += 80
    elif artist_name_match:
        score += 55
    if verified:
        score += 8
    score += min(12, max(0, repeated_hits - 1) * 3)
    if "topic" in channel_norm and artist_name_match:
        score += 5
    if "vevo" in channel_norm and artist_name_match:
        score += 4
    if reason:
        score = 0
        confidence = "rejected"
    elif score >= HIGH_CONFIDENCE_SCORE:
        confidence = "high"
    elif score >= MEDIUM_CONFIDENCE_SCORE:
        confidence = "medium"
    else:
        confidence = "low"
    return {
        **candidate,
        "exact_topic": exact_topic,
        "exact_artist": exact_artist,
        "official_name_match": official_name_match,
        "verified": verified,
        "repeated_hits": repeated_hits,
        "rejected_reason": reason,
        "score": score,
        "confidence": confidence,
    }


def merge_candidates(candidates: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for candidate in candidates:
        key = str(candidate.get("source_url") or candidate.get("channel_id") or "")
        if not key:
            continue
        if key not in merged:
            merged[key] = dict(candidate)
            merged[key]["queries"] = list(candidate.get("queries") or [])
            merged[key]["sample_titles"] = list(candidate.get("sample_titles") or [])
            continue
        current = merged[key]
        current["hit_count"] = int(current.get("hit_count") or 0) + int(candidate.get("hit_count") or 1)
        for query in candidate.get("queries") or []:
            if query not in current["queries"]:
                current["queries"].append(query)
        for title in candidate.get("sample_titles") or []:
            if title and title not in current["sample_titles"]:
                current["sample_titles"].append(title)
    return list(merged.values())


def select_candidate(artist: Dict[str, Any], candidates: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    scored = [score_candidate(artist, candidate) for candidate in candidates]
    scored.sort(key=lambda item: (item["score"], item.get("hit_count") or 0, bool(item.get("exact_topic"))), reverse=True)
    if not scored:
        return {"status": "not_found", "selected": None, "candidates": []}
    best = scored[0]
    viable = [item for item in scored if item.get("confidence") in ("high", "medium")]
    second = viable[1] if len(viable) > 1 else None
    margin = int(best.get("score") or 0) - int(second.get("score") or 0) if second else int(best.get("score") or 0)
    if best.get("confidence") == "high" and (second is None or margin >= AMBIGUOUS_MARGIN):
        status = "ready"
    elif best.get("confidence") == "high" and best.get("exact_topic") and not second.get("exact_topic"):
        status = "ready"
    elif best.get("confidence") in ("high", "medium"):
        status = "ambiguous"
    else:
        status = "not_found"
    return {
        "status": status,
        "selected": best if status == "ready" else None,
        "candidates": scored,
        "top_score": best.get("score"),
        "second_score": second.get("score") if second else None,
        "margin": margin,
    }


def discover_artist(artist: Dict[str, Any], search_limit: int = DEFAULT_SEARCH_LIMIT, ydl_factory: Any = None) -> Dict[str, Any]:
    if ydl_factory is None:
        if yt_dlp is None:
            raise RuntimeError("yt-dlp is not installed")
        ydl_factory = yt_dlp.YoutubeDL
    queries = build_search_queries(artist)
    raw_candidates: List[Dict[str, Any]] = []
    with ydl_factory(build_search_ytdl_options(search_limit)) as ydl:
        for query in queries:
            info = ydl.extract_info("ytsearch{0}:{1}".format(search_limit, query), download=False) or {}
            for entry in info.get("entries") or []:
                candidate = extract_channel_candidate(entry or {}, query)
                if candidate:
                    raw_candidates.append(candidate)
    selected = select_candidate(artist, merge_candidates(raw_candidates))
    selected_source_url = (selected.get("selected") or {}).get("source_url") or ""
    return {
        "rank": artist.get("rank"),
        "display_name": artist.get("display_name"),
        "command_name": artist.get("command_name"),
        "status": selected.get("status"),
        "confidence": (selected.get("selected") or {}).get("confidence") or selected.get("status"),
        "selected_source_url": selected_source_url,
        "selected_channel": (selected.get("selected") or {}).get("channel") or "",
        "top_score": selected.get("top_score"),
        "second_score": selected.get("second_score"),
        "margin": selected.get("margin"),
        "queries": queries,
        "candidates": selected.get("candidates") or [],
    }


def load_resume_results(path: Optional[Path]) -> Dict[int, Dict[str, Any]]:
    if not path or not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("results") if isinstance(payload, dict) else payload
    return {int(row["rank"]): row for row in rows or [] if row.get("rank") is not None}


def write_reports(dataset: Dict[str, Any], results: List[Dict[str, Any]], json_path: Path, csv_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "dataset_id": dataset.get("dataset_id"),
        "source_url": dataset.get("source_url"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "rank",
                "display_name",
                "command_name",
                "status",
                "confidence",
                "selected_channel",
                "selected_source_url",
                "top_score",
                "second_score",
                "margin",
            ],
        )
        writer.writeheader()
        for row in results:
            writer.writerow({key: row.get(key, "") for key in writer.fieldnames})


def discover_all(
    dataset: Dict[str, Any],
    resume_path: Optional[Path] = None,
    search_limit: int = DEFAULT_SEARCH_LIMIT,
    ydl_factory: Any = None,
) -> List[Dict[str, Any]]:
    resume = load_resume_results(resume_path)
    results: List[Dict[str, Any]] = []
    for artist in dataset.get("artists") or []:
        rank = int(artist["rank"])
        if rank in resume:
            results.append(resume[rank])
            continue
        results.append(discover_artist(artist, search_limit=search_limit, ydl_factory=ydl_factory))
    results.sort(key=lambda row: int(row["rank"]))
    return results


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover YouTube channel candidates for Billboard JAPAN Artist 100 N-pull presets.")
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_JSON)
    parser.add_argument("--output-csv", type=Path, default=DEFAULT_OUTPUT_CSV)
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--search-limit", type=int, default=DEFAULT_SEARCH_LIMIT)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    dataset = load_dataset(args.dataset)
    errors = validate_dataset(dataset)
    if errors:
        for error in errors:
            print("[NG] {0}".format(error))
        return 1
    results = discover_all(dataset, resume_path=args.resume, search_limit=args.search_limit)
    write_reports(dataset, results, args.output_json, args.output_csv)
    counts = defaultdict(int)
    for row in results:
        counts[row.get("status") or "unknown"] += 1
    print("wrote {0}".format(args.output_json))
    print("wrote {0}".format(args.output_csv))
    print("summary: {0}".format(dict(sorted(counts.items()))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
