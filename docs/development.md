# ローカル開発環境

v3.0 前準備として、ローカルで `db` / `admin` / `bot` を Docker Compose で動かすための手順です。本番・stg の systemd 運用は変更しません。

## 事前準備

`.env.example` を `.env` にコピーし、必要な値だけ変更します。

```powershell
Copy-Item .env.example .env
```

`.env` は Git 管理外です。Bot Token、Discord OAuth Client Secret、X Bearer Token は `.env` にだけ入れます。

## サービス構成

- `db`: PostgreSQL 16
- `admin`: `uvicorn admin.main:app`
- `bot`: `python main.py`

`admin` と `bot` は同じ PostgreSQL を見ます。Compose 内では `DATABASE_URL_DOCKER` の `db:5432` を使います。ホストから migration や psql を実行する場合は、`.env.example` の `DATABASE_URL` のように `localhost:5432` を使います。

既存の PostgreSQL や古い Compose コンテナが 5432 を使っている場合は、`.env` の `POSTGRES_PORT` とホスト用 `DATABASE_URL` のポートを変更します。

将来の `voice-worker` / `minecraft-bridge` / `ml-worker` は、同じ Compose にサービスを足す前提です。

## 起動

まず DB を起動します。

```powershell
docker compose up -d db
```

管理画面を起動します。

```powershell
docker compose up -d admin
```

管理画面:

```text
http://localhost:8080
```

ログ確認:

```powershell
docker compose logs admin
```

## DB 初期化

コンテナ内から migration を実行します。

```powershell
docker compose exec admin python scripts/migrate.py
```

プリセットを入れる例:

```powershell
docker compose exec admin python scripts/seed_v2_presets.py --guild-id 111 --guild-name ローカル開発
```

既存 JSON を DB へ移行する場合:

```powershell
docker compose exec admin python scripts/migrate_json_to_db.py --guild-id 111 --dry-run
```

dry-run で確認してから `--dry-run` を外します。

## Bot 起動

Bot は Discord Token が必要です。`.env` に以下のどちらかを設定します。

```text
DISCORD_TOKEN=...
```

または:

```text
DISCORD_BOT_TOKEN=...
```

Bot は profile 指定で起動します。

```powershell
docker compose --profile bot up -d bot
```

Token が未設定の場合、Bot コンテナは起動に失敗して構いません。admin と DB の確認には不要です。

## 確認コマンド

Compose 設定確認:

```powershell
docker compose config
```

`docker compose config` は環境変数の値も表示します。出力を共有する時は、実 `.env` ではなく以下のようにダミー値で確認します。

```powershell
docker compose --env-file .env.example config
```

コンテナ内の軽い確認:

```powershell
docker compose exec admin python -m compileall bot admin scripts
docker compose exec admin python scripts/check_modes_runtime.py
docker compose exec admin python scripts/check_special_effects_runtime.py
docker compose exec admin python scripts/check_deck_search_runtime.py
docker compose exec admin python scripts/check_mention_guard_runtime.py
docker compose exec admin python scripts/check_reaction_threshold_runtime.py
```

DB テーブル確認:

```powershell
docker compose exec db psql -U ichiyon_robot -d ichiyon_robot -c "\dt"
```

## 注意

- `.env`、Token、Client Secret、SSH 鍵、本番 DB 情報はコミットしません。
- `data/*.json` は互換用・移行元として残します。
- X 検索は `X_SEARCH_ENABLED=false` がローカル既定です。
- 本番 Docker 化はこの手順の対象外です。
