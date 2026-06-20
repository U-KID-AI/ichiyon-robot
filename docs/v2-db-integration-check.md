# ver2.0 DB Integration Check

この手順は、ver2.0 の DB backend がローカルまたはいちよんラボ相当で一通りつながっているか確認するためのものです。

本番切り替え手順ではありません。`main` 相当の本番環境ではまだ `ICHIYON_DATA_BACKEND=db` に切り替えません。

## 確認する範囲

- PostgreSQL migration
- `scripts/seed_v2_presets.py`
- `scripts/migrate_json_to_db.py`
- DB backend runtime
- 管理画面DBページで使う主要テーブル

`scripts/check_v2_db_integration.py` はBot TokenやDiscord APIを使いません。フェイクmessageでDB runtime関数を呼び出します。

## ローカルDB手順

1. PostgreSQLを起動します。

```powershell
docker compose up -d postgres
```

2. migrationを適用します。

```powershell
python scripts/migrate.py `
  --database-url postgresql://ichiyon_robot:ichiyon_robot_password@localhost:5432/ichiyon_robot
```

3. 初期プリセットを投入します。

```powershell
python scripts/seed_v2_presets.py `
  --database-url postgresql://ichiyon_robot:ichiyon_robot_password@localhost:5432/ichiyon_robot `
  --guild-id 111 `
  --guild-name テストギルド
```

4. 既存JSONデータをDBへ移行する場合は、プリセット後に実行します。

```powershell
python scripts/migrate_json_to_db.py `
  --database-url postgresql://ichiyon_robot:ichiyon_robot_password@localhost:5432/ichiyon_robot `
  --guild-id 111 `
  --guild-name テストギルド `
  --dry-run
```

dry-runで件数を見て問題なければ、`--dry-run` を外して実行します。

5. 統合確認を実行します。

```powershell
python scripts/check_v2_db_integration.py `
  --database-url postgresql://ichiyon_robot:ichiyon_robot_password@localhost:5432/ichiyon_robot `
  --guild-id 111
```

guild行だけをテスト用に作りたい場合は `--upsert-guild` を使えます。ただしプリセット確認は `seed_v2_presets.py` 実行済みであることを前提にします。

## check_v2_db_integration.py が見るもの

主要プリセット:

- はゆすモード
- 成田モード
- しこっちモード
- ミニいちよん
- 成田カウント加算
- しこっち抽選
- お前も〇〇よな？候補へのミニいちよん付与
- 自動反応「しこっち」へのしこっち抽選付与

runtime:

- feature_flags OFF/ON判定
- メンション反応
- 自動反応
- NGワード停止
- 特殊効果 `counter_delta`
- 特殊効果 `counter_set`
- `counter_threshold` によるモード突入
- replyモード
- offlineモード

確認スクリプトは `integration_check_*` の検証用データを指定 `guild_id` に作ります。再実行しても同じキーを使うため重複しません。

feature_flags、既存modeのenabled、mode_statesは確認中に一時変更し、最後に元へ戻します。検証中に中断した場合は、管理画面かSQLで状態を確認してください。

## DB backendでBotを起動する場合

ローカルまたはいちよんラボ相当でだけ、以下のようにDB backendへ切り替えます。

```powershell
$env:ICHIYON_DATA_BACKEND = "db"
$env:DATABASE_URL = "postgresql://ichiyon_robot:ichiyon_robot_password@localhost:5432/ichiyon_robot"
python main.py
```

未設定、または `ICHIYON_DATA_BACKEND=json` の場合は既存JSON運用のままです。

## 管理画面DBページ

管理画面を起動し、OAuthログイン後に対象サーバーを選び、以下のページを確認します。

- `/guilds/{guild_id}`
- `/guilds/{guild_id}/mention-reactions`
- `/guilds/{guild_id}/auto-reactions`
- `/guilds/{guild_id}/ng-words`
- `/guilds/{guild_id}/special-effects`
- `/guilds/{guild_id}/modes`
- `/guilds/{guild_id}/auto-posts`

既存の旧JSON編集画面はまだDB運用へ切り替えません。

## 検証順

まずローカルDBかいちよんラボで検証します。

1. migration
2. seed presets
3. 必要ならJSON migration
4. integration check
5. `ICHIYON_DATA_BACKEND=db` で短時間のBot動作確認
6. 管理画面DBページ確認

いちよんラボで問題がないことを確認してから、ランセ地方の検証へ進みます。レストランや本番相当への切り替えは、このPhaseでは行いません。

## 注意

この手順にBot Token、OAuth Client Secret、秘密鍵は不要です。`.env`、本番データ、`data/backups` は触りません。

`guild_id` は検証用を指定してください。確認スクリプトは検証用データを削除しないため、本番サーバーの `guild_id` では実行しないでください。
