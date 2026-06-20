# いちよんロボ ver2.0 PostgreSQL開発基盤

## 目的

PostgreSQL移行に向けて、既存Bot挙動を変えずにDB接続と開発環境の土台を用意します。

今回の追加範囲は以下です。

* `.env.example` にDB接続設定例を追加
* `docker-compose.yml` にPostgreSQLコンテナを追加
* `bot/db.py` にDB接続用の最小モジュールを追加

既存JSON処理のDB置き換え、OAuth実装、管理画面の大改修、Bot挙動変更は行いません。

## 環境変数

開発環境では `DATABASE_URL` を使ってDBへ接続します。

設定例:

```env
POSTGRES_DB=ichiyon_robot
POSTGRES_USER=ichiyon_robot
POSTGRES_PASSWORD=ichiyon_robot_password
DATABASE_URL=postgresql://ichiyon_robot:ichiyon_robot_password@localhost:5432/ichiyon_robot
```

`.env` はコミットしません。
実際のパスワードやトークンもコミット対象にしません。

## PostgreSQLコンテナ

ローカル開発では Docker Compose でPostgreSQLだけを起動します。

```bash
docker compose up -d postgres
```

PostgreSQLは `localhost:5432` に公開されます。
データは名前付きボリューム `postgres_data` に保存します。

## マイグレーション

初期スキーマは `migrations/001_initial_schema.sql` に定義します。
マイグレーションの適用状況はDB内の `schema_migrations` テーブルで管理します。

PostgreSQL起動後、以下を実行します。

```bash
python scripts/migrate.py
```

別の接続先を明示する場合は `--database-url` を使います。

```bash
python scripts/migrate.py --database-url postgresql://ichiyon_robot:ichiyon_robot_password@localhost:5432/ichiyon_robot
```

テーブル一覧は以下で確認できます。

```bash
docker compose exec postgres psql -U ichiyon_robot -d ichiyon_robot -c "\dt"
```

## DB接続モジュール

`bot/db.py` は `DATABASE_URL` を読み込み、必要なときだけ接続します。
既存Bot起動時に自動接続しないため、既存挙動には影響しません。

主な関数:

* `get_database_url()`
* `require_database_url()`
* `connect()`
* `get_connection()`
* `ping()`

接続確認例:

```python
from bot.db import ping

print(ping())
```

## 今後の想定

次の段階で、設計済みのテーブルに合わせてマイグレーション方針を決めます。
既存JSONデータのDB置き換えは、Bot挙動を維持できる単位で段階的に進めます。

