# AI Task Runner運用フロー

この文書は将来のAI Task Runnerの基本フローを定義する。ここでは本番操作手順を実装・自動化しない。

## 基本フロー

```text
Discord
↓
Task受付
↓
task queue
↓
isolated worktree + branch
↓
Codex実行
↓
tests
↓
git diff review
↓
commit
↓
push
↓
Draft PR
↓
Discordへ結果通知
↓
人間レビュー
```

受付時にtask IDを発行し、要求、依頼者、受け付け時刻、対象worktree、branch、状態を記録する。実際のqueue・state保存先と保持期間はTBD。

Phase 1では、taskをPostgreSQLの`ai_tasks`へ保存する。`task_id`はUUID、初期状態は`queued`、branch名は`ai/task/<UUID>`、worktree名は`ai-task-<UUID>`とする。Discordからは`AI 開発 <依頼内容>`、`AI 状態 <task_id>`、`AI 一覧`を利用できる。AI taskの受付・照会は`AI_TASK_ALLOWED_USER_IDS`に明示されたDiscord userだけを許可し、実際のbranch/worktree作成、Codex実行、commit、push、PR、staging・production操作は行わない。

## 実行時の原則

1. 関連コードと文書を調査する。
2. AI専用worktreeで作業する。
3. 変更範囲をtaskに限定する。
4. 関連test、compile、checkを実行する。
5. `git diff` と `git status` でレビューする。
6. 結果を記録してからcommit、push、Draft PRへ進む。
7. 実行結果、変更内容、検証結果、残課題をDiscordへ通知する。

秘密情報を取得・表示・送信しない。Discord入力を任意shellコマンドとして実行しない。Level 3操作は停止し、対象、影響、必要な操作、検証結果、復旧方法を提示してtaskを人間へ引き渡す。production操作は人間が外部で実行し、AI Runnerは承認後も続行しない。

## エラー時

- 変更内容を保持する。
- ログを保存する。ただし秘密情報を含めない。
- taskを `failed` 状態にする。
- エラー、対象task ID、変更の有無、次に必要な判断をDiscordへ報告する。
- 本番には触らない。

自動で変更を破棄したり、rollbackしたり、branchやworktreeを削除したりしない。後処理方針が定義されていない場合は人間判断へ引き渡す。

## キャンセル時

- 実行中のCodexを停止する。
- 子プロセスと関連するtask runnerの状態を確認する。
- 変更内容とログを保持する。
- worktree後処理を行う前に未保存変更の有無を確認する。
- branchの扱いを運用ルールに従って決める。未定義なら削除しない。
- task状態を `cancelled` に更新する。
- Discordへキャンセル結果を報告する。

## 人間レビューへの引き渡し

Draft PRには目的、変更範囲、テスト結果、既知の制約、Level 3操作の有無を記載する。人間レビューが完了するまでmerge、production deploy、production restart、production DB migration、production secrets変更、production firewall/network変更、Minecraft production world変更、production data deletion、production rollbackを行わない。これらが必要になった場合は `needs_human` にして、人間が外部で実行するための情報を引き渡す。

## 状態

想定状態は `queued`、`running`、`testing`、`needs_human`、`ready_for_review`、`failed`、`cancelled`、`completed`。`needs_human` は、Level 3操作が必要、権限不足、要求が曖昧で安全に決定できない、外部副作用を伴う判断が必要、人間による操作・判断を待っている場合に使う。`needs_human` 中はAI Runnerがproduction操作を続行してはいけない。状態遷移、再実行、タイムアウト、並列実行数は要設計。
## Phase 2A runner control plane

Runner APIは`/internal/ai-tasks/claim`、`/{task_id}/heartbeat`、`/{task_id}/progress`、`/{task_id}/testing`、`/{task_id}/fail`、`/{task_id}/needs-human`、`/{task_id}/ready-for-review`の固定operationで構成する。`Authorization: Bearer`には専用の`AI_TASK_RUNNER_API_TOKEN`を使い、未設定・不一致は拒否する。claimはqueuedを古い順にatomic取得し、180秒leaseとUUID claim tokenを発行する。lease期限切れは再queueせずneeds_humanへ移す。

Phase 2AではAPIのDB状態更新だけを行う。Windows RunnerのCodex/Git/GitHub処理、worktree管理、Draft PR、Discord報告はPhase 2B/2Cの対象であり、APIはtask descriptionと固定された状態情報だけを返す。
