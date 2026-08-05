# Interaction Panel, Audio Assets, and Game Library

## Discord mention panel

Sending only a bot mention opens the operation panel:

```text
@Bot
```

The panel provides:

- Music controls: join VC, pause, resume, skip, stop, now playing, queue, loop, shuffle, volume, and add URL.
- Audio / SE controls: choose a category, choose an audio asset, play it in VC, and stop foreground audio.
- Game controls: Steam search, owned list, wishlist, backlog, and recent searches.

Buttons are intentionally free-operation controls. Any member can press them. Existing command handlers and bot/guild scoped runtime state are reused.

## Audio assets

Managed audio files are stored on the host filesystem, not in PostgreSQL.

Default container path:

```text
/app/data/audio-assets
```

Default host path under Compose:

```text
./data/audio-assets
```

Supported file extensions:

- `.mp3`
- `.wav`
- `.ogg`
- `.m4a`

Validation uses `ffprobe`, not only the extension. Invalid files, path traversal, oversized files, and overly long files are rejected.

Environment:

```text
AUDIO_ASSETS_DIR=/app/data/audio-assets
AUDIO_ASSET_MAX_BYTES=20971520
AUDIO_ASSET_MAX_DURATION_SECONDS=300
```

Audio assets can be enabled/disabled from the admin UI. They are not physically deleted by the UI.

## Special effects

Special effects support:

```json
{
  "audio_asset_id": 1,
  "volume_percent": 50,
  "foreground": true
}
```

The admin form exposes these fields without requiring manual JSON editing. Unknown JSON keys are preserved.

When the effect fires, the sound plays only if the bot is connected to VC. It uses the foreground mixer and does not stop the music queue.

## Games

The game panel is an MVP for Steam / PC games.

It stores:

- Steam app id
- title
- current price
- regular price
- discount
- sale status via discount value
- release date
- platforms
- store URL
- fetched time

Historical low is displayed as unavailable until a terms-safe provider is configured in a later phase.

No Spotify, Steam, or Discord secrets are required for the game panel. Steam current-price lookup uses the public Steam Store API through the shared external HTTP layer.

## Migration

Apply:

```text
migrations/039_add_audio_assets.sql
migrations/040_add_games.sql
```

These migrations add:

- `audio_assets`
- `games`
- `user_game_entries`
- `game_search_history`
- `audio_asset` to the existing special effect type check

It does not drop tables, truncate tables, or rewrite existing data.
