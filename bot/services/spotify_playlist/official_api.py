from bot.services.spotify_client import SpotifyClient, SpotifyPlaylistMetadata


async def fetch_official_playlist(client: SpotifyClient, playlist_id: str) -> SpotifyPlaylistMetadata:
    return await client.get_playlist(playlist_id)
