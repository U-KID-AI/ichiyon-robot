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

秘密値を表示・送信しない。Discord入力を任意shellコマンドとしてそのまま実行しない。AI専用worktree内では通常の開発に必要なファイル作成・変更・削除・renameとローカルコマンド実行を許可する。Level 3操作は停止し、対象、影響、必要な操作、検証結果、復旧方法を提示してtaskを人間へ引き渡す。production操作は固定deployフロー以外では実行しない。

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

Phase 2AではAPIのDB状態更新だけを行う。Windows/Linux RunnerのCodex/Git/GitHub処理、worktree管理、Draft PR、Discord報告はPhase 2B/2Cの対象であり、APIはtask descriptionと固定された状態情報だけを返す。
## Phase 2B Local Runner

Phase 2Bのruntime test registryはuntrustedなrepository Pythonをホスト上でimport・実行しない。Python変更はメモリ上で構文compileのみを行い、`__pycache__`や`.pyc`を生成せず、repository由来のcheck scriptも実行しない。

Windows/Linux Runnerは`--once`で1 taskだけ処理する。Control Planeからclaimした後、source repositoryのclean状態と固定originを確認し、`origin/main`をfetchしてUUID由来のbranch/worktreeを作成する。Codex実行中とtest中はheartbeatを送り、lease維持に失敗した場合は処理を止めてworktreeを保持する。Codex自身にはcommitやGitHub操作をさせず、runnerが後続のcommit/push/PRを管理する。

Phase 2B以降は固定test registryと`git diff --check`を実行し、通常のrepo内変更をパス種別だけでrollbackしない。Git/worktree破損、repo外path、`.git`内部、symlink/reparse、timeout、process異常は停止する。Codex実行、Git、filesystem、API通信はfakeで検証し、production/staging接続はcheckで行わない。
RunnerのGit executableは`AI_TASK_RUNNER_GIT_PATH`で絶対path指定し、source repo/worktree配下の実行ファイルを拒否する。Codex childはGit credential helperを無効化した環境で起動し、stdout/stderrはbounded drainで上限を設ける。test registryはsubprocessを起動せず、メモリ上で構文compileを行う。
## Phase 2C Safe Publishing

Phase 2Cは、Phase 2BのCodex実行・静的テスト・差分検証に成功し、Control Planeのleaseを維持できているtaskだけをレビュー可能なGitHub状態へ進める。

RunnerはworktreeのHEADや実indexを移動させず、検証済み変更から決定的なcommit objectを生成する。remote操作直前に同じ候補を再構築し、commit SHAとtree SHAが一致しなければpublishしない。

push先は固定repositoryの `ai/task/<UUID>` branchだけとし、force pushを行わない。remote branchが既に存在する場合は、期待commit SHAと完全一致する場合だけretryとして採用し、不一致なら停止する。

branch publish後は `main` をbaseとするDraft Pull Requestを1件だけ作成または採用する。PR number、URL、Draft状態、base branch、head branch、head SHAを再検証し、すべて期待値と一致した場合だけControl Planeを `ready_for_review` へ遷移させる。

Git/GitHub subprocessはshellを使わず、出力上限と停止処理を持つ。Git network操作はinteractive authenticationを無効化し、Windowsでは検証済みのGit Credential Manager、Linuxでは検証済み絶対pathのGitHub CLI (`gh auth git-credential`) を使用する。task由来の秘密情報やrunner API tokenをGit/Codex/GitHub child environmentへ渡さない。

pushまたはPR作成の応答が不明確な状態でprocessが終了した場合、retry時は固定UUID branchとPRを再照合する。期待SHA・base・head・Draft metadataが完全一致する場合だけ既存成果物を採用し、それ以外はfail closedとする。

Phase 2Cでもmerge、production deploy、restart、DB migration、secrets変更、network変更その他Level 3操作は自動実行しない。Draft PRから先は人間レビューへ引き渡す。

## Linux runner runtime

Supported Linux target: Ubuntu 20.04 x86_64 with Python 3.12. Configure trusted
process settings `AI_TASK_RUNNER_CODEX_PATH=/home/ubuntu/.local/bin/codex`,
`AI_TASK_RUNNER_GIT_PATH=/usr/bin/git`, and `AI_TASK_RUNNER_GH_PATH=/usr/bin/gh`.
Executables must be normal files outside the source and task worktree roots;
those roots must be separate normal directories, never the production repository.
Linux does not require `AI_TASK_RUNNER_GCM_PATH`; Windows still requires it.
An operator provisions Codex authentication and GitHub CLI authentication as
U-KID-AI beforehand. Do not export GitHub tokens or read credential files for
validation. The runner does not load dotenv or run `gh auth setup-git`.

Publishing disables system/global Git configuration, audits local network
configuration, resets credential helpers, and pins the trusted GitHub CLI helper
for github.com. Linux also supplies fixed `-c` options for Git 2.25 compatibility,
sets Git's HOME/XDG_CONFIG_HOME to `/dev/null`, and pins GH_CONFIG_DIR to the
trusted runner HOME's `.config/gh` path. No credential contents are copied into
the environment. Operator-supplied GH_CONFIG_DIR/XDG overrides are not inherited.
Missing helpers or unsafe configuration fail closed. Repository
and UUID branch restrictions are unchanged. Windows-only Codex sandbox options
are emitted only on Windows; Linux retains the fixed sandbox with network enabled
for desktop-like implementation tasks.
Managed POSIX children start a new session. Cleanup signals the entire group,
waits boundedly after SIGTERM and SIGKILL, and fails closed if group disappearance
cannot be verified (including unreaped descendants). Windows retains taskkill /T /F.
The static test registry performs in-memory compilation without subprocesses.
Local process-group cleanup does not prove cancellation of remote SSH operations.

Offline regression: `python3.12 scripts/check_ai_task_linux.py`, plus the existing
local runner, publish, GitHub, code review, review merge, auto merge and deployment
checks. These checks must not invoke authenticated CLIs or activate deployment.
