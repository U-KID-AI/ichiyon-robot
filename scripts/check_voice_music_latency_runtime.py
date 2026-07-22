import sys
import time
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot.services.voice_music import (
    MusicTrack,
    YoutubeExtractStageRecorder,
    build_ytdl_options,
    classify_ytdlp_stage,
    extract_youtube_video_id,
    format_music_timing_fields,
    make_loop_track,
    mark_track_enqueued,
    perf_ms,
    queue_wait_ms,
)


def check(name: str, ok: bool, detail: str = "") -> bool:
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


def main() -> int:
    results = []
    started = time.perf_counter()
    results.append(check("perf ms is monotonic", perf_ms(started) >= 0))

    track = MusicTrack("title", "https://example.com/watch", "https://stream.example/audio", "123", 100, "https://example.com/watch")
    results.append(check("new track has no queue wait", queue_wait_ms(track) is None))
    mark_track_enqueued(track)
    results.append(check("marked track has queue wait", queue_wait_ms(track) is not None and queue_wait_ms(track) >= 0, str(queue_wait_ms(track))))

    loop_track = make_loop_track(track)
    results.append(check("loop track resets queue timestamp", loop_track.enqueued_at_monotonic >= track.enqueued_at_monotonic, str(loop_track)))
    results.append(check("loop track still requires refresh", loop_track.refresh_required is True and loop_track.stream_url == "", str(loop_track)))

    fields = format_music_timing_fields(
        {
            "total_ms": 12,
            "source_type": "youtube",
            "queue_wait_ms": None,
        }
    )
    results.append(check("timing fields omit None values", "queue_wait_ms" not in fields, fields))
    results.append(check("timing fields include safe values", "total_ms=12" in fields and "source_type=youtube" in fields, fields))
    results.append(check("youtube watch id is extracted safely", extract_youtube_video_id("https://www.youtube.com/watch?v=abc123&list=secret") == "abc123"))
    results.append(check("youtu.be id is extracted safely", extract_youtube_video_id("https://youtu.be/xyz987?t=1") == "xyz987"))
    results.append(check("shorts id is extracted safely", extract_youtube_video_id("https://www.youtube.com/shorts/short123") == "short123"))

    stage_examples = {
        "[youtube] abc: Downloading webpage": "webpage",
        "[youtube] abc: Downloading ios player API JSON": "player_api",
        "[youtube] abc: Downloading player abc123": "player_js",
        "[jsc:deno] Solving JS challenges using deno": "challenge",
        "[youtube] abc: Downloading m3u8 information": "manifest",
        "[info] abc: Downloading 1 format(s): 251": "format",
        "[debug] Loading youtube-nsig cache": "cache",
    }
    for message, expected_stage in stage_examples.items():
        results.append(check("yt-dlp stage classified: {0}".format(expected_stage), classify_ytdlp_stage(message) == expected_stage, str(classify_ytdlp_stage(message))))

    recorder = YoutubeExtractStageRecorder("abc123")
    recorder.set_option_build_ms(2)
    recorder.debug("[youtube] abc: Downloading webpage")
    time.sleep(0.001)
    recorder.debug("[youtube] abc: Downloading ios player API JSON")
    time.sleep(0.001)
    recorder.debug("[jsc:deno] Solving JS challenges using deno")
    time.sleep(0.001)
    recorder.finish_extract_info(50)
    recorder.set_result_processing_ms(3)
    stages = dict(recorder.iter_stage_timings())
    results.append(check("recorder tracks webpage stage", "webpage" in stages, str(stages)))
    results.append(check("recorder tracks player api stage", "player_api" in stages, str(stages)))
    results.append(check("recorder tracks challenge stage", "challenge" in stages, str(stages)))
    results.append(check("recorder keeps unknown extract bucket", "unknown_extract" in stages and stages["unknown_extract"] >= 0, str(stages)))
    results.append(check("recorder includes result processing", stages.get("result_processing") == 3, str(stages)))

    options_recorder = YoutubeExtractStageRecorder("abc123")
    options = build_ytdl_options("guild", copy_cookies=False, stage_recorder=options_recorder)
    results.append(check("yt-dlp options include custom stage logger", options.get("logger") is options_recorder))
    results.append(check("yt-dlp options keep deno ejs", options.get("js_runtimes") == {"deno": {}} and options.get("remote_components") == ["ejs:github"], str(options)))
    results.append(check("timing stage output avoids urls and secrets", "youtube.com" not in format_music_timing_fields({"stage": "webpage", "video_id": "abc123", "elapsed_ms": 5}), ""))

    passed = len([item for item in results if item])
    print("summary: {0}/{1} OK".format(passed, len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
