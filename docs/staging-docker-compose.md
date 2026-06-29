# stg Docker Compose 運用手順

stg の通常運用は Docker Compose で `db` / `admin` / `bot` をまとめて起動します。本番はまだ対象外です。既存 systemd unit は削除せず、ロールバック用に残します。

対象:

- host: `141.147.145.113`
- project: `/home/ubuntu/ichiyon-robot`
- branch: `feature/docker-dev-environment`
- admin URL: `http://141.147.145.113:8080/login`

## 構成

- `db`: PostgreSQL 16
- `admin`: `uvicorn admin.main:app`
- `bot`: `python main.py`

stg の DB は `127.0.0.1:${POSTGRES_PORT:-5433}:5432` だけに bind します。外部公開しません。`docker-compose.stg.yml` は base 側の `db.ports` を置き換えるため、最終 config に DB port は1つだけ出ます。

コンテナ内のアプリは `DATABASE_URL=postgresql://...@db:5432/...` を使います。`localhost:5432` 前提の systemd 用接続文字列は通常運用では使いません。

## Docker 未導入の場合

Docker がない場合だけ実行します。

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker ubuntu
```

グループ反映のため、SSHを入り直します。

## 事前確認

```bash
cd /home/ubuntu/ichiyon-robot
git fetch origin
git switch feature/docker-dev-environment
git pull origin feature/docker-dev-environment
git status --short
docker --version
docker compose version
```

既存 unit とポートを確認します。

```bash
systemctl status ichiyon-bot-stg --no-pager
systemctl status ichiyon-admin-stg --no-pager
ss -ltnp | grep -E ':5432|:5433|:8080'
```

## stg 用 .env

`.env.stg.example` を元に、サーバー上だけで `.env` を作ります。

初回切替時は、既存 systemd 運用で使っている `.env` をすぐに上書きしません。先に「1. 既存DBをcustom dump」を実行します。dump が取れてから、以下で Docker Compose 用の `.env` に差し替えます。

```bash
cp .env .env.systemd.backup
cp .env.stg.example .env
chmod 600 .env
nano .env
```

`.env` には stg の実値を入れます。Token、Client Secret、X Bearer Token、DB password は Git に入れません。

重要:

- `APP_ENV=staging`
- `ENABLE_DEV_COMMANDS=false`
- `POSTGRES_PORT=5433`
- `DATABASE_URL=postgresql://...@db:5432/...`
- `DISCORD_TOKEN` に stg Bot Token を入れる
- `ADMIN_BASE_URL=http://141.147.145.113:8080`

## 正しい切替順

1. 既存DBをcustom dump
2. Docker DB volume作り直し
3. Docker DB起動
4. `pg_restore`
5. `migrate`
6. `guilds` / `modes` 件数確認
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
pg_dump "$LEGACY_DATABASE_URL" --format=custom --file="/tmp/ichiyon_stg_$(date +%Y%m%d_%H%M%S).dump"
sudo mv /tmp/ichiyon_stg_*.dump /home/ubuntu/ichiyon-db-backups/
ls -lh /home/ubuntu/ichiyon-db-backups
```

このあと `.env` を Docker Compose 用に編集します。`DATABASE_URL` は `db:5432` にします。

## 2. Docker DB volume作り直し

dump が取れていることを確認してから実行します。

```bash
docker compose -f docker-compose.yml -f docker-compose.stg.yml --profile bot down
docker volume rm ichiyon-robot_postgres_data || true
docker volume rm ichiyonrobot_postgres_data || true
```

volume名が違う場合は以下で確認します。

```bash
docker volume ls | grep postgres_data
```

## 3. Docker DB起動

```bash
docker compose -f docker-compose.yml -f docker-compose.stg.yml up -d db
docker compose -f docker-compose.yml -f docker-compose.stg.yml ps
```

## 4. pg_restore

```bash
LATEST_DUMP="$(ls -t /home/ubuntu/ichiyon-db-backups/ichiyon_stg_*.dump | head -n 1)"
docker compose -f docker-compose.yml -f docker-compose.stg.yml exec -T db pg_restore --clean --if-exists --no-owner --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" < "$LATEST_DUMP"
```

## 5. migrate

```bash
docker compose -f docker-compose.yml -f docker-compose.stg.yml run --rm admin python scripts/migrate.py
```

## 6. 件数確認

```bash
docker compose -f docker-compose.yml -f docker-compose.stg.yml exec db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "select count(*) as guilds from guilds;"
docker compose -f docker-compose.yml -f docker-compose.stg.yml exec db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "select count(*) as modes from modes;"
```

目安として、切替時点では `guilds=3` / `modes=12` を確認済みです。

## 7. admin起動

```bash
docker compose -f docker-compose.yml -f docker-compose.stg.yml up -d admin
docker compose -f docker-compose.yml -f docker-compose.stg.yml logs admin --tail=100
```

`http://141.147.145.113:8080/login` を開きます。

## 8. bot起動

```bash
docker compose -f docker-compose.yml -f docker-compose.stg.yml --profile bot up -d bot
docker compose -f docker-compose.yml -f docker-compose.stg.yml logs bot --tail=100
```

ログで以下を確認します。

- Discord にログインしている
- `APP_ENV=staging ENABLE_DEV_COMMANDS=False`
- `APP_ENV must be production or development` の警告が出ない

## 9. check系実行

```bash
docker compose -f docker-compose.yml -f docker-compose.stg.yml ps
docker compose -f docker-compose.yml -f docker-compose.stg.yml exec admin python -m compileall bot admin scripts
docker compose -f docker-compose.yml -f docker-compose.stg.yml exec bot python -c "import cv2; print(cv2.__version__)"
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

## 10. systemd disable

Docker版の admin / bot が確認できてから、既存 stg systemd を停止・無効化します。削除はしません。

```bash
sudo systemctl stop ichiyon-admin-stg
sudo systemctl disable ichiyon-admin-stg
sudo systemctl stop ichiyon-bot-stg
sudo systemctl disable ichiyon-bot-stg
```

## 通常運用

起動:

```bash
cd /home/ubuntu/ichiyon-robot
docker compose -f docker-compose.yml -f docker-compose.stg.yml build bot admin
docker compose -f docker-compose.yml -f docker-compose.stg.yml up -d db admin
docker compose -f docker-compose.yml -f docker-compose.stg.yml --profile bot up -d bot
```

ログ:

```bash
docker compose -f docker-compose.yml -f docker-compose.stg.yml logs admin --tail=100
docker compose -f docker-compose.yml -f docker-compose.stg.yml logs bot --tail=100
```

停止:

```bash
docker compose -f docker-compose.yml -f docker-compose.stg.yml --profile bot down
```

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
pg_restore --clean --if-exists --no-owner --dbname "$LEGACY_DATABASE_URL" "$LATEST_DUMP"
```

ポート確認:

```bash
ss -ltnp | grep -E ':5432|:5433|:8080'
sudo iptables -S
```

戻すファイル:

- `.env`: systemd 運用時の値へ戻す
- systemd unit: 削除していないため `enable` / `start` で復旧
- DB: `/home/ubuntu/ichiyon-db-backups/*.dump` から復旧

## 注意

- `.env` 実値はコミットしません。
- systemd unit は削除しません。
- systemd は通常運用ではなくロールバック用です。
- 本番はこの手順の対象外です。
- dump が取れていない状態で DB 切替を進めません。
