import asyncio
import html
import json
import sys
import time
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from bot.services.spotify_client import SpotifyPlaylistMetadata, SpotifyTrackMetadata
from bot.services.spotify_link import parse_spotify_link
from bot.services.spotify_playlist.errors import SpotifyPlaylistNoTracks
import bot.services.spotify_public as spotify_public
from bot.services.spotify_resolver import ResolvedYouTubeTrack
from bot.services.voice.models import MusicTrack
from bot.services.voice.session import clear_music_state, get_music_state
import bot.services.voice_music as voice_music


PLAYLIST_ID = "1Q2W3E4R5T6Y7U8I9O0P1A"
REAL_PLAYLIST_ID = "6wtgpQbVF1aJ4irWRKE0Rq"


def check(name: str, ok: bool, detail: str = "") -> bool:
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


def sample_track(index: int) -> SpotifyTrackMetadata:
    return SpotifyTrackMetadata(
        track_id="TRACK{0:017d}".format(index),
        name="Playlist Song {0}".format(index),
        artists=["Artist {0}".format(index)],
        album_name="Album {0}".format(index),
        duration_ms=(180 + index) * 1000,
        isrc="",
        explicit=False,
        spotify_url="https://open.spotify.com/track/TRACK{0:017d}".format(index),
        disc_number=1,
        track_number=index,
    )


def playlist_with_tracks(count: int, skipped: int = 0) -> SpotifyPlaylistMetadata:
    return SpotifyPlaylistMetadata(
        playlist_id=PLAYLIST_ID,
        name="Test Playlist",
        spotify_url="https://open.spotify.com/playlist/{0}".format(PLAYLIST_ID),
        image_url="https://image.example/cover.jpg",
        tracks=[sample_track(index) for index in range(1, count + 1)],
        skipped_tracks=skipped,
        total_tracks=count + skipped,
    )


class FakePublicResolver:
    def __init__(self, playlist: SpotifyPlaylistMetadata):
        self.playlist = playlist
        self.get_playlist_calls = 0

    async def get_playlist(self, playlist_id: str) -> SpotifyPlaylistMetadata:
        self.get_playlist_calls += 1
        return self.playlist


class FakeAuthor:
    def __init__(self, user_id="requester"):
        self.id = user_id
        self.bot = False


class FakeGuild:
    def __init__(self, guild_id):
        self.id = guild_id
        self.voice_client = None


class FakeChannel:
    def __init__(self):
        self.messages = []
        self.embeds = []
        self.id = "text-channel"

    async def send(self, content="", embed=None):
        self.messages.append(str(content))
        if embed is not None:
            self.embeds.append(embed)


class FakeMessage:
    def __init__(self, guild_id="guild-playlist"):
        self.guild = FakeGuild(guild_id)
        self.channel = FakeChannel()
        self.author = FakeAuthor()


class FakeVoiceClient:
    def __init__(self):
        self.channel = type("Channel", (), {"id": "voice-channel"})()

    def is_connected(self):
        return True


def public_embed_html(track_count=17, playlist_id=REAL_PLAYLIST_ID):
    tracks = []
    for index in range(1, track_count + 1):
        tracks.append(
            {
                "uri": "spotify:track:PUBLIC{0:016d}".format(index),
                "title": "Public Song {0}".format(index),
                "subtitle": "Public Artist {0}".format(index),
                "duration": "3:{0:02d}".format(index % 60),
            }
        )
    payload = {
        "props": {
            "pageProps": {
                "state": {
                    "data": {
                        "entity": {
                            "name": "Public Playlist",
                            "uri": "spotify:playlist:{0}".format(playlist_id),
                            "subtitle": "Playlist Owner",
                            "coverArt": {"url": "https://image.example/public.jpg"},
                            "trackList": tracks,
                        }
                    }
                }
            }
        }
    }
    return '<html><script id="__NEXT_DATA__" type="application/json">{0}</script></html>'.format(
        html.escape(json.dumps(payload))
    )


async def run_public_playlist_provider_checks(results):
    playlist = spotify_public.parse_public_playlist_html(REAL_PLAYLIST_ID, public_embed_html(17))
    results.append(check("public embed static html extracts 17 tracks", len(playlist.tracks) == 17, str(len(playlist.tracks))))
    results.append(check("public embed keeps order", [track.name for track in playlist.tracks[:3]] == ["Public Song 1", "Public Song 2", "Public Song 3"], str([track.name for track in playlist.tracks[:3]])))
    results.append(check("public embed extracts artists", playlist.tracks[0].display_artist == "Public Artist 1", playlist.tracks[0].display_artist))
    results.append(check("public embed extracts duration", playlist.tracks[0].duration_seconds == 181, str(playlist.tracks[0].duration_seconds)))
    results.append(check("public embed extracts cover", playlist.image_url.endswith("public.jpg"), playlist.image_url))
    duplicate_html = public_embed_html(2).replace("PUBLIC0000000000000002", "PUBLIC0000000000000001")
    duplicate_playlist = spotify_public.parse_public_playlist_html(REAL_PLAYLIST_ID, duplicate_html)
    results.append(check("public embed removes duplicate tracks", len(duplicate_playlist.tracks) == 1, str(len(duplicate_playlist.tracks))))

    empty_html = public_embed_html(0)
    try:
        spotify_public.parse_public_playlist_html(REAL_PLAYLIST_ID, empty_html)
    except SpotifyPlaylistNoTracks:
        results.append(check("public embed zero tracks is a distinct failure", True))
    except Exception as exc:
        results.append(check("public embed zero tracks is a distinct failure", False, type(exc).__name__))
    else:
        results.append(check("public embed zero tracks is a distinct failure", False, "no error"))


async def run_queue_checks(results):
    guild_id = "guild-playlist"
    clear_music_state(guild_id)
    state = get_music_state(guild_id)
    state.current = MusicTrack("Current", "https://youtube.example/current", "stream", "other")
    fake_resolver = FakePublicResolver(playlist_with_tracks(30, skipped=2))
    original_get_public_resolver = voice_music.get_spotify_public_resolver
    original_resolve = voice_music.resolve_spotify_track_to_youtube
    original_extract = voice_music.extract_track_info_with_cookie_fallback
    original_play_next = voice_music.play_next_track
    resolve_calls = []
    extract_calls = []

    async def fake_resolve(spotify_track, guild_id_arg, bypass_cache=False):
        resolve_calls.append((spotify_track.track_id, guild_id_arg, bypass_cache))
        return ResolvedYouTubeTrack(
            spotify_track.track_id,
            "https://www.youtube.com/watch?v=YT{0}".format(spotify_track.track_id[-6:]),
            "yt {0}".format(spotify_track.name),
            spotify_track.duration_seconds,
            90,
            time.time(),
        )

    async def fake_extract(url, requester_id, guild_id_arg, voice_client=None):
        extract_calls.append((url, requester_id, guild_id_arg))
        return MusicTrack(
            title="YouTube {0}".format(url[-6:]),
            webpage_url=url,
            stream_url="https://stream.example/{0}".format(url[-6:]),
            requester_id=requester_id,
            duration=180,
            source_url=url,
        )

    async def fake_play_next(voice_client, guild_id_arg):
        return True

    try:
        voice_music.get_spotify_public_resolver = lambda: fake_resolver
        voice_music.resolve_spotify_track_to_youtube = fake_resolve
        voice_music.extract_track_info_with_cookie_fallback = fake_extract
        voice_music.play_next_track = fake_play_next

        message = FakeMessage(guild_id)
        link = parse_spotify_link("https://open.spotify.com/playlist/{0}?si=abc".format(PLAYLIST_ID))
        handled = await voice_music.enqueue_spotify_link(message, link, FakeVoiceClient())
        results.append(check("playlist enqueue handled", handled))
        results.append(check("playlist metadata fetched once from public resolver", fake_resolver.get_playlist_calls == 1, str(fake_resolver.get_playlist_calls)))
        results.append(check("playlist queues all tracks lazily", len(state.queue) == 30 and all(track.source_type == "spotify_playlist" for track in state.queue), str(len(state.queue))))
        results.append(check("playlist enqueue does not resolve all youtube upfront", not resolve_calls and not extract_calls, str((resolve_calls, extract_calls))))
        results.append(check("playlist summary sends one message", len(message.channel.messages) == 1 and len(message.channel.embeds) == 1, str(message.channel.messages)))
        results.append(check("playlist queue is guild scoped", not get_music_state("guild-other").queue, str(len(get_music_state("guild-other").queue))))

        first = state.queue[0]
        resolved_first = await voice_music.refresh_track_for_playback(first, guild_id, FakeVoiceClient())
        results.append(check("playlist track resolves at playback", resolved_first is not None and resolved_first.stream_url and resolved_first.source_url.startswith("https://www.youtube.com/watch"), str(resolved_first)))
        results.append(check("lazy resolved track keeps playlist metadata", resolved_first.spotify_playlist_id == PLAYLIST_ID and resolved_first.spotify_playlist_index == 1))
        results.append(check("lazy resolve uses existing youtube path", len(resolve_calls) == 1 and len(extract_calls) == 1, str((resolve_calls, extract_calls))))

        await voice_music.prefetch_spotify_playlist_tracks(FakeVoiceClient(), guild_id, limit=2)
        queued = list(state.queue)
        prefetched = [track for track in queued[:2] if track.stream_url and track.spotify_resolve_status == "prefetched"]
        results.append(check("playlist prefetch resolves next two", len(prefetched) == 2, str([(track.spotify_playlist_index, track.spotify_resolve_status) for track in queued[:3]])))
        results.append(check("playlist prefetch leaves later tracks pending", queued[2].spotify_resolve_status == "pending" and not queued[2].stream_url, queued[2].spotify_resolve_status))

        empty_resolver = FakePublicResolver(playlist_with_tracks(0))
        voice_music.get_spotify_public_resolver = lambda: empty_resolver
        clear_music_state("guild-empty")
        empty_message = FakeMessage("guild-empty")
        handled_empty = await voice_music.enqueue_spotify_link(empty_message, link, FakeVoiceClient())
        results.append(check("empty playlist handled with message", handled_empty and not get_music_state("guild-empty").queue and empty_message.channel.messages, str(empty_message.channel.messages)))
    finally:
        voice_music.get_spotify_public_resolver = original_get_public_resolver
        voice_music.resolve_spotify_track_to_youtube = original_resolve
        voice_music.extract_track_info_with_cookie_fallback = original_extract
        voice_music.play_next_track = original_play_next
        clear_music_state(guild_id)
        clear_music_state("guild-empty")


async def main() -> int:
    results = []
    playlist_link = parse_spotify_link("https://open.spotify.com/playlist/{0}?si=abc&utm_source=copy-link".format(PLAYLIST_ID))
    playlist_uri = parse_spotify_link("spotify:playlist:{0}".format(PLAYLIST_ID))
    results.append(check("playlist url supported", playlist_link is not None and playlist_link.is_supported and playlist_link.kind == "playlist", str(playlist_link)))
    results.append(check("playlist uri supported", playlist_uri is not None and playlist_uri.is_supported and playlist_uri.kind == "playlist", str(playlist_uri)))
    await run_public_playlist_provider_checks(results)
    await run_queue_checks(results)
    print("spotify playlist support checks: {0}/{1}".format(sum(1 for value in results if value), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
