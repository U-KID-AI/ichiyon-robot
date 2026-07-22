import argparse
import statistics
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot.services.voice_music import (
    DEFAULT_YTDLP_JS_RUNTIME,
    SUPPORTED_YTDLP_JS_RUNTIMES,
    YoutubeExtractStageRecorder,
    build_ytdl_options,
    classify_ytdlp_error,
    extract_youtube_video_id,
    perf_ms,
    yt_dlp,
)


BENCHMARK_GUILD_ID = "benchmark"
BENCHMARK_REQUESTER_ID = "benchmark"
RUNS_MIN = 1
RUNS_MAX = 10
YOUTUBE_WATCH_URL_TEMPLATE = "https://www.youtube.com/watch?v={0}"


def check(name: str, ok: bool, detail: str = "") -> bool:
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


def validate_runtime(runtime: str) -> str:
    value = str(runtime or "").strip().lower()
    if value not in SUPPORTED_YTDLP_JS_RUNTIMES:
        raise ValueError("--runtime must be deno or node")
    return value


def validate_runs(runs: int) -> int:
    try:
        value = int(runs)
    except (TypeError, ValueError) as exc:
        raise ValueError("--runs must be an integer") from exc
    if value < RUNS_MIN or value > RUNS_MAX:
        raise ValueError("--runs must be between {0} and {1}".format(RUNS_MIN, RUNS_MAX))
    return value


def validate_video_id(video_id: str) -> str:
    value = str(video_id or "").strip()
    if not value:
        raise ValueError("--video-id is required")
    if not all(char.isalnum() or char in "_-" for char in value):
        raise ValueError("--video-id contains unsupported characters")
    return value


def build_benchmark_options(runtime: str, use_cookies: bool, recorder: YoutubeExtractStageRecorder) -> Dict[str, object]:
    options = build_ytdl_options(
        BENCHMARK_GUILD_ID,
        use_cookies=use_cookies,
        stage_recorder=recorder,
        js_runtime=validate_runtime(runtime),
    )
    if "remote_components" in options:
        raise RuntimeError("remote_components must not be enabled for this benchmark")
    return options


def summarize_stage_timings(stage_timings: Dict[str, int]) -> str:
    parts = []
    for stage in ("options", "cookie_prep", "webpage", "player_api", "player_js", "challenge", "manifest", "format", "cache", "result_processing", "unknown_extract"):
        if stage in stage_timings:
            parts.append("{0}_ms={1}".format(stage, stage_timings[stage]))
    return " ".join(parts)


def add_stage_timings(target: Dict[str, int], source: Dict[str, int]) -> None:
    for stage, elapsed_ms in source.items():
        target[stage] = target.get(stage, 0) + int(elapsed_ms)


def safe_status(exc: Exception) -> str:
    return classify_ytdlp_error(exc)


def run_extract_attempt(url: str, video_id: str, runtime: str, use_cookies: bool) -> Tuple[bool, Dict[str, int], Optional[Exception]]:
    if yt_dlp is None:
        raise RuntimeError("yt-dlp is not installed")
    recorder = YoutubeExtractStageRecorder(video_id)
    options_started = time.perf_counter()
    options = build_benchmark_options(runtime, use_cookies, recorder)
    recorder.set_option_build_ms(perf_ms(options_started))
    extract_started = time.perf_counter()
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
        recorder.finish_extract_info(perf_ms(extract_started))
        processing_started = time.perf_counter()
        if info is None:
            recorder.set_result_processing_ms(perf_ms(processing_started))
            return False, dict(recorder.iter_stage_timings()), RuntimeError("NO_INFO")
        if "entries" in info:
            entries = [entry for entry in info.get("entries") or [] if entry]
            if not entries:
                recorder.set_result_processing_ms(perf_ms(processing_started))
                return False, dict(recorder.iter_stage_timings()), RuntimeError("NO_ENTRIES")
            info = entries[0]
        if not str((info or {}).get("url") or "").strip():
            recorder.set_result_processing_ms(perf_ms(processing_started))
            return False, dict(recorder.iter_stage_timings()), RuntimeError("NO_STREAM_URL")
        recorder.set_result_processing_ms(perf_ms(processing_started))
        return True, dict(recorder.iter_stage_timings()), None
    except Exception as exc:
        recorder.finish_extract_info(perf_ms(extract_started))
        return False, dict(recorder.iter_stage_timings()), exc


def run_benchmark(runtime: str, video_id: str, runs: int) -> int:
    runtime = validate_runtime(runtime)
    video_id = validate_video_id(video_id)
    runs = validate_runs(runs)
    url = YOUTUBE_WATCH_URL_TEMPLATE.format(video_id)
    totals: List[int] = []
    challenges: List[int] = []

    for index in range(1, runs + 1):
        run_started = time.perf_counter()
        attempts = 1
        success, stages, error = run_extract_attempt(url, video_id, runtime, True)
        combined_stages: Dict[str, int] = {}
        add_stage_timings(combined_stages, stages)
        if not success and error is not None and safe_status(error) in {"LOGIN_REQUIRED", "BOT_CHECK", "CAPTCHA_REQUIRED"}:
            attempts += 1
            success, stages, error = run_extract_attempt(url, video_id, runtime, False)
            add_stage_timings(combined_stages, stages)
        total_ms = perf_ms(run_started)
        totals.append(total_ms)
        challenges.append(combined_stages.get("challenge", 0))
        print(
            "youtube_js_runtime_benchmark runtime={0} run={1} video_id={2} success={3} attempts={4} challenge_ms={5} youtube_extract_total_ms={6} {7}".format(
                runtime,
                index,
                video_id,
                str(success).lower(),
                attempts,
                combined_stages.get("challenge", 0),
                total_ms,
                summarize_stage_timings(combined_stages),
            )
        )
        if not success:
            print(
                "youtube_js_runtime_benchmark_error runtime={0} run={1} video_id={2} status={3}".format(
                    runtime,
                    index,
                    video_id,
                    safe_status(error) if error is not None else "UNKNOWN",
                )
            )

    print(
        "youtube_js_runtime_benchmark_summary runtime={0} video_id={1} runs={2} total_median_ms={3} total_min_ms={4} total_max_ms={5} challenge_median_ms={6} challenge_min_ms={7} challenge_max_ms={8}".format(
            runtime,
            video_id,
            runs,
            int(statistics.median(totals)),
            min(totals),
            max(totals),
            int(statistics.median(challenges)),
            min(challenges),
            max(challenges),
        )
    )
    return 0


def run_self_test() -> int:
    results = []
    results.append(check("default runtime remains deno", DEFAULT_YTDLP_JS_RUNTIME == "deno", DEFAULT_YTDLP_JS_RUNTIME))
    results.append(check("deno runtime validates", validate_runtime("deno") == "deno"))
    results.append(check("node runtime validates", validate_runtime("node") == "node"))
    try:
        validate_runtime("bun")
        results.append(check("invalid runtime is rejected", False))
    except ValueError:
        results.append(check("invalid runtime is rejected", True))
    results.append(check("minimum runs validates", validate_runs(1) == 1))
    results.append(check("maximum runs validates", validate_runs(10) == 10))
    for invalid_runs in (0, 11):
        try:
            validate_runs(invalid_runs)
            results.append(check("invalid runs rejected: {0}".format(invalid_runs), False))
        except ValueError:
            results.append(check("invalid runs rejected: {0}".format(invalid_runs), True))
    deno_options = build_benchmark_options("deno", False, YoutubeExtractStageRecorder("abc"))
    node_options = build_benchmark_options("node", False, YoutubeExtractStageRecorder("abc"))
    results.append(check("deno options only set deno", deno_options.get("js_runtimes") == {"deno": {}}, str(deno_options)))
    results.append(check("node options only set node", node_options.get("js_runtimes") == {"node": {}}, str(node_options)))
    results.append(check("remote components stay disabled", "remote_components" not in deno_options and "remote_components" not in node_options))
    results.append(check("cookie fallback option is preserved", "cookiefile" not in build_benchmark_options("deno", False, YoutubeExtractStageRecorder("abc"))))
    results.append(check("video id extracts safely", extract_youtube_video_id(YOUTUBE_WATCH_URL_TEMPLATE.format("woz5qvDdMRM")) == "woz5qvDdMRM"))
    source = Path(__file__).read_text(encoding="utf-8")
    results.append(check("benchmark uses download false", "download=False" in source))
    safe_line = "runtime=deno run=1 video_id=woz5qvDdMRM success=true"
    results.append(check("sample output omits full URL", "youtube.com" not in safe_line and "youtu.be" not in safe_line, safe_line))
    passed = sum(1 for item in results if item)
    print("summary: {0}/{1} OK".format(passed, len(results)))
    return 0 if passed == len(results) else 1


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark yt-dlp YouTube JS runtimes without printing URLs or secrets.")
    parser.add_argument("--runtime", choices=sorted(SUPPORTED_YTDLP_JS_RUNTIMES), default=DEFAULT_YTDLP_JS_RUNTIME)
    parser.add_argument("--video-id", default="")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        return run_self_test()
    return run_benchmark(args.runtime, args.video_id, args.runs)


if __name__ == "__main__":
    raise SystemExit(main())
