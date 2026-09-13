# Minecraft Narita Carpet Bridge

Discord command:

```text
@いちよんロボ マイクラ 成田カーペット @対象ユーザー
```

The bot resolves the mentioned Discord user ID to a registered Minecraft player name, enqueues a structured
`narita_carpet` command, and waits for the Minecraft behavior pack to report success or failure. The bot never
accepts a raw Minecraft command from Discord.

## Required database row

Register Discord user IDs explicitly. Do not infer Minecraft names from Discord display names.

```sql
INSERT INTO minecraft_player_links (
    bot_id, guild_id, discord_user_id, minecraft_player_name
)
VALUES (
    'ichiyon', '<discord guild id>', '<discord user id>', 'Player45165996'
)
ON CONFLICT (bot_id, guild_id, discord_user_id) DO UPDATE
SET minecraft_player_name = EXCLUDED.minecraft_player_name,
    updated_at = NOW();
```

Minecraft player names must match `^[A-Za-z0-9_]{1,16}$`.

## Behavior pack files

Prepared files:

- `minecraft/behavior_packs/import_structures/manifest.json`
- `minecraft/behavior_packs/import_structures/scripts/main.js`
- `minecraft/config/2fbc1c02-0c4d-4e98-a851-c1e41337c7a8/permissions.json`
- `minecraft/config/2fbc1c02-0c4d-4e98-a851-c1e41337c7a8/variables.json`
- `minecraft/config/2fbc1c02-0c4d-4e98-a851-c1e41337c7a8/secrets.json.example`

The module-specific config keeps `config/default/permissions.json` minimal. HTTP is allowed only to the private
OCI bot/admin API because the Minecraft server reaches the bot over the private network.

The behavior pack command always loads `mystructure:narita_map_item`. On the Minecraft host, prepare that ID as
`data/behavior_packs/import_structures/structures/mystructure/narita_map_item.mcstructure` before activation.

## Script API versions

- `@minecraft/server`: `2.10.0-beta`
- `@minecraft/server-net`: `1.0.0-beta`
- `@minecraft/server-admin`: `1.0.0-beta`

These correspond to the available `1.26.44-stable` npm packages and are intended for the current BDS 1.26.45.1
environment.

## Activation hold point

Do not activate this bridge until the creative world is backed up and Beta APIs are enabled deliberately. The
current world was observed with `experiments_ever_used=0` and `saved_with_toggled_experiments=0`.
