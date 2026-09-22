# AI開発コンテキスト

この文書は現在の実行モデルとリポジトリ構成をまとめる。稼働環境、migration適用状態、外部サービスの到達性は作業時に確認し、秘密情報は記載しない。

## リポジトリ構成

- `bot/`: Discord Bot、サービス、repository層。
- `admin/`: FastAPI管理画面とAI taskの内部API。
- `main.py`: Botの起動エントリポイント。
- `migrations/`: PostgreSQL向け連番SQL migration。
- `scripts/`: runner、Git/GitHub、deployment、migration、check等。
- `minecraft/`: Minecraft Bedrock向けbehavior/resource packと設定。
- `data/`: 互換用・移行元JSON等。ローカルデータを無断で共有しない。
- `docs/`: 開発・運用文書。
- `docker-compose.yml`、`docker-compose.prod.yml`、`docker-compose.stg.yml`: 基本構成と環境別差分。

## AI taskの受付と状態

Ichiyonの指定Guild/Channelで、許可ユーザーのメッセージをtaskとして受け付ける。既定値はGuild `1515983621461245972`、Channel `1551004878808285377`。運用設定は `AI_TASK_DISCORD_GUILD_ID`、`AI_TASK_DISCORD_CHANNEL_ID`、`AI_TASK_ALLOWED_USER_IDS`。Irsiaはこのfreeform受付を担当しない。

通常の依頼文に加えて `AI 開発 <依頼内容>`、`AI 状態 <task_id>`、`AI 一覧` がある。taskはPostgreSQLの `ai_tasks` に保存し、UUIDごとに `ai/task/<UUID>` branchと `ai-task-<UUID>` worktreeを対応付ける。Discord受付の依頼文は1800文字、保存・runner側は4000文字まで。

Bot/AdminがControl Plane、Windows/Linux runnerが開発作業と公開を担当する。runnerは固定APIを使い、task管理のためにDBへ直接接続しない。これはCodexの承認済み開発コマンドを制限するものではない。claim token、heartbeat、leaseで同じtaskの実行所有者を管理する。

## Codexと公開の責務

Codexは通常のデスクトップ作業と同じ自由度で、承認済みユーザースコープ内を調査・編集・検証する。`danger-full-access` と `approval_policy="never"` を使用し、親processの環境と通常のユーザー・project設定を利用する。runnerによる編集パスの分類、symlink/junction拒否、Codex設定層検査、変更内容に対する自動reviewは行わない。

workflow、migration、Docker、scripts、Bot/Admin、Minecraft pack、テスト等はすべて開発対象。必要なローカルコマンドと認証を利用できるが、秘密値は出力しない。関連テストはCodexが実行し、runnerは追加のPython構文チェックを行う。

runnerがcommit、task branchのpush、PR、merge、deploymentを管理する。公開先とSHA、CI、lease、deployment結果の確認は維持する。操作に失敗した場合はredaction済みの診断を残し、編集内容を保持する。運用の詳細は `AI_RUNBOOK.md` を参照する。

## ローカル開発

Botの起動は `python main.py`、Adminは `uvicorn admin.main:app`。PostgreSQLはBotとAdminで共有する。ローカル接続先、ポート、必要な認証は現在の構成で確認する。

Composeでは `docker compose up -d db`、`docker compose up -d admin`、必要に応じて `docker compose --profile bot up -d bot` を使用する。サービスやprofileは作業時に定義を確認する。`docker compose config` 等の出力には秘密値が含まれ得るため、共有する診断をredactする。

migration用entry pointは `scripts/migrate.py`。適用対象、データ、バックアップ、変更の影響を確認し、ユーザーの承認範囲に従う。production/stagingの実状態を文書中の古いホスト名やbranch名から推測しない。

## Minecraft連携とdeployment

Narita BridgeはMinecraft behavior packとBotを接続し、構造化コマンドを `minecraft_command_queue` で扱う。Minecraft向けAPIやプレイヤー名の入力検証は各サービスの通信契約であり、Codexのファイル編集権限とは別である。

アプリとMinecraftへの公開は対象別のdeployment adapterが担当する。対象の選択、必要な運用設定、実際に反映されたSHAとhealthを確認する。Minecraftの運用詳細は `AI_MINECRAFT_DEPLOYMENT.md` を参照する。既存DB、world、共有データは通常のテストfixtureと混同しない。

## Discordへの結果通知

Ichiyonは `completed`、`failed`、`needs_human`、`cancelled` を指定channelへ通知する。通知のmentionsは無効化し、秘密値を含むtask本文や認証設定をそのまま出さない。送信成功後に通知記録を保存し、失敗した通知は再試行対象になる。Discord送信とDB commitの間で停止すると通知が重複し得る。
