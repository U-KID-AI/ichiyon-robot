import sys
import time
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot.services.voice_music import (
    MusicTrack,
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

    passed = len([item for item in results if item])
    print("summary: {0}/{1} OK".format(passed, len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
