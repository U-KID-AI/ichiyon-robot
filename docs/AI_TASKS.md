# AI開発タスク計画

この文書はPhase 0からPhase 4までの計画を示す。現在実装済みの機能と将来タスクを混同しない。明示していない項目は未実装または要確認である。

## Phase 0: 基盤

このタスクで文書化する範囲:

- AI専用git worktree
- `AGENTS.md`
- `docs/AI_CONTEXT.md`
- `docs/AI_RULES.md`
- `docs/AI_TASKS.md`
- `docs/AI_RUNBOOK.md`
- Codex CLIの利用方針（導入・設定は要確認）
- GitHub CLIの利用方針（認証・権限は要確認）

このPhase 0では、Discordからの受付、task queue、Codexの非対話実行、commit、push、PR作成、Discord報告の自動化は実装しない。

## Phase 1: タスク受付基盤（実装済み）

- DiscordからAI開発taskを受け付ける。
- task IDを発行する。
- `ai_tasks`へtaskとtask stateを保存する。
- Discordからtaskの状態と一覧を確認する。
- taskごとのbranch名とworktree名を記録する。実際のbranch/worktreeは作成しない。

受付形式は`AI 開発 <依頼内容>`、`AI 状態 <task_id>`、`AI 一覧`とする。AI task権限は`AI_TASK_ALLOWED_USER_IDS`のallowlistだけで判定し、allowlist未設定時は拒否する。依頼本文はDiscord受付時1800文字、DBでは4000文字を上限とし、同じBotとDiscord messageの二重登録をDB制約で防止する。Codex実行、git操作、staging・production操作はPhase 2以降または人間の運用対象である。

## Phase 2: 実行とDraft PR（将来）

- Codexを非対話で実行する。
- 変更内容に応じてtestを自動選択する。
- 結果を確認してcommitする。
- pushする。
- Draft PRを作成する。
- テスト結果と変更内容をDiscordへ報告する。

Discord入力をshellとしてそのまま実行する受付、秘密情報の受け渡し、本番環境への直接接続は設計対象外または禁止対象である。Codex自身は専用worktree内で実装・検証に必要なローカルコマンドを実行できる。

## Phase 3: レビュー支援（将来）

- PRレビューを支援する。
- 修正依頼を受け付ける。
- 修正を再実行する。
- 人間承認フローへ接続する。

merge権限と承認の記録方式は要設計。AIが承認者を代行しない。

## Phase 4: staging反映（将来）

- 承認済み変更をstagingへ反映する。
- staging反映後のcheckと報告を行う。
- stagingの自動化範囲は、このPhaseで別途定義する。
- productionの操作は人間承認必須とする。

production deploy、production restart、production DB migration、production secrets変更、production firewall/network変更、Minecraft production world変更、production data deletion、production rollbackのAI Runnerによる実行は現在の計画対象外である。必要な場合は対象、影響、必要な操作、検証結果、復旧方法を整理して人間へ引き渡し、人間が外部で実行する。stagingの自動化とproductionの人間実行を混同しない。
## Phase 2A Control Plane

`migrations/060_add_ai_task_runner_fields.sql`でRunner識別、claim token、lease、試験結果、commit/PR情報を追加する。`queued` taskは固定APIのclaimで`running`になり、heartbeatとprogressを経て、`testing`、`needs_human`、`failed`、`ready_for_review`へ固定遷移する。lease期限切れは自動再queueせず`needs_human`へ遷移する。

Phase 2AはControl Planeだけを提供し、Codex、Git、worktree、branch、PR作成、Discord通知は実行しない。
## Phase 2B Local Runner

Runnerの設定はprocess environmentからのみ読み取り、dotenvは読み込まない。API URL、Runner ID、source repo、専用worktree root、Codex実行ファイルを検証する。taskのbranch/worktree名はUUIDから再生成してAPI値と一致確認し、Codex終了後にHEAD、branch、worktree一覧、remoteを比較する。repo内のworkflow、migration、Docker、scripts、bot/admin、Minecraft pack、テスト等は通常の編集対象とし、パスだけを理由にrollbackやneeds_humanへ送らない。repo外path、`.git`内部、symlink/reparse等のworktree境界違反は停止する。

Codexは固定argv、workspace-write、network有効、ephemeral、ignore-user-config、Bearer tokenを渡さないclean environmentで実行する。Codexの変更は保持し、runnerが検証後にcommit/push/Draft PRを管理する。
