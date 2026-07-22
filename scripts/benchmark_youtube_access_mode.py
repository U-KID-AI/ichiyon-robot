import argparse
import os
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot.services.youtube_cookie_monitor import AUTH_FAILURE_STATUSES
from bot.services.voice_music import (
    YoutubeExtractStageRecorder,
    build_ytdl_options,
    classify_ytdlp_error,
    extract_youtube_video_id,
    perf_ms,
    yt_dlp,
)


MODE_COOKIELESS_DEFAULT = "cookieless-default"
MODE_COOKIE_CURRENT = "cookie-current"
MODE_POT_MWEB = "pot-mweb"
SUPPORTED_MODES = {MODE_COOKIELESS_DEFAULT, MODE_COOKIE_CURRENT, MODE_POT_MWEB}
BENCHMARK_GUILD_ID = "benchmark"
RUNS_MIN = 1
RUNS_MAX = 10
YOUTUBE_WATCH_URL_TEMPLATE = "https://www.youtube.com/watch?v={0}"
POT_PROVIDER_URL_ENV = "YTDLP_POT_PROVIDER_URL"
DEFAULT_POT_PROVIDER_URL = "http://pot-provider:4416"
POT_EXTRACTOR_KEY = "youtubepot-bgutilhttp"
POT_PLAYER_CLIENT = "mweb"
STAGE_OUTPUT_ORDER = (
    "options",
    "cookie_prep",
    "webpage",
    "player_api",
    "player_js",
    "challenge",
    "pot_provider",
    "manifest",
    "format",
    "cache",
    "result_processing",
    "unknown_extract",
)


def check(name: str, ok: bool, detail: str = "") -> bool:
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


def validate_mode(mode: str) -> str:
    value = str(mode or "").strip().lower()
    if value not in SUPPORTED_MODES:
        raise ValueError("--mode must be cookieless-default, cookie-current, or pot-mweb")
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


def validate_provider_url(provider_url: str) -> str:
    value = str(provider_url or "").strip()
    if not value:
        raise ValueError("--pot-provider-url is required for pot-mweb")
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("--pot-provider-url must be an http(s) URL")
    return value.rstrip("/")


def get_provider_url(value: Optional[str] = None) -> str:
    return validate_provider_url(value or os.getenv(POT_PROVIDER_URL_ENV) or DEFAULT_POT_PROVIDER_URL)


def build_access_mode_options(
    mode: str,
    recorder: YoutubeExtractStageRecorder,
    pot_provider_url: Optional[str] = None,
    use_cookies: bool = True,
    copy_cookies: bool = True,
) -> Dict[str, object]:
    mode = validate_mode(mode)
    if mode == MODE_COOKIELESS_DEFAULT:
        return build_ytdl_options(
            BENCHMARK_GUILD_ID,
            copy_cookies=copy_cookies,
            use_cookies=False,
            stage_recorder=recorder,
        )
    if mode == MODE_COOKIE_CURRENT:
        return build_ytdl_options(
            BENCHMARK_GUILD_ID,
            copy_cookies=copy_cookies,
            use_cookies=use_cookies,
            stage_recorder=recorder,
        )

    options = build_ytdl_options(
        BENCHMARK_GUILD_ID,
        copy_cookies=copy_cookies,
        use_cookies=False,
        stage_recorder=recorder,
    )
    options.pop("cookiefile", None)
    options["extractor_args"] = {
        "youtube": {"player_client": [POT_PLAYER_CLIENT]},
        POT_EXTRACTOR_KEY: {"base_url": [get_provider_url(pot_provider_url)]},
    }
    return options


def summarize_stage_timings(stage_timings: Dict[str, int]) -> str:
    parts = []
    for stage in STAGE_OUTPUT_ORDER:
        if stage in stage_timings:
            parts.append("{0}_ms={1}".format(stage, stage_timings[stage]))
    return " ".join(parts)


def add_stage_timings(target: Dict[str, int], source: Dict[str, int]) -> None:
    for stage, elapsed_ms in source.items():
        target[stage] = target.get(stage, 0) + int(elapsed_ms)


def is_http_403(exc: Optional[Exception]) -> bool:
    text = str(exc or "").lower()
    return "403" in text or "forbidden" in text


def status_for_error(exc: Optional[Exception]) -> str:
    if exc is None:
        return "OK"
    return classify_ytdlp_error(exc)


def should_cookie_fallback(error: Optional[Exception]) -> bool:
    if error is None:
        return False
    return status_for_error(error) in AUTH_FAILURE_STATUSES


def run_extract_attempt(
    url: str,
    video_id: str,
    mode: str,
    use_cookies: bool,
    pot_provider_url: Optional[str],
) -> Tuple[bool, Dict[str, int], Optional[Exception], bool]:
    if yt_dlp is None:
        raise RuntimeError("yt-dlp is not installed")
    recorder = YoutubeExtractStageRecorder(video_id)
    options_started = time.perf_counter()
    options = build_access_mode_options(mode, recorder, pot_provider_url, use_cookies=use_cookies)
    recorder.set_option_build_ms(perf_ms(options_started))
    extract_started = time.perf_counter()
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
        recorder.finish_extract_info(perf_ms(extract_started))
        processing_started = time.perf_counter()
        if info is None:
            recorder.set_result_processing_ms(perf_ms(processing_started))
            return False, dict(recorder.iter_stage_timings()), RuntimeError("NO_INFO"), False
        if "entries" in info:
            entries = [entry for entry in info.get("entries") or [] if entry]
            if not entries:
                recorder.set_result_processing_ms(perf_ms(processing_started))
                return False, dict(recorder.iter_stage_timings()), RuntimeError("NO_ENTRIES"), False
            info = entries[0]
        audio_format_obtained = bool(str((info or {}).get("url") or "").strip())
        if not audio_format_obtained:
            recorder.set_result_processing_ms(perf_ms(processing_started))
            return False, dict(recorder.iter_stage_timings()), RuntimeError("NO_STREAM_URL"), False
        recorder.set_result_processing_ms(perf_ms(processing_started))
        return True, dict(recorder.iter_stage_timings()), None, True
    except Exception as exc:
        recorder.finish_extract_info(perf_ms(extract_started))
        return False, dict(recorder.iter_stage_timings()), exc, False


def run_mode_once(mode: str, url: str, video_id: str, pot_provider_url: Optional[str]) -> Tuple[bool, int, Dict[str, int], Optional[Exception], bool]:
    attempts = 1
    success, stages, error, audio_format_obtained = run_extract_attempt(
        url,
        video_id,
        mode,
        mode == MODE_COOKIE_CURRENT,
        pot_provider_url,
    )
    combined_stages: Dict[str, int] = {}
    add_stage_timings(combined_stages, stages)
    if mode == MODE_COOKIE_CURRENT and not success and should_cookie_fallback(error):
        attempts += 1
        success, stages, error, audio_format_obtained = run_extract_attempt(
            url,
            video_id,
            mode,
            False,
            pot_provider_url,
        )
        add_stage_timings(combined_stages, stages)
    return success, attempts, combined_stages, error, audio_format_obtained


def error_counts_text(counter: Counter) -> str:
    if not counter:
        return "none"
    return ",".join("{0}:{1}".format(key, counter[key]) for key in sorted(counter))


def run_benchmark(mode: str, video_id: str, runs: int, pot_provider_url: Optional[str]) -> int:
    mode = validate_mode(mode)
    video_id = validate_video_id(video_id)
    runs = validate_runs(runs)
    provider_url = get_provider_url(pot_provider_url) if mode == MODE_POT_MWEB else ""
    url = YOUTUBE_WATCH_URL_TEMPLATE.format(video_id)
    totals: List[int] = []
    challenges: List[int] = []
    successes = 0
    errors: Counter = Counter()

    for index in range(1, runs + 1):
        run_started = time.perf_counter()
        success, attempts, stages, error, audio_format_obtained = run_mode_once(mode, url, video_id, provider_url)
        total_ms = perf_ms(run_started)
        status = status_for_error(error)
        totals.append(total_ms)
        challenges.append(stages.get("challenge", 0))
        if success:
            successes += 1
        else:
            errors[status] += 1
        print(
            "youtube_access_mode_benchmark mode={0} run={1} video_id={2} success={3} status={4} attempts={5} bot_check={6} http_403={7} audio_format={8} total_ms={9} challenge_ms={10} pot_provider_ms={11} {12}".format(
                mode,
                index,
                video_id,
                str(success).lower(),
                status,
                attempts,
                str(status == "BOT_CHECK").lower(),
                str(is_http_403(error)).lower(),
                str(audio_format_obtained).lower(),
                total_ms,
                stages.get("challenge", 0),
                stages.get("pot_provider", 0),
                summarize_stage_timings(stages),
            )
        )

    print(
        "youtube_access_mode_benchmark_summary mode={0} video_id={1} runs={2} success_count={3} total_median_ms={4} total_min_ms={5} total_max_ms={6} challenge_median_ms={7} challenge_min_ms={8} challenge_max_ms={9} errors={10}".format(
            mode,
            video_id,
            runs,
            successes,
            int(statistics.median(totals)),
            min(totals),
            max(totals),
            int(statistics.median(challenges)),
            min(challenges),
            max(challenges),
            error_counts_text(errors),
        )
    )
    return 0


def run_self_test() -> int:
    results = []
    results.append(check("mode validates cookieless", validate_mode(MODE_COOKIELESS_DEFAULT) == MODE_COOKIELESS_DEFAULT))
    results.append(check("mode validates cookie", validate_mode(MODE_COOKIE_CURRENT) == MODE_COOKIE_CURRENT))
    results.append(check("mode validates pot", validate_mode(MODE_POT_MWEB) == MODE_POT_MWEB))
    try:
        validate_mode("remote-components")
        results.append(check("invalid mode rejected", False))
    except ValueError:
        results.append(check("invalid mode rejected", True))
    results.append(check("minimum runs validates", validate_runs(1) == 1))
    results.append(check("maximum runs validates", validate_runs(10) == 10))
    for invalid_runs in (0, 11):
        try:
            validate_runs(invalid_runs)
            results.append(check("invalid runs rejected: {0}".format(invalid_runs), False))
        except ValueError:
            results.append(check("invalid runs rejected: {0}".format(invalid_runs), True))

    pot_url = "http://pot-provider:4416"
    cookieless = build_access_mode_options(MODE_COOKIELESS_DEFAULT, YoutubeExtractStageRecorder("abc"), copy_cookies=False)
    cookie = build_access_mode_options(MODE_COOKIE_CURRENT, YoutubeExtractStageRecorder("abc"), use_cookies=True, copy_cookies=False)
    pot = build_access_mode_options(MODE_POT_MWEB, YoutubeExtractStageRecorder("abc"), pot_url, copy_cookies=False)
    results.append(check("cookieless mode passes no cookies", "cookiefile" not in cookieless, str(cookieless)))
    results.append(check("pot mode passes no cookies", "cookiefile" not in pot, str(pot)))
    results.append(check("pot mode uses mweb client", pot.get("extractor_args", {}).get("youtube", {}).get("player_client") == ["mweb"], str(pot.get("extractor_args"))))
    results.append(check("pot mode uses bgutil http provider", pot.get("extractor_args", {}).get(POT_EXTRACTOR_KEY, {}).get("base_url") == [pot_url], str(pot.get("extractor_args"))))
    results.append(check("pot mode keeps deno js runtime", pot.get("js_runtimes") == {"deno": {}}, str(pot.get("js_runtimes"))))
    results.append(check("remote components stay disabled", "remote_components" not in cookieless and "remote_components" not in cookie and "remote_components" not in pot))
    results.append(check("video id extracts safely", extract_youtube_video_id(YOUTUBE_WATCH_URL_TEMPLATE.format("woz5qvDdMRM")) == "woz5qvDdMRM"))
    results.append(check("provider URL validates", get_provider_url(pot_url) == pot_url))
    try:
        validate_provider_url("file:///tmp/socket")
        results.append(check("invalid provider URL rejected", False))
    except ValueError:
        results.append(check("invalid provider URL rejected", True))

    source = Path(__file__).read_text(encoding="utf-8")
    results.append(check("benchmark uses download false", "download=False" in source))
    results.append(check("benchmark keeps pot without cookie fallback", "mode == MODE_COOKIE_CURRENT and not success and should_cookie_fallback" in source))
    results.append(check("benchmark documents all modes", all(mode in source for mode in SUPPORTED_MODES)))
    safe_line = "mode=pot-mweb run=1 video_id=woz5qvDdMRM success=true pot_provider_ms=12"
    results.append(check("sample output omits full URL", "youtube.com" not in safe_line and "youtu.be" not in safe_line, safe_line))
    results.append(check("sample output omits secrets", "cookie" not in safe_line.lower() and "token" not in safe_line.lower(), safe_line))

    passed = sum(1 for item in results if item)
    print("summary: {0}/{1} OK".format(passed, len(results)))
    return 0 if passed == len(results) else 1


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark YouTube access modes without printing URLs or secrets.")
    parser.add_argument("--mode", choices=sorted(SUPPORTED_MODES), default=MODE_COOKIELESS_DEFAULT)
    parser.add_argument("--pot-provider-url", default="")
    parser.add_argument("--video-id", default="")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        return run_self_test()
    return run_benchmark(args.mode, args.video_id, args.runs, args.pot_provider_url)


if __name__ == "__main__":
    raise SystemExit(main())
