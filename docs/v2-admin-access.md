# ver2.0 Admin Access Grant

`scripts/grant_admin_access.py` は、ローカル/検証DBでDiscordユーザーに管理画面権限を付与するための補助スクリプトです。

Bot Token、Discord OAuth Client Secret、秘密鍵は不要です。`.env`、本番データ、`data/backups` は触りません。

## 使い方

```powershell
python scripts/grant_admin_access.py `
  --database-url postgresql://ichiyon_robot:ichiyon_robot_password@localhost:5432/ichiyon_robot `
  --discord-user-id 835109546640932875 `
  --guild-id 1515983621461245972 `
  --role guild_admin
```

dry-runではDBへ書き込みません。

```powershell
python scripts/grant_admin_access.py `
  --database-url postgresql://ichiyon_robot:ichiyon_robot_password@localhost:5432/ichiyon_robot `
  --discord-user-id 835109546640932875 `
  --guild-id 1515983621461245972 `
  --role guild_admin `
  --dry-run
```

## role

指定できるroleは以下です。

- `global_admin`
- `guild_admin`
- `editor`
- `viewer`

`global_admin` は `admin_users` に保存され、`/servers` ではDB上の全guildが表示対象になります。

`guild_admin`、`editor`、`viewer` は `guild_permissions` に保存され、指定した `guild_id` のみが `/servers` に表示されます。

## 事前条件

指定する `guild_id` は `guilds` テーブルに存在している必要があります。存在しない場合、スクリプトは分かりやすいエラーで停止します。

検証用guildは先に以下のどちらかで作成してください。

- `scripts/seed_v2_presets.py`
- Bot/管理画面のguild登録処理

例:

```powershell
python scripts/seed_v2_presets.py `
  --database-url postgresql://ichiyon_robot:ichiyon_robot_password@localhost:5432/ichiyon_robot `
  --guild-id 1515983621461245972 `
  --guild-name いちよんラボ
```

## 再実行安全性

同じ権限が既に付与されている場合は `skipped` になります。

同じユーザーに別roleを指定した場合は、既存行を指定roleへ更新します。これはローカル/検証で権限確認をやり直しやすくするためです。

## `/servers` 表示

Discord OAuthログイン後、セッションのDiscord user idが `--discord-user-id` と一致していれば、`/servers` に対象サーバーが表示される想定です。

- `global_admin`: 全guild
- `guild_admin`: 指定guild、管理者操作可
- `editor`: 指定guild、通常編集可
- `viewer`: 指定guild、閲覧のみ

OAuthのClient ID/SecretやBot Tokenは、この権限付与スクリプトでは使いません。

## 注意

このスクリプトは本番切り替え用ではありません。まずローカルDBまたはいちよんラボ相当で検証し、ランセ地方以降へ進む前に付与対象の `discord_user_id` と `guild_id` を必ず確認してください。
