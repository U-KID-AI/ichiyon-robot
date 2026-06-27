# stg Docker Compose 切替手順

stg の `db` / `admin` / `bot` を Docker Compose で起動するための手順です。本番は対象外です。

対象:

- host: `141.147.145.113`
- project: `/home/ubuntu/ichiyon-robot`
- branch: `feature/docker-dev-environment`
- admin URL: `http://141.147.145.113:8080/login`

## 方針

stg では既存 PostgreSQL を必ず dump してから、Compose の `db` へ restore します。これで `db` / `admin` / `bot` を Compose 管理へ寄せられます。既存 systemd unit は削除せず、Docker 起動確認後に停止・無効化します。

DB は `127.0.0.1:${POSTGRES_PORT:-5432}` だけに bind します。外部公開しません。

## 事前確認

```bash
cd /home/ubuntu/ichiyon-robot
git fetch origin
git switch feature/docker-dev-environment
git pull origin feature/docker-dev-environment
git status --short
```

既存 unit とポートを確認します。

```bash
systemctl status ichiyon-bot-stg --no-pager
systemctl status ichiyon-admin-stg --no-pager
ss -ltnp | grep -E ':5432|:8080'
```

## stg 用 .env

`.env.stg.example` を元に、サーバー上だけで `.env` を作ります。

```bash
cp .env.stg.example .env
chmod 600 .env
nano .env
```

`.env` には stg の実値を入れます。Token、Client Secret、X Bearer Token、DB password は Git に入れません。

## DB バックアップ

バックアップ先を作ります。

```bash
mkdir -p /home/ubuntu/ichiyon-db-backups
chmod 700 /home/ubuntu/ichiyon-db-backups
```

既存 stg の `DATABASE_URL` が systemd の EnvironmentFile にある場合は、それを使って dump します。値は表示しません。

```bash
set -a
. /home/ubuntu/ichiyon-robot/.env
set +a
pg_dump "$DATABASE_URL" --format=custom --file="/home/ubuntu/ichiyon-db-backups/ichiyon_stg_$(date +%Y%m%d_%H%M%S).dump"
ls -lh /home/ubuntu/ichiyon-db-backups
```

`DATABASE_URL` が Docker 用に書き換わっている場合は、既存 systemd の EnvironmentFile から旧 DB 接続文字列を確認して、同じ形式で `pg_dump` します。秘密値は画面共有しません。

## Compose 設定確認

```bash
docker compose -f docker-compose.yml -f docker-compose.stg.yml config
```

`config` は環境変数の値も出ます。出力を共有する場合は秘密値を伏せます。

## Compose DB 起動と restore

既存 PostgreSQL が 5432 を使っている場合は、先に `.env` の `POSTGRES_PORT` を一時的に `5433` などへ変更して restore します。最終的に systemd 停止後、5432 へ戻しても構いません。

```bash
docker compose -f docker-compose.yml -f docker-compose.stg.yml up -d db
docker compose -f docker-compose.yml -f docker-compose.stg.yml ps
```

dump を restore します。

```bash
LATEST_DUMP="$(ls -t /home/ubuntu/ichiyon-db-backups/ichiyon_stg_*.dump | head -n 1)"
docker compose -f docker-compose.yml -f docker-compose.stg.yml exec -T db pg_restore --clean --if-exists --no-owner --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" < "$LATEST_DUMP"
```

migration を最新化します。

```bash
docker compose -f docker-compose.yml -f docker-compose.stg.yml run --rm admin python scripts/migrate.py
```

## admin / bot 起動

admin を起動します。

```bash
docker compose -f docker-compose.yml -f docker-compose.stg.yml up -d admin
docker compose -f docker-compose.yml -f docker-compose.stg.yml logs admin --tail=100
```

`http://141.147.145.113:8080/login` を開きます。

admin が確認できたら、既存 stg systemd を停止・無効化します。削除はしません。

```bash
sudo systemctl stop ichiyon-admin-stg
sudo systemctl disable ichiyon-admin-stg
sudo systemctl stop ichiyon-bot-stg
sudo systemctl disable ichiyon-bot-stg
```

bot を Compose で起動します。

```bash
docker compose -f docker-compose.yml -f docker-compose.stg.yml --profile bot up -d bot
docker compose -f docker-compose.yml -f docker-compose.stg.yml logs bot --tail=100
```

## 確認

```bash
docker compose -f docker-compose.yml -f docker-compose.stg.yml ps
docker compose -f docker-compose.yml -f docker-compose.stg.yml logs admin --tail=100
docker compose -f docker-compose.yml -f docker-compose.stg.yml logs bot --tail=100
docker compose -f docker-compose.yml -f docker-compose.stg.yml exec admin python -m compileall bot admin scripts
docker compose -f docker-compose.yml -f docker-compose.stg.yml exec admin python scripts/check_modes_runtime.py
docker compose -f docker-compose.yml -f docker-compose.stg.yml exec admin python scripts/check_special_effects_runtime.py
docker compose -f docker-compose.yml -f docker-compose.stg.yml exec admin python scripts/check_deck_search_runtime.py
docker compose -f docker-compose.yml -f docker-compose.stg.yml exec admin python scripts/check_mention_guard_runtime.py
docker compose -f docker-compose.yml -f docker-compose.stg.yml exec admin python scripts/check_reaction_threshold_runtime.py
```

画面で確認する項目:

- `/login` が開く
- `/servers` が開く
- モード編集画面が開く
- モード中 Bot 名が表示・保存できる
- しこっち月一制限が表示される
- 既存 stg データが見える

Discord で確認する項目:

- Bot がログインしている
- ライオからしこっち周りが動く
- デッキ検索が落ちずに返る

## ロールバック

Compose を止めます。

```bash
cd /home/ubuntu/ichiyon-robot
docker compose -f docker-compose.yml -f docker-compose.stg.yml --profile bot down
```

既存 systemd を戻します。

```bash
sudo systemctl enable ichiyon-admin-stg
sudo systemctl enable ichiyon-bot-stg
sudo systemctl start ichiyon-admin-stg
sudo systemctl start ichiyon-bot-stg
systemctl status ichiyon-admin-stg --no-pager
systemctl status ichiyon-bot-stg --no-pager
```

DB を dump から戻す場合:

```bash
LATEST_DUMP="$(ls -t /home/ubuntu/ichiyon-db-backups/ichiyon_stg_*.dump | head -n 1)"
pg_restore --clean --if-exists --no-owner --dbname "$DATABASE_URL" "$LATEST_DUMP"
```

ポート確認:

```bash
ss -ltnp | grep -E ':5432|:8080'
sudo iptables -S
```

戻すファイル:

- `.env`: Docker 用に変更した場合は、systemd 運用時の値へ戻す
- `docker-compose.yml` / `docker-compose.stg.yml`: Git の `feature/docker-dev-environment` を使う
- systemd unit: 削除していないため `enable` / `start` で復旧

## 注意

- `.env` 実値はコミットしません。
- systemd unit は削除しません。
- 本番はこの手順の対象外です。
- dump が取れていない状態で DB 切替を進めません。
