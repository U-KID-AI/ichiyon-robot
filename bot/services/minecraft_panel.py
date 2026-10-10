"""Discord UI for the existing Minecraft bridge; no separate execution path."""
from functools import partial

import discord

from bot.services import minecraft_bridge as bridge
from bot.services.interaction_adapter import InteractionMessageAdapter, custom_id


CATEGORIES = {
    "items": "アイテム・ブロック",
    "posters": "ポスター",
    "mobs": "Mob・マネキン",
    "diagnostics": "診断",
}
COMMANDS_PER_PAGE = 20
PLAYERS_PER_PAGE = 25
SESSION_TIMEOUT = 300
_RUNNING_USERS = set()


async def reply(interaction, content):
    await InteractionMessageAdapter(interaction).channel.send(content)


async def defer(interaction, *, replace=False):
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=not replace)


async def show(interaction, content, view, *, replace=False):
    await defer(interaction, replace=replace)
    await interaction.edit_original_response(content=content, view=view, allowed_mentions=discord.AllowedMentions.none())


async def execute(interaction, command, player=None):
    key = (interaction.guild_id, interaction.user.id)
    if key in _RUNNING_USERS:
        await reply(interaction, "Minecraft操作を処理中です。完了までお待ちください。")
        return
    _RUNNING_USERS.add(key)
    try:
        await defer(interaction)
        if command.needs_player and not bridge.is_valid_minecraft_player_name(player):
            await reply(interaction, "Minecraft名は半角英数字・_の1〜16文字で入力してください。")
            return
        text = f"{bridge.MINECRAFT_COMMAND_PREFIX} {command.text}"
        if command.needs_player:
            text += f" {player}"
        adapter = InteractionMessageAdapter(interaction, text)
        adapter.id = interaction.id
        await bridge.handle_minecraft_command(adapter, text)
    finally:
        _RUNNING_USERS.discard(key)


async def choose_player(interaction, *, replace=False):
    await defer(interaction, replace=replace)
    adapter = InteractionMessageAdapter(interaction)
    adapter.id = interaction.id
    try:
        players = await bridge.fetch_online_players(adapter)
        message = "対象プレイヤーを選択してください。" if players else "現在オンラインのプレイヤーはいません。\nMinecraft名を手入力できます。"
    except bridge.OnlinePlayersError:
        players = []
        message = "オンラインプレイヤー一覧を取得できませんでした。\nMinecraft名を手入力できます。"
    await show(interaction, message, PlayerSelectView(interaction.user.id, players), replace=replace)


class ActionButton(discord.ui.Button):
    def __init__(self, label, action, *, key, row=None, style=discord.ButtonStyle.secondary):
        super().__init__(label=label, custom_id=custom_id("mc", key), row=row, style=style)
        self.action = action

    async def callback(self, interaction):
        await self.action(interaction)


class MinecraftPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(ActionButton("プレイヤー操作", choose_player, key="players", row=0, style=discord.ButtonStyle.primary))
        labels = {"server_status": "サーバー状態", "server_restart": "サーバー再起動"}
        for command in bridge.minecraft_panel_commands():
            if command.needs_player:
                continue
            restart = command.command_type == "server_restart"
            action = partial(self.confirm_restart, command=command) if restart else partial(execute, command=command)
            self.add_item(ActionButton(labels.get(command.command_type, command.text), action,
                key=command.command_type, row=1 if restart else 0,
                style=discord.ButtonStyle.danger if restart else discord.ButtonStyle.secondary))
        self.add_item(ActionButton("閉じる", self.close, key="close", row=1))

    async def interaction_check(self, interaction):
        if interaction.guild is None:
            await reply(interaction, "サーバー内で使ってください。")
            return False
        return True

    async def confirm_restart(self, interaction, *, command):
        await show(interaction, "Minecraftサーバーを再起動しますか？",
                   RestartConfirmView(interaction.user.id, command))

    async def close(self, interaction):
        # A shared root message must remain usable by other members.
        if interaction.message.flags.ephemeral:
            await show(interaction, "閉じました。", None, replace=True)
        else:
            await reply(interaction, "閉じました。")


class SessionView(discord.ui.View):
    def __init__(self, owner_id, *, timeout=SESSION_TIMEOUT):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id

    async def interaction_check(self, interaction):
        if interaction.guild is None or interaction.user.id != self.owner_id:
            await reply(interaction, "この操作パネルを開いたユーザーが操作してください。")
            return False
        return True

    async def replace(self, interaction, content, view):
        # Unregister the old callbacks before Discord stores the replacement view.
        self.stop()
        await show(interaction, content, view, replace=True)

    async def close(self, interaction):
        await self.replace(interaction, "閉じました。", None)


class PlayerNameModal(discord.ui.Modal, title="Minecraft名を入力"):
    player = discord.ui.TextInput(label="Minecraft名", min_length=1, max_length=16)

    def __init__(self, owner_id):
        super().__init__(timeout=SESSION_TIMEOUT)
        self.owner_id = owner_id

    async def on_submit(self, interaction):
        await defer(interaction)
        if interaction.user.id != self.owner_id or interaction.guild is None:
            await reply(interaction, "この操作パネルを開いたユーザーが操作してください。")
            return
        player = str(self.player).strip()
        if not bridge.is_valid_minecraft_player_name(player):
            await reply(interaction, "Minecraft名は半角英数字・_の1〜16文字で入力してください。")
            return
        view = PlayerPanelView(self.owner_id, player)
        await show(interaction, view.content, view)


class PlayerSelect(discord.ui.Select):
    def __init__(self, names):
        super().__init__(placeholder="対象プレイヤーを選択", custom_id=custom_id("mc", "player"), row=0,
                         options=[discord.SelectOption(label=name, value=name) for name in names])

    async def callback(self, interaction):
        player = self.values[0]
        if player not in self.view.players or not bridge.is_valid_minecraft_player_name(player):
            await reply(interaction, "対象プレイヤーを選び直してください。")
            return
        view = PlayerPanelView(self.view.owner_id, player)
        await self.view.replace(interaction, view.content, view)


class PlayerSelectView(SessionView):
    def __init__(self, owner_id, players, page=0):
        super().__init__(owner_id)
        self.players = players
        if players:
            self.add_item(PlayerSelect(players[page * PLAYERS_PER_PAGE:(page + 1) * PLAYERS_PER_PAGE]))
        if page:
            self.add_item(ActionButton("前へ", partial(self.turn_page, page=page - 1), key="players_prev", row=1))
        if (page + 1) * PLAYERS_PER_PAGE < len(players):
            self.add_item(ActionButton("次へ", partial(self.turn_page, page=page + 1), key="players_next", row=1))
        self.add_item(ActionButton("Minecraft名を入力", self.manual, key="manual", row=2))
        self.add_item(ActionButton("戻る", self.back, key="root", row=2))
        self.add_item(ActionButton("閉じる", self.close, key="close", row=2))

    async def manual(self, interaction):
        # Opening a modal is itself the immediate interaction acknowledgement.
        await interaction.response.send_modal(PlayerNameModal(self.owner_id))

    async def back(self, interaction):
        await self.replace(interaction, "Minecraft操作", MinecraftPanelView())

    async def turn_page(self, interaction, *, page):
        await self.replace(interaction, "対象プレイヤーを選択してください。",
                           PlayerSelectView(self.owner_id, self.players, page))


class PlayerPanelView(SessionView):
    def __init__(self, owner_id, player, category=None, page=0):
        super().__init__(owner_id)
        self.player = player
        self.category = category
        self.content = f"Minecraft操作\n対象: {player}"
        if category is None:
            for index, (key, label) in enumerate(CATEGORIES.items()):
                self.add_item(ActionButton(label, partial(self.open_category, category=key), key=key, row=index // 2))
        else:
            commands = [c for c in bridge.minecraft_panel_commands() if c.category == category]
            self.content += f"\n{CATEGORIES[category]} ({page + 1}/{max(1, (len(commands) + COMMANDS_PER_PAGE - 1) // COMMANDS_PER_PAGE)})"
            for index, command in enumerate(commands[page * COMMANDS_PER_PAGE:(page + 1) * COMMANDS_PER_PAGE]):
                self.add_item(ActionButton(command.text, partial(execute, command=command, player=player),
                                          key=command.command_type, row=index // 5))
            if page:
                self.add_item(ActionButton("前へ", partial(self.open_category, category=category, page=page - 1), key="prev", row=4))
            if (page + 1) * COMMANDS_PER_PAGE < len(commands):
                self.add_item(ActionButton("次へ", partial(self.open_category, category=category, page=page + 1), key="next", row=4))
            self.add_item(ActionButton("戻る", partial(self.open_category, category=None), key="back", row=4))
        self.add_item(ActionButton("対象変更", self.change_player, key="players", row=4))
        self.add_item(ActionButton("閉じる", self.close, key="close", row=4))

    async def open_category(self, interaction, *, category, page=0):
        view = PlayerPanelView(self.owner_id, self.player, category, page)
        await self.replace(interaction, view.content, view)

    async def change_player(self, interaction):
        self.stop()
        await choose_player(interaction, replace=True)


class RestartConfirmView(SessionView):
    def __init__(self, owner_id, command):
        super().__init__(owner_id, timeout=60)
        self.command = command
        self.confirmed = False
        self.add_item(ActionButton("再起動する", self.confirm, key="confirm_restart", style=discord.ButtonStyle.danger))
        self.add_item(ActionButton("キャンセル", self.close, key="cancel"))

    async def confirm(self, interaction):
        if self.confirmed:
            await reply(interaction, "再起動の確認は処理済みです。")
            return
        self.confirmed = True
        await self.replace(interaction, "再起動を要求しました。", None)
        # Authorization, restart, progress, errors and completion stay in the bridge.
        await execute(interaction, self.command)
