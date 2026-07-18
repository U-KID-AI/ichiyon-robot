from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from bot.repositories.youtube_n_pull import normalize_command_name
from scripts import discover_youtube_n_pull_artist_sources as discover
from scripts import import_youtube_n_pull_artist100 as importer


def check(name: str, ok: bool, detail: str = "") -> bool:
    line = "[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else "")
    print(line.encode("cp932", errors="backslashreplace").decode("cp932"))
    return ok


class FakeRepository:
    def __init__(self, existing=None):
        self.presets = list(existing or [])
        self.created: List[Dict[str, Any]] = []
        self.sources: Dict[int, List[Dict[str, Any]]] = {}
        self.updated: List[Dict[str, Any]] = []
        self.next_id = 1000

    def list_presets(self, guild_id, enabled=None):
        return list(self.presets)

    def create_preset(self, guild_id, values):
        row = dict(values)
        row["id"] = self.next_id
        row["guild_id"] = guild_id
        row["command_key"] = normalize_command_name(values["command_name"])
        self.next_id += 1
        self.created.append(row)
        self.presets.append(row)
        return row

    def replace_sources(self, preset_id, sources):
        self.sources[preset_id] = list(sources)

    def get_preset(self, guild_id, preset_id):
        for preset in self.presets:
            if int(preset["id"]) == int(preset_id):
                return dict(preset)
        return None

    def update_preset(self, guild_id, preset_id, values):
        row = dict(values)
        row["id"] = preset_id
        self.updated.append(row)
        return row


def artist(name: str, rank: int = 1) -> Dict[str, Any]:
    return {
        "rank": rank,
        "display_name": name,
        "command_name": name,
        "search_names": [name],
        "category": "Billboard 2026上半期",
        "enabled_default": False,
    }


def candidate(channel: str, channel_id: str = "UCabcdefghijklmnopqrstuv", verified: bool = False, hits: int = 1) -> Dict[str, Any]:
    return {
        "channel_id": channel_id,
        "source_url": "https://www.youtube.com/channel/{0}/videos".format(channel_id),
        "channel_url": "https://www.youtube.com/channel/{0}".format(channel_id),
        "channel": channel,
        "uploader": channel,
        "uploader_id": channel_id,
        "uploader_url": "https://www.youtube.com/channel/{0}".format(channel_id),
        "channel_is_verified": verified,
        "queries": ["query"],
        "hit_count": hits,
        "sample_titles": ["sample"],
    }


def main() -> int:
    results: List[bool] = []
    dataset = discover.load_dataset()
    artists = dataset["artists"]
    ranks = [row["rank"] for row in artists]
    results.append(check("dataset has 99 artists", len(artists) == 99, str(len(artists))))
    results.append(check("rank 30 is excluded", 30 not in ranks and dataset["excluded_ranks"][0]["rank"] == 30))
    results.append(check("ranks are 1-100 except 30", sorted(ranks) == [rank for rank in range(1, 101) if rank != 30]))
    results.append(check("dataset validates cleanly", discover.validate_dataset(dataset) == [], str(discover.validate_dataset(dataset))))
    results.append(check("no duplicate display_name", len({row["display_name"] for row in artists}) == len(artists)))
    results.append(check("no duplicate command_name", len({normalize_command_name(row["command_name"]) for row in artists}) == len(artists)))
    results.append(check("existing presets are not included", not {"しゃろう", "油粘土マン", "ペルソナ5"}.intersection({row["command_name"] for row in artists})))
    results.append(check("rank 30 reason documents joint credit", "共同" in dataset["excluded_ranks"][0]["reason"]))

    options = discover.build_search_ytdl_options()
    results.append(check("discovery uses flat extraction", options.get("extract_flat") is True and options.get("skip_download") is True, str(options)))
    results.append(check("discovery does not request formats", "format" not in options and "noplaylist" not in options and "js_runtimes" not in options, str(options)))
    results.append(check("search query set includes Topic", any("Topic" in query for query in discover.build_search_queries(artist("Ado"))), str(discover.build_search_queries(artist("Ado")))))

    ado = artist("Ado")
    exact_topic = discover.score_candidate(ado, candidate("Ado - Topic", verified=True, hits=4))
    official = discover.score_candidate(ado, candidate("Ado Official", channel_id="UCofficialabcdefghijkl", verified=True, hits=3))
    fan = discover.score_candidate(ado, candidate("Ado fan archive", channel_id="UCfanabcdefghijklmnop"))
    label = discover.score_candidate(ado, candidate("Mega Records", channel_id="UClabelabcdefghijklmn", verified=True, hits=5))
    results.append(check("exact Topic is high confidence", exact_topic["confidence"] == "high" and exact_topic["exact_topic"], str(exact_topic)))
    results.append(check("official artist channel is viable", official["confidence"] in ("high", "medium") and official["official_name_match"], str(official)))
    results.append(check("fan/unofficial channel is rejected", fan["confidence"] == "rejected" and fan["rejected_reason"], str(fan)))
    results.append(check("label channel is not mistaken for artist", label["confidence"] == "rejected", str(label)))
    selected = discover.select_candidate(ado, [official, exact_topic])
    results.append(check("Topic is prioritized over official", selected["status"] == "ready" and selected["selected"]["channel"] == "Ado - Topic", str(selected)))
    ambiguous_artist = artist("TEST")
    amb = discover.select_candidate(
        ambiguous_artist,
        [
            candidate("TEST Official", channel_id="UCtestofficialabcdefgh", verified=True, hits=3),
            candidate("TEST VEVO", channel_id="UCtestvevoabcdefghijkl", verified=True, hits=3),
        ],
    )
    results.append(check("small margin becomes ambiguous", amb["status"] == "ambiguous", str(amb)))
    results.append(check("source URL normalizes to /videos", exact_topic["source_url"].endswith("/videos"), exact_topic["source_url"]))

    class FakeYDL:
        calls: List[str] = []

        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, query, download=False):
            self.calls.append(query)
            return {"entries": [candidate("Ado - Topic", verified=True, hits=1)]}

    discovered = discover.discover_artist(ado, ydl_factory=FakeYDL)
    results.append(check("discover returns ready high candidate", discovered["status"] == "ready" and discovered["selected_source_url"].endswith("/videos"), str(discovered)))

    with tempfile.TemporaryDirectory() as tmpdir:
        resume_path = Path(tmpdir) / "resume.json"
        resume_path.write_text(json.dumps({"results": [{"rank": 1, "status": "ready"}]}, ensure_ascii=False), encoding="utf-8")
        resume = discover.load_resume_results(resume_path)
        results.append(check("resume loads processed ranks", 1 in resume and resume[1]["status"] == "ready", str(resume)))

    candidates = {
        1: {"rank": 1, "status": "ready", "confidence": "high", "selected_source_url": "https://www.youtube.com/channel/UCreadyabcdefghijkl/videos"},
        2: {"rank": 2, "status": "ambiguous", "confidence": "medium", "selected_source_url": ""},
        3: {"rank": 3, "status": "not_found", "confidence": "not_found", "selected_source_url": ""},
    }
    tiny_dataset = {"artists": [artist("Ready Artist", 1), artist("Ambiguous Artist", 2), artist("No Source Artist", 3)]}
    plan = importer.build_import_plan(tiny_dataset, candidates, existing_presets=[])
    summary = importer.summarize_plan(plan)
    results.append(check("high candidate creates source", summary.get("create_with_source") == 1, str(summary)))
    results.append(check("ambiguous/not_found create OFF presets without source", summary.get("create_without_source") == 2 and all(not item.values["enabled"] for item in plan), str(summary)))
    repository = FakeRepository()
    applied = importer.apply_import_plan(repository, "guild-a", plan)
    results.append(check("apply creates all planned presets", len(applied) == 3 and len(repository.created) == 3, str(repository.created)))
    results.append(check("only high candidate source registered", sum(len(value) for value in repository.sources.values()) == 1, str(repository.sources)))
    plan_after = importer.build_import_plan(tiny_dataset, candidates, existing_presets=repository.list_presets("guild-a"))
    results.append(check("rerun is idempotent via existing command keys", all(action.action == "skip_existing" for action in plan_after), str([action.action for action in plan_after])))
    existing = [{"id": 1, "command_name": "Ready Artist", "command_key": normalize_command_name("Ready Artist"), "aliases": ""}]
    skip_plan = importer.build_import_plan(tiny_dataset, candidates, existing_presets=existing)
    results.append(check("existing preset is not modified", skip_plan[0].action == "skip_existing" and not skip_plan[0].sources, str(skip_plan[0])))
    skip_unresolved = importer.build_import_plan(tiny_dataset, candidates, existing_presets=[], include_unresolved=False)
    results.append(check("skip unresolved option avoids empty-source creates", [action.action for action in skip_unresolved] == ["create", "skip_unresolved", "skip_unresolved"], str([action.action for action in skip_unresolved])))
    results.append(check("command normalizer supports spaces width punctuation", normalize_command_name("Ａぇ！　group") == normalize_command_name("Aぇ! group")))
    results.append(check("refresh delay lower bound is 5 seconds", max(importer.MIN_REFRESH_DELAY_SECONDS, 0) == 5))
    results.append(check("429/cookie refresh errors stop", importer._refresh_should_stop("HTTP Error 429") and importer._refresh_should_stop("Cookie required")))

    with tempfile.TemporaryDirectory() as tmpdir:
        json_path = Path(tmpdir) / "candidates.json"
        csv_path = Path(tmpdir) / "candidates.csv"
        discover.write_reports(tiny_dataset, [{"rank": 1, "status": "ready", "selected_source_url": "https://www.youtube.com/channel/UCreadyabcdefghijkl/videos"}], json_path, csv_path)
        report_text = json_path.read_text(encoding="utf-8") + csv_path.read_text(encoding="utf-8-sig")
        results.append(check("reports do not contain secret-like fields", all(term not in report_text.casefold() for term in ("token", "cookie", "secret")), report_text))

    ok_count = sum(1 for result in results if result)
    print("summary: {0}/{1} OK".format(ok_count, len(results)))
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
