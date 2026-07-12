import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot.services.voice_music import (
    MusicState,
    MusicTrack,
    format_now_playing,
    format_queue,
    is_http_url,
    parse_music_command,
)


def check(name: str, ok: bool, detail: str = "") -> bool:
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


def main() -> int:
    results = []
    play_examples = {
        "歌え https://example.com/a": ("music_play", "https://example.com/a"),
        "流して https://example.com/b": ("music_play", "https://example.com/b"),
        "音楽 https://example.com/c": ("music_play", "https://example.com/c"),
        "play https://example.com/d": ("music_play", "https://example.com/d"),
    }
    for command, expected in play_examples.items():
        results.append(check("play command parses: {0}".format(command), parse_music_command(command) == expected, str(parse_music_command(command))))

    control_examples = {
        "スキップ": "music_skip",
        "skip": "music_skip",
        "次": "music_skip",
        "次の曲": "music_skip",
        "一時停止": "music_pause",
        "pause": "music_pause",
        "再開": "music_resume",
        "resume": "music_resume",
        "キュー": "music_queue",
        "queue": "music_queue",
        "再生予定": "music_queue",
        "今何": "music_now",
        "now": "music_now",
        "nowplaying": "music_now",
    }
    for command, expected_action in control_examples.items():
        action, argument = parse_music_command(command)
        results.append(check("control command parses: {0}".format(command), action == expected_action and argument == "", str((action, argument))))

    results.append(check("http url is accepted", is_http_url("https://example.com/watch?v=1")))
    results.append(check("non-url is rejected", not is_http_url("not-a-url")))
    results.append(check("javascript url is rejected", not is_http_url("javascript:alert(1)")))

    state = MusicState()
    first = MusicTrack("一曲目", "https://example.com/1", "https://stream.example.com/1", "111", 125)
    second = MusicTrack("二曲目", "https://example.com/2", "https://stream.example.com/2", "222", None)
    state.current = first
    state.queue.append(second)
    queue_text = format_queue(state)
    now_text = format_now_playing(state)
    results.append(check("queue shows current track", "再生中: 一曲目" in queue_text, queue_text))
    results.append(check("queue shows waiting track", "1. 二曲目" in queue_text, queue_text))
    results.append(check("now playing shows requester", "リクエスト: <@111>" in now_text, now_text))
    results.append(check("empty queue message", format_queue(MusicState()) == "キューは空です。"))
    results.append(check("empty now playing message", format_now_playing(MusicState()) == "現在再生中の曲はありません。"))

    ok_count = sum(1 for item in results if item)
    print("summary: {0}/{1} OK".format(ok_count, len(results)))
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
