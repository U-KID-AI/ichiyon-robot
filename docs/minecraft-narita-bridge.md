# Minecraft Narita Carpet Bridge

Discord commands:

```text
@いちよんロボ マイクラ 成田カーペット Player45165996
@いちよんロボ マイクラ ストラクチャーブロック Player45165996
@いちよんロボ マイクラ コマンドブロック Player45165996
```

The last argument is the Minecraft player name. The bot validates it, enqueues one of the structured command
types below, and waits for the Minecraft behavior pack to report success or failure. The bot never accepts a raw
Minecraft command or arbitrary item ID from Discord.

Structured command types:

- `narita_carpet`
- `structure_block`
- `command_block`

## Player name validation

Minecraft player names must match `^[A-Za-z0-9_]{1,16}$`. Discord user to Minecraft user mapping is not used by
this command.

The migration includes `minecraft_player_links` for a possible future mapping flow, but this command path does
not read it.

## Command queue

```sql
INSERT INTO minecraft_command_queue (
    request_id, bot_id, guild_id, discord_channel_id, discord_message_id,
    requester_discord_user_id, target_discord_user_id,
    command_type, minecraft_player_name
)
VALUES (
    gen_random_uuid(), 'ichiyon', '<discord guild id>', '<channel id>', '<message id>',
    '<requester discord user id>', '',
    'narita_carpet', 'Player45165996'
);
```

Existing databases that already applied migration 047 need migration 048 before `structure_block` or
`command_block` can be enqueued.

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
The block commands use the player inventory container and add exactly one `minecraft:structure_block` or
`minecraft:command_block`; if the inventory cannot accept the item, the bridge reports `inventory_full`.

## Script API versions

- `@minecraft/server`: `2.10.0-beta`
- `@minecraft/server-net`: `1.0.0-beta`
- `@minecraft/server-admin`: `1.0.0-beta`

These correspond to the available `1.26.44-stable` npm packages and are intended for the current BDS 1.26.45.1
environment.

## Activation hold point

Do not activate this bridge until the creative world is backed up and Beta APIs are enabled deliberately. The
current world was observed with `experiments_ever_used=0` and `saved_with_toggled_experiments=0`.
