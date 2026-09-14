# Minecraft Narita Carpet Bridge

Discord commands:

```text
@いちよんロボ マイクラ 成田カーペット Player45165996
@いちよんロボ マイクラ ストラクチャーブロック Player45165996
@いちよんロボ マイクラ コマンドブロック Player45165996
@いちよんロボ マイクラ バリアブロック Player45165996
@いちよんロボ マイクラ ライトブロック Player45165996
@いちよんロボ マイクラ ジグソーブロック Player45165996
@いちよんロボ マイクラ ストラクチャーヴォイド Player45165996
@いちよんロボ マイクラ リピートコマンドブロック Player45165996
@いちよんロボ マイクラ チェーンコマンドブロック Player45165996
@いちよんロボ マイクラ タケツミエッグ Player45165996
@いちよんロボ マイクラ 手持ち確認 Player45165996
@いちよんロボ マイクラ 状態
```

The last argument is the Minecraft player name. The bot validates it, enqueues one of the structured command
types below, and waits for the Minecraft behavior pack to report success or failure. The bot never accepts a raw
Minecraft command or arbitrary item ID from Discord.

Structured command types:

- `narita_carpet`
- `structure_block`
- `command_block`
- `barrier_block`
- `light_block`
- `jigsaw_block`
- `structure_void`
- `repeating_command_block`
- `chain_command_block`
- `taketumi_spawn_egg`
- `held_item_inspect`
- `server_status`

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
`command_block` can be enqueued. Migration 049 extends the same non-destructive check constraint for the
additional utility block commands.

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
Most block commands use the player inventory container and add exactly one allow-listed item:
`minecraft:structure_block`, `minecraft:command_block`, `minecraft:barrier`, `minecraft:jigsaw`,
`minecraft:structure_void`, `minecraft:repeating_command_block`, `minecraft:chain_command_block`, or
`ichiyon:taketumi_spawn_egg`.
If the inventory cannot accept the item, the bridge reports `inventory_full`.

The light block command grants `minecraft:light_block_15` through the same inventory `ItemStack` path as the
other allow-listed items.

`タケツミエッグ` is a custom item in `ichiyon_avatar_bp/items/taketumi_spawn_egg.json`. It uses
`minecraft:entity_placer` for `ichiyon:taketumi`; the resource pack registers its inventory icon in
`ichiyon_avatar_rp/textures/item_texture.json`.

`Eの聖文字` is intentionally not enqueued yet. The active live packs and repository packs do not contain a custom
item definition, item texture entry, or language entry for that display name, so the bot consumes the Minecraft
command and returns a clear unavailable message instead of guessing an item identifier or creating a second item.

`手持ち確認` is a read-only diagnostic command for identifying an item already held by a player. The behavior pack
reads the player's selected hotbar slot and returns the available Script API fields: slot, `typeId`, amount,
`nameTag`, lore, component type IDs, dynamic property IDs and values, dynamic property byte count, `lockMode`,
`keepOnDeath`, stackability, max stack size, weight, can-destroy/can-place-on lists, and tags. Internal item NBT
and map internals are not exposed by the Script API, so the response explicitly says they are unavailable.

`状態` is a read-only bridge status command. A successful response means the behavior pack is polling and the
Minecraft side is online. It returns the current player count, player names, script uptime, and the configured BDS
version when `MINECRAFT_BDS_VERSION` is set. Host CPU, host memory, and Docker state are not collected by this
behavior pack because the Minecraft Script API cannot read host or Docker metrics.

For production operations, configure the bot to call the fixed Minecraft host control API instead:

```text
MINECRAFT_CONTROL_API_BASE=http://10.0.0.62:8099
MINECRAFT_CONTROL_API_SECRET=<long random secret>
MINECRAFT_CONTROL_TIMEOUT_SECONDS=10
MINECRAFT_RESTART_TIMEOUT_SECONDS=240
MINECRAFT_RESTART_ALLOWED_USER_IDS=<comma separated Discord user IDs allowed to restart>
```

When the control API is configured, `マイクラ 状態` first reads host/Docker state from the control API and then
briefly probes NaritaBridge for in-game player names. This means it can still answer when BDS or NaritaBridge is
offline. The control API reports container state, health, restart count, container start time, container uptime,
BDS version from Docker env or the installed binary name, host CPU, host memory, and container CPU/memory from
Docker stats.

`マイクラ 再起動` is never sent to NaritaBridge. The bot only calls the fixed control API restart endpoint, and only
for users allowed by `MINECRAFT_RESTART_ALLOWED_USER_IDS`, `DEVELOPER_USER_ID`, or the DB `global_admin` role.

The Minecraft host control API source is `scripts/minecraft/minecraft_control_api.py`. It exposes only:

- `GET /status`
- `POST /restart`

It does not accept arbitrary shell, Docker, SSH, or Minecraft commands. The restart flow is fixed:

1. read current status
2. if the container is running, gracefully stop it with `docker compose stop -t 60 bedrock-creative`
3. create a timestamped backup of `docker-compose.yml`, the active world, active pack directories, and world pack refs
4. optionally sync repository-managed packs from `MINECRAFT_CONTROL_PACK_SOURCE_DIR`
5. `docker compose up -d bedrock-creative`
6. wait for Docker health or Bedrock status to return
7. prune old restart backups according to `MINECRAFT_CONTROL_BACKUP_RETENTION`
8. return post-restart status

Pack sync is skipped unless `MINECRAFT_CONTROL_PACK_SOURCE_DIR` points to a local copy of the repo `minecraft/`
directory on the Minecraft host. If present, it compares source and live pack contents while ignoring manifest
version numbers. Only packs with actual content changes are copied, only those packs have their manifest patch
version incremented, and existing UUIDs are preserved. The matching `world_behavior_packs.json` or
`world_resource_packs.json` entry is updated to the new version.

The current Minecraft host does not have an `ichiyon-robot` git clone or a separate pack source directory. The
live packs are currently only under `/home/ubuntu/minecraft-bedrock-creative/data/...`. For automatic pack sync,
deploy the repository `minecraft/` directory separately to a host path such as
`/home/ubuntu/ichiyon-robot-minecraft-packs/minecraft`, then point `MINECRAFT_CONTROL_PACK_SOURCE_DIR` there.
`マイクラ 再起動` must not run `git pull`; code/pack source deployment and BDS restart stay separate.

## Minecraft Control API Deployment

The recommended production resident process is a host-level systemd service, not a process inside the BDS
container. This keeps `マイクラ 状態` and `マイクラ 再起動` available even when `minecraft-bedrock-creative` is stopped.
Use the templates:

- `scripts/minecraft/minecraft-control-api.env.example`
- `scripts/minecraft/minecraft-control-api.service.example`

On the current host, bind the API to the private VCN address `10.0.0.62`, not `0.0.0.0`. The bot should call it
through the private network, for example `http://10.0.0.62:8099`. Do not open the control API to the public
Internet. Add a host firewall or OCI rule that allows TCP 8099 only from the bot host private IP `10.0.0.94`.
The API also requires `X-Minecraft-Control-Secret` for both `GET /status` and `POST /restart`.

The current host-level firewall has UFW inactive and nftables rejects inbound TCP except SSH, while Docker opens
the Bedrock UDP ports. Therefore TCP 8099 will require a deliberate private-only allow rule before the bot can
reach the control API. No public ingress rule should be added.

The current image includes `send-command`, but on the live container it failed while searching for the Bedrock
process. For this reason `MINECRAFT_CONTROL_USE_SAVE_HOLD` defaults to false. The safe default world save method
is Docker's graceful stop (`docker compose stop -t 60`), followed by a backup while the server is stopped. Enable
`save hold/query/resume` only after it is confirmed working on the live container.

## Script API versions

- `@minecraft/server`: `2.10.0-beta`
- `@minecraft/server-net`: `1.0.0-beta`
- `@minecraft/server-admin`: `1.0.0-beta`

These correspond to the available `1.26.44-stable` npm packages and are intended for the current BDS 1.26.45.1
environment.

## Activation hold point

Do not activate this bridge until the creative world is backed up and Beta APIs are enabled deliberately. The
current world was observed with `experiments_ever_used=0` and `saved_with_toggled_experiments=0`.
