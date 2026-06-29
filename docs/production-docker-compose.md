# 本番 Docker Compose 運用手順

本番の通常運用は Docker Compose で `db` / `admin` / `bot` をまとめて起動します。既存 systemd unit は削除せず、ロールバック用に残します。

対象:

- host: `138.2.57.139`
- admin URL: `http://138.2.57.139:8000/login`
- branch: `main`

## 構成

- `db`: PostgreSQL 16
- `admin`: `uvicorn admin.main:app`
- `bot`: `python main.py`

本番 admin は既存URL維持のため、ホスト側 `8000` 番で公開します。コンテナ内 admin は `8080` 番です。

DB は `127.0.0.1:${POSTGRES_PORT:-5433}:5432` だけに bind します。外部公開しません。`docker-compose.prod.yml` は base 側の `db.ports` と `admin.ports` を置き換えます。

## 事前確認

```bash
cd /home/ubuntu/ichiyon-robot
git fetch origin
git switch main
git pull origin main
git status --short
docker --version
docker compose version
```

既存 unit とポートを確認します。

```bash
systemctl status ichiyon-bot --no-pager
systemctl status ichiyon-admin --no-pager
ss -ltnp | grep -E ':5432|:5433|:8000'
```

## .env

本番の `.env` 実値はコミットしません。初回切替時は、既存 systemd 運用の `.env` をすぐに上書きせず、先にDB dumpを取ります。

dump 後に Docker Compose 用へ差し替える例:

```bash
cp .env .env.before-docker-$(date +%Y%m%d_%H%M%S)
nano .env
```

本番 Docker Compose 用の主な値:

```text
APP_ENV=production
ENABLE_DEV_COMMANDS=false
ICHIYON_DATA_BACKEND=db
ADMIN_PORT=8000
ADMIN_BASE_URL=http://138.2.57.139:8000
POSTGRES_PORT=5433
DATABASE_URL=postgresql://...@db:5432/...
DISCORD_TOKEN=...
DISCORD_OAUTH_CLIENT_ID=...
DISCORD_OAUTH_CLIENT_SECRET=...
DISCORD_OAUTH_REDIRECT_URI=http://138.2.57.139:8000/auth/discord/callback
X_SEARCH_ENABLED=true
X_BEARER_TOKEN=...
```

Token、Client Secret、X Bearer Token、DB password は共有・コミットしません。

## 正しい切替順

1. 既存DBをcustom dump
2. Docker DB volume作り直し
3. Docker DB起動
4. `pg_restore`
5. `migrate`
6. 主要テーブル件数確認
7. `admin` 起動
8. `bot` 起動
9. check系実行
10. systemd disable

restore 前に migration は流しません。

## 1. 既存DBをcustom dump

バックアップ先を作ります。

```bash
sudo mkdir -p /home/ubuntu/ichiyon-db-backups
sudo chown ubuntu:ubuntu /home/ubuntu/ichiyon-db-backups
chmod 700 /home/ubuntu/ichiyon-db-backups
```

既存 systemd 運用で使っていた DB 接続文字列を `LEGACY_DATABASE_URL` に入れて dump します。値は表示しません。

```bash
set -a
. /home/ubuntu/ichiyon-robot/.env
set +a
LEGACY_DATABASE_URL="$DATABASE_URL"
pg_dump "$LEGACY_DATABASE_URL" --format=custom --file="/tmp/ichiyon_prod_$(date +%Y%m%d_%H%M%S).dump"
sudo mv /tmp/ichiyon_prod_*.dump /home/ubuntu/ichiyon-db-backups/
ls -lh /home/ubuntu/ichiyon-db-backups
```

このあと `.env` を Docker Compose 用に編集します。`DATABASE_URL` は `db:5432` にします。

## 2. Docker DB volume作り直し

dump が取れていることを確認してから実行します。

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile bot down
docker volume rm ichiyon-robot_postgres_data || true
docker volume rm ichiyonrobot_postgres_data || true
```

volume名が違う場合は以下で確認します。

```bash
docker volume ls | grep postgres_data
```

## 3. Docker DB起動

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d db
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
```

## 4. pg_restore

```bash
LATEST_DUMP="$(ls -t /home/ubuntu/ichiyon-db-backups/ichiyon_prod_*.dump | head -n 1)"
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T db pg_restore --clean --if-exists --no-owner --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" < "$LATEST_DUMP"
```

## 5. migrate

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm admin python scripts/migrate.py
```

## 6. 件数確認

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "select count(*) as guilds from guilds;"
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "select count(*) as modes from modes;"
```

必要に応じて `mention_reactions`、`auto_reactions`、`special_effect_tags` も確認します。

## 7. admin起動

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d admin
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs admin --tail=100
```

`http://138.2.57.139:8000/login` を開きます。

## 8. bot起動

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile bot up -d bot
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs bot --tail=100
```

ログで以下を確認します。

- Discord にログインしている
- `APP_ENV=production ENABLE_DEV_COMMANDS=False`
- Token や QR 文字列がログに出ていない

## 9. check系実行

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec admin python -m compileall bot admin scripts
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec bot python -c "import cv2; print(cv2.__version__)"
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec admin python scripts/check_modes_runtime.py
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec admin python scripts/check_special_effects_runtime.py
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec admin python scripts/check_deck_search_runtime.py
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec admin python scripts/check_mention_guard_runtime.py
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec admin python scripts/check_reaction_threshold_runtime.py
```

## 10. systemd disable

Docker版の admin / bot が確認できてから、既存 systemd を停止・無効化します。削除はしません。

```bash
sudo systemctl stop ichiyon-admin
sudo systemctl disable ichiyon-admin
sudo systemctl stop ichiyon-bot
sudo systemctl disable ichiyon-bot
```

## 通常運用

起動:

```bash
cd /home/ubuntu/ichiyon-robot
docker compose -f docker-compose.yml -f docker-compose.prod.yml build bot admin
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d db admin
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile bot up -d bot
```

まとめて起動:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile bot up -d
```

ログ:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs admin --tail=100
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs bot --tail=100
```

停止:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile bot down
```

## ロールバック

Compose を止めます。

```bash
cd /home/ubuntu/ichiyon-robot
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile bot down
```

既存 systemd を戻します。

```bash
sudo systemctl enable ichiyon-admin
sudo systemctl enable ichiyon-bot
sudo systemctl start ichiyon-admin
sudo systemctl start ichiyon-bot
systemctl status ichiyon-admin --no-pager
systemctl status ichiyon-bot --no-pager
```

DB を dump から戻す場合:

```bash
LATEST_DUMP="$(ls -t /home/ubuntu/ichiyon-db-backups/ichiyon_prod_*.dump | head -n 1)"
pg_restore --clean --if-exists --no-owner --dbname "$LEGACY_DATABASE_URL" "$LATEST_DUMP"
```

ポート確認:

```bash
ss -ltnp | grep -E ':5432|:5433|:8000'
sudo iptables -S
```

戻すもの:

- `.env`: systemd 運用時の値へ戻す
- systemd unit: 削除していないため `enable` / `start` で復旧
- DB: `/home/ubuntu/ichiyon-db-backups/*.dump` から復旧

## 注意

- `.env` 実値や `.env.before-docker-*` はコミットしません。
- systemd unit は削除しません。
- systemd は通常運用ではなくロールバック用です。
- dump が取れていない状態で DB 切替を進めません。
