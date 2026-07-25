class SpotifyPlaylistResolveError(Exception):
    user_message = "このSpotifyプレイリストの曲一覧を取得できませんでした。"


class SpotifyPlaylistProviderUnavailable(SpotifyPlaylistResolveError):
    pass


class SpotifyPlaylistNoTracks(SpotifyPlaylistResolveError):
    pass


class SpotifyPlaylistParseError(SpotifyPlaylistResolveError):
    pass
