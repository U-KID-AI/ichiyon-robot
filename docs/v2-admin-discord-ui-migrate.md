# ver2.0 Admin DB UI Migration

このPhaseで、管理画面の通常導線を ver2.0 DB管理画面へ一本化しました。

## 管理画面の通常導線

管理画面は以下の流れを正とします。

1. `/login`
2. `/servers`
3. `/guilds/{guild_id}`
4. `/guilds/{guild_id}/...` の各DB管理画面

トップ `/` は、未ログインなら `/login`、ログイン済みなら `/servers` へ誘導します。旧JSON画面へは誘導しません。

## 旧JSON画面の扱い

通常状態では以下の旧JSON管理画面を表示しません。

- `/quotes`
- `/reactions`
- `/ng-words`
- `/kuji`

未設定または `ADMIN_ENABLE_LEGACY_JSON_PAGES=false` の場合、これらのURLへ直接アクセスしても `/servers` へリダイレクトします。

緊急のローカル開発確認で旧画面が必要な場合のみ、以下を設定すると表示できます。

```powershell
$env:ADMIN_ENABLE_LEGACY_JSON_PAGES = "true"
```

このフラグは互換確認用です。通常運用では使いません。

## JSONファイルの扱い

`data/*.json` と `assets/images/*` は削除しません。

- 旧JSON管理画面は通常導線から撤去
- JSONファイルは移行元バックアップ扱い
- Botの `ICHIYON_DATA_BACKEND=json` は互換用に残す
- 本番移行完了後、別作業でアーカイブまたは削除を判断する

管理画面ではDBのデータだけを編集する前提です。

## 移行確認の順番

ローカル/いちよんラボ相当では、以下の順で確認します。

1. migration

```powershell
python scripts/migrate.py `
  --database-url postgresql://ichiyon_robot:ichiyon_robot_password@localhost:5432/ichiyon_robot
```

2. 既存JSONデータのDB移行

```powershell
python scripts/migrate_json_to_db.py `
  --database-url postgresql://ichiyon_robot:ichiyon_robot_password@localhost:5432/ichiyon_robot `
  --guild-id 1515983621461245972 `
  --guild-name いちよんラボ
```

3. ver2.0プリセット投入

```powershell
python scripts/seed_v2_presets.py `
  --database-url postgresql://ichiyon_robot:ichiyon_robot_password@localhost:5432/ichiyon_robot `
  --guild-id 1515983621461245972 `
  --guild-name いちよんラボ
```

4. DB統合確認

```powershell
python scripts/check_v2_db_integration.py `
  --database-url postgresql://ichiyon_robot:ichiyon_robot_password@localhost:5432/ichiyon_robot `
  --guild-id 1515983621461245972
```

既にプリセットやJSON移行済みの場合は、再実行安全なスクリプトのため重複作成せずskipされます。

## Discord風UI

管理画面はDiscordの設定画面に寄せたダークテーマへ変更しました。

- 背景: `#313338`
- ヘッダー/サイド相当: `#1e1f22` / `#2b2d31`
- カード: `#383a40`
- 入力: `#1e1f22`
- 文字: `#f2f3f5`
- サブ文字: `#b5bac1`
- 通常ボタン: `#5865F2`
- 危険ボタン: `#DA373C`

ヘッダーから旧JSON画面リンクを削除し、`ver2.0 DB管理画面`、サーバー選択、機能一覧、ログイン/ログアウトを中心にしました。

## 本番注意

このPhaseでは本番切り替えをしません。

Bot実行側の `ICHIYON_DATA_BACKEND=json` は壊さず残します。ローカルまたはいちよんラボで検証してからランセ地方へ進み、本番相当の `main` は別Phaseで切り替え判断します。

`.env`、Bot Token、Discord OAuth Client Secret、秘密鍵、本番データ、`data/backups` は触りません。
