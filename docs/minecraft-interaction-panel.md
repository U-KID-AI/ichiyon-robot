# Minecraft Discord panel

Mention the bot with `マイクラ` to open the shared Minecraft root panel,
or use the Minecraft button in the existing main panel (`パネル`).
Existing bare-mention handling is unchanged.

## Flow

1. **プレイヤー操作** fetches `bridge.player_names` from the Control API.
   Select an online player, or use **Minecraft名を入力** to enter a name.
   Manual entry remains available when the API is unavailable or the roster is
   empty. Both paths use the bridge's existing player-name validation.
2. The selected player's private panel offers items/blocks, posters,
   mobs/mannequins, and diagnostics. Each command is a button.
3. **戻る**, **対象変更**, and **閉じる** navigate without another mention.
   Lists paginate at 25 players or 20 command buttons per page.
4. **サーバー状態** and **join履歴** run from the root without a player.
   **サーバー再起動** first opens a private confirmation. Execution still
   checks the existing restart permissions at confirmation time.

## Execution and compatibility

`minecraft_bridge.minecraft_panel_commands()` derives metadata from the same
command registry as the text parser. No second command-to-type allow-list is
maintained in the panel. All 44 player commands and three playerless commands
currently in the bridge are available.

Buttons use the shared `InteractionMessageAdapter` and call the original
`handle_minecraft_command()`. Parser, permissions, queue, Control API, result
messages and timeouts remain shared with text commands. Interaction IDs are
used as the queue's request message ID. Command results are private and disable
mentions. One in-flight command per Discord user/guild limits repeat clicks.

Only the exact `マイクラ` command opens the panel; commands with arguments
continue down the existing Minecraft route, including invalid command usage.
The existing main panel re-exports the shared adapter names for compatibility.

## Lifetime

The shared root has no selected player, has persistent custom IDs scoped by
`BOT_INSTANCE_ID`, and is registered at bot startup. Closing a shared root only
acknowledges the user's dismissal so other members can still use it.
Private selection/action views expire after five minutes; restart confirmation
expires after one minute. They belong to the opener and are not restored after
a bot restart. Open the root again to start a new session.

No migration, Minecraft pack change, BDS change or production configuration is
required. Deployment is outside this PR's scope.

## Verification

`python scripts/check_minecraft_panel.py` runs offline interaction tests through
the real parser, permission checks and queue handler, with DB/Control API calls
mocked. It covers every command, validation, manual fallback, private responses,
confirmation, permission changes, duplicate clicks, paging, component limits,
persistent registration and Discord ViewStore callback replacement.

Existing interaction-panel, message-routing and Minecraft bridge checkers remain
in CI alongside this checker. Real Discord/BDS execution is not performed by
these offline tests.
