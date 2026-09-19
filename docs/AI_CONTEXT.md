# AI開発コンテキスト

この文書は、AI開発エージェントが作業前に把握する共通知識である。既存文書・コードから確認できた事項だけを記載し、未確認事項は `TBD` または `要確認` とする。秘密情報は記載しない。

## リポジトリ全体構成

- `bot/`: Discord Botの実装とサービス群。
- `admin/`: FastAPIベースの管理画面。
- `main.py`: Botの起動エントリポイント。
- `migrations/`: PostgreSQL向けの連番SQL migrationを保持する。現在の最新番号や適用状態は作業時に確認する。
- `scripts/`: migration、seed、runtime check、Minecraft関連スクリプトなど。
- `minecraft/`: Minecraft Bedrock向けbehavior/resource packと設定。
- `data/`: 互換用・移行元のJSONなど。ローカルデータを含むため、内容を無断で共有しない。
- `docs/`: 開発・運用・機能文書。
- `docker-compose.yml`: ローカルの基本Compose構成。
- `docker-compose.prod.yml`: 本番用の差分Compose構成。
- `docker-compose.stg.yml`: stg用の差分Compose構成。

## Discord bot

Botは `python main.py` で起動し、Discord Tokenを環境変数から読み取る。ローカルでは `DISCORD_TOKEN` または `DISCORD_BOT_TOKEN` が必要で、Composeでは `bot` profileで起動する。AIからDiscordへ報告する将来機能の認証・送信先・権限設計はTBD。

Minecraft連携を含むDiscord入力は、固定された構造化コマンドと入力検証を経由する。AI task受付も許可ユーザーのallowlistと固定コマンド解析を経由し、任意のMinecraftコマンド、任意のitem ID、任意shellコマンドを受け付ける設計ではない。AI taskの保存先は`ai_tasks`、状態は`queued`などの固定値で管理する。AI taskの実行、git操作、staging・production操作はPhase 1の対象外である。

## admin

Adminは `uvicorn admin.main:app` で起動する。ローカルComposeでは通常 `http://localhost:8080`、stgでは文書上 `http://141.147.145.113:8080/login`、本番では文書上 `http://138.2.57.139:8000/login` が案内されている。実際の到達性・現在の稼働状態は作業時に要確認。

## PostgreSQLとmigrations

`db` は PostgreSQL 16で、`admin`と`bot`が同じDBを利用する。Compose内部の接続先は `db:5432`、ホストから接続する場合のポートは環境設定に依存する。migrationは `scripts/migrate.py` を使う。既存DBへのmigration、restore、データ変更は本番では人間承認必須とする。

## production

本番の通常運用は `db`、`admin`、`bot`をDocker Composeでまとめる方針で、branchは `main` と文書化されている。既存systemd unitはロールバック用に残す。AIは本番デプロイ、再起動、DB migration、rollbackを実行しない。実際の本番状態、稼働プロセス、バックアップの有無は都度要確認。

## staging

stgも `db`、`admin`、`bot`をDocker Composeで運用する方針で、文書上のbranchは `feature/docker-dev-environment` である。既存systemd unitはロールバック用に残す。本番とstgの接続情報や実値は `.env` にあり、AIは読み取らない。

## local development

ローカル開発では `.env.example` を `.env` にコピーし、`docker compose up -d db`、`docker compose up -d admin`、必要に応じて `docker compose --profile bot up -d bot` を使う。`docker compose config` は環境変数を表示し得るため、共有確認にはダミー値の `.env.example` を使う。

## Docker Compose

Composeの基本サービスは `db`、`admin`、`bot`。本番とstgはbase Composeにそれぞれの差分ファイルを重ねる。AIはCompose定義や環境設定を変更する場合、対象環境と影響を確認し、変更後に `docker compose ... config` を実行する。Compose上の追加サービスや現在の稼働状況は要確認。

## Minecraft Bedrock serverとの連携

BotはMinecraft向けの構造化コマンドをキューへ登録し、behavior packから成功・失敗を受け取る。プレイヤー名は `^[A-Za-z0-9_]{1,16}$` で検証される。Minecraft本番world、pack、Beta API、world設定への変更は人間承認必須。

## Narita Bridge

Narita BridgeはMinecraft behavior packとBot間の連携で、成田カーペット、各種block、タケツミエッグ、手持ち確認、状態確認などの固定コマンドを扱う。コマンドキューは `minecraft_command_queue` を使用する。必要なmigrationと適用状態は、作業時に関連実装とDBを確認する。

## Minecraft Control API

Minecraftホストの固定APIは `GET /status` と `POST /restart` のみを公開し、任意shell、Docker、SSH、Minecraftコマンドは受け付けない方針である。APIはprivate network限定とし、秘密値は環境変数で管理する。AIは本番restart、pack sync、world backupやworld変更を実行しない。

## Git / GitHub運用

Phase 0の想定フローは、AI専用branch/worktreeで調査・編集・検証を行い、`git diff` と `git status` を確認した後にcommit、push、Draft Pull Request作成、Discord報告へ進むこと。現時点でこのタスクのcommit、push、PRは行わない。リポジトリの既定branch、保護ルール、CI必須チェック、PRテンプレートは要確認。

## AI専用worktree方式

AIごとに専用branchと専用worktreeを割り当て、他の作業者の変更と混ぜない。worktreeの作成場所、命名規則、同時実行数、完了後の削除方針はTBD。worktree削除やbranch削除は、未保存変更がないことを確認し、人間の運用ルールに従う。
