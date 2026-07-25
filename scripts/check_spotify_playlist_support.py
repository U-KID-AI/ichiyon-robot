import asyncio
import sys
import time
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from bot.services.spotify_client import (
    SpotifyApiError,
    SpotifyClient,
    SpotifyPlaylistMetadata,
    SpotifyRateLimitedError,
    SpotifyTimeoutError,
    SpotifyTrackMetadata,
)
from bot.services.spotify_link import parse_spotify_link
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


class FakeSpotifyClient:
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


def playlist_metadata_payload(total=17, embedded_items=None, next_url=None):
    return {
        "id": PLAYLIST_ID,
        "name": "API Playlist",
        "external_urls": {"spotify": "https://open.spotify.com/playlist/{0}".format(PLAYLIST_ID)},
        "images": [{"url": "https://image.example/api.jpg"}],
        "tracks": {"total": total, "items": embedded_items or [], "next": next_url},
    }


def playlist_track_payload(index, **overrides):
    payload = {
        "id": "TRACK{0:017d}".format(index),
        "type": "track",
        "name": "Song {0}".format(index),
        "artists": [{"name": "Artist {0}".format(index)}],
        "album": {"name": "Album {0}".format(index)},
        "duration_ms": (180 + index) * 1000,
        "external_ids": {},
        "external_urls": {"spotify": "https://open.spotify.com/track/TRACK{0:017d}".format(index)},
        "disc_number": 1,
        "track_number": index,
        "explicit": False,
    }
    payload.update(overrides)
    return payload


def playlist_item_entry(index, field="item", **overrides):
    return {field: playlist_track_payload(index, **overrides)}


async def run_client_pagination_checks(results):
    calls = []
    client = SpotifyClient("id", "secret")

    async def fake_get_json_with_status(path, params=None, retry_auth=True):
        calls.append((path, dict(params or {})))
        if path == "/playlists/{0}".format(PLAYLIST_ID):
            return playlist_metadata_payload(20), 200
        if path == "/playlists/{0}/items".format(PLAYLIST_ID) and str((params or {}).get("offset") or "") != "10":
            return {
                "next": "https://api.spotify.com/v1/playlists/{0}/items?offset=10&limit=10".format(PLAYLIST_ID),
                "items": [playlist_item_entry(index, field="item", preview_url=None) for index in range(1, 11)],
            }, 200
        return {
            "next": None,
            "items": [
                playlist_item_entry(index, field="item", is_playable=None, available_markets=None)
                for index in range(11, 18)
            ]
            + [
                playlist_item_entry(18, field="item", is_local=True),
                {"item": {"id": "EPISODE0000000000001", "type": "episode", "name": "Podcast"}},
                {"item": None},
            ],
        }, 200

    client._get_json_with_status = fake_get_json_with_status
    playlist = await client.get_playlist(PLAYLIST_ID)
    results.append(check("playlist api uses metadata endpoint", calls[0][0] == "/playlists/{0}".format(PLAYLIST_ID), str(calls[:1])))
    results.append(check("playlist metadata request sends market only", calls[0][1] == {"market": "JP"}, str(calls[0][1])))
    results.append(check("playlist api uses items endpoint", any(call[0] == "/playlists/{0}/items".format(PLAYLIST_ID) for call in calls), str(calls)))
    results.append(check("playlist api sends market JP", all(call[1].get("market") == "JP" for call in calls), str(calls)))
    results.append(check("playlist item format keeps 17 tracks", len(playlist.tracks) == 17, str(len(playlist.tracks))))
    results.append(check("playlist item format keeps order", [track.name for track in playlist.tracks[:3]] == ["Song 1", "Song 2", "Song 3"], str([track.name for track in playlist.tracks[:3]])))
    results.append(check("playlist preview_url null is accepted", playlist.tracks[0].name == "Song 1", playlist.tracks[0].name))
    results.append(check("playlist missing is_playable is accepted", playlist.tracks[10].name == "Song 11", playlist.tracks[10].name))
    results.append(check("playlist missing available_markets is accepted", playlist.tracks[10].name == "Song 11", playlist.tracks[10].name))
    results.append(check("playlist api counts skipped items", playlist.skipped_tracks == 3, str(playlist.skipped_tracks)))
    results.append(check("playlist local tracks are skipped", playlist.local_tracks == 1, str(playlist.local_tracks)))
    results.append(check("playlist episodes are skipped", playlist.episode_tracks == 1, str(playlist.episode_tracks)))
    results.append(check("playlist null items are skipped", playlist.missing_metadata_tracks == 1, str(playlist.missing_metadata_tracks)))
    results.append(check("playlist api paginates items URL", any(call[0].endswith("/items") and call[1].get("offset") == "10" for call in calls), str(calls)))
    results.append(check("playlist api keeps cover image", playlist.image_url.endswith("api.jpg"), playlist.image_url))


async def run_legacy_track_field_checks(results):
    client = SpotifyClient("id", "secret")

    async def fake_get_json_with_status(path, params=None, retry_auth=True):
        if path == "/playlists/{0}".format(PLAYLIST_ID):
            return playlist_metadata_payload(2), 200
        return {
            "next": None,
            "items": [
                playlist_item_entry(1, field="track"),
                playlist_item_entry(2, field="track"),
            ],
        }, 200

    client._get_json_with_status = fake_get_json_with_status
    playlist = await client.get_playlist(PLAYLIST_ID)
    results.append(check("legacy track field format is supported", len(playlist.tracks) == 2 and playlist.track_field_tracks == 2, str((len(playlist.tracks), playlist.track_field_tracks))))


async def run_api_error_checks(results):
    async def playlist_for_error(error):
        client = SpotifyClient("id", "secret")

        async def fake_get_json_with_status(path, params=None, retry_auth=True):
            if path == "/playlists/{0}".format(PLAYLIST_ID):
                return playlist_metadata_payload(17), 200
            raise error

        client._get_json_with_status = fake_get_json_with_status
        return await client.get_playlist(PLAYLIST_ID)

    for name, error, error_type in (
        ("playlist 403 is not treated as zero tracks", SpotifyApiError(403), SpotifyApiError),
        ("playlist 429 is not treated as zero tracks", SpotifyRateLimitedError(30), SpotifyRateLimitedError),
        ("playlist timeout is not treated as zero tracks", SpotifyTimeoutError(), SpotifyTimeoutError),
    ):
        try:
            await playlist_for_error(error)
        except error_type:
            results.append(check(name, True))
        except Exception as exc:
            results.append(check(name, False, type(exc).__name__))
        else:
            results.append(check(name, False, "no error"))


async def run_items_endpoint_fallback_checks(results):
    calls = []
    client = SpotifyClient("id", "secret")

    async def fake_get_json_with_status(path, params=None, retry_auth=True):
        calls.append((path, dict(params or {})))
        if path == "/playlists/{0}".format(PLAYLIST_ID):
            return playlist_metadata_payload(
                2,
                embedded_items=[playlist_item_entry(1, field="item"), playlist_item_entry(2, field="item")],
            ), 200
        raise SpotifyApiError(403)

    client._get_json_with_status = fake_get_json_with_status
    playlist = await client.get_playlist(PLAYLIST_ID)
    results.append(check("items endpoint 403 can fall back to embedded playlist tracks", len(playlist.tracks) == 2, str((len(playlist.tracks), calls))))
    results.append(check("embedded fallback still handles item field", playlist.item_field_tracks == 2, str(playlist.item_field_tracks)))


async def run_queue_checks(results):
    guild_id = "guild-playlist"
    clear_music_state(guild_id)
    state = get_music_state(guild_id)
    state.current = MusicTrack("Current", "https://youtube.example/current", "stream", "other")
    fake_client = FakeSpotifyClient(playlist_with_tracks(30, skipped=2))
    original_get_client = voice_music.get_spotify_client
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
        voice_music.get_spotify_client = lambda: fake_client
        voice_music.resolve_spotify_track_to_youtube = fake_resolve
        voice_music.extract_track_info_with_cookie_fallback = fake_extract
        voice_music.play_next_track = fake_play_next

        message = FakeMessage(guild_id)
        link = parse_spotify_link("https://open.spotify.com/playlist/{0}?si=abc".format(PLAYLIST_ID))
        handled = await voice_music.enqueue_spotify_link(message, link, FakeVoiceClient())
        results.append(check("playlist enqueue handled", handled))
        results.append(check("playlist metadata fetched once", fake_client.get_playlist_calls == 1, str(fake_client.get_playlist_calls)))
        results.append(check("playlist queues all tracks lazily", len(state.queue) == 30 and all(track.source_type == "spotify_playlist" for track in state.queue), str(len(state.queue))))
        results.append(check("playlist enqueue does not resolve all youtube upfront", not resolve_calls and not extract_calls, str((resolve_calls, extract_calls))))
        results.append(check("playlist summary sends one message", len(message.channel.messages) == 1 and len(message.channel.embeds) == 1, str(message.channel.messages)))
        results.append(check("playlist queue is guild scoped", not get_music_state("guild-other").queue, str(len(get_music_state("guild-other").queue))))

        first = state.queue[0]
        resolved_first = await voice_music.refresh_track_for_playback(first, guild_id, FakeVoiceClient())
        results.append(check("playlist track resolves at playback", resolved_first is not None and resolved_first.stream_url and resolved_first.source_url.startswith("https://www.youtube.com/watch"), str(resolved_first)))
        results.append(check("lazy resolved track keeps playlist metadata", resolved_first.spotify_playlist_id == PLAYLIST_ID and resolved_first.spotify_playlist_index == 1))
        results.append(check("lazy resolve uses existing youtube path", len(resolve_calls) == 1 and len(extract_calls) == 1, str((resolve_calls, extract_calls))))
        results.append(check("only first track lazy resolves before prefetch", sum(1 for track in state.queue if track.stream_url) == 0, str(sum(1 for track in state.queue if track.stream_url))))

        await voice_music.prefetch_spotify_playlist_tracks(FakeVoiceClient(), guild_id, limit=2)
        queued = list(state.queue)
        prefetched = [track for track in queued[:2] if track.stream_url and track.spotify_resolve_status == "prefetched"]
        results.append(check("playlist prefetch resolves next two", len(prefetched) == 2, str([(track.spotify_playlist_index, track.spotify_resolve_status) for track in queued[:3]])))
        results.append(check("playlist prefetch leaves later tracks pending", queued[2].spotify_resolve_status == "pending" and not queued[2].stream_url, queued[2].spotify_resolve_status))

        empty_client = FakeSpotifyClient(playlist_with_tracks(0))
        voice_music.get_spotify_client = lambda: empty_client
        clear_music_state("guild-empty")
        empty_message = FakeMessage("guild-empty")
        handled_empty = await voice_music.enqueue_spotify_link(empty_message, link, FakeVoiceClient())
        results.append(check("empty playlist handled with message", handled_empty and not get_music_state("guild-empty").queue and empty_message.channel.messages, str(empty_message.channel.messages)))
    finally:
        voice_music.get_spotify_client = original_get_client
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
    await run_client_pagination_checks(results)
    await run_legacy_track_field_checks(results)
    await run_api_error_checks(results)
    await run_items_endpoint_fallback_checks(results)
    await run_queue_checks(results)
    print("spotify playlist support checks: {0}/{1}".format(sum(1 for value in results if value), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
