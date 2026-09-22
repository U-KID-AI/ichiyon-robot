# AI Task Runner運用

## 実行モデル

runnerは承認済みユーザーの開発依頼を専用branch/worktreeで処理する。Codexは `danger-full-access`、`approval_policy="never"` で起動し、通常のユーザー・project設定と親processの環境を利用する。設定されたユーザースコープとOS権限の範囲で、通常のデスクトップCodexと同じ調査・編集・コマンド実行ができる。

runner独自の編集パス制限、symlink/junction拒否、Codex設定層の検査、環境変数の名前による除去、変更内容の自動reviewは行わない。秘密値のredaction、入力設定の検証、process停止とtimeout、task identity、制御された公開は維持する。

## 通常フロー

1. 許可ユーザーの依頼をDiscordから受け付け、PostgreSQLにtaskを保存する。
2. runnerがControl Plane APIでtaskをclaimし、UUID由来のbranch/worktree名を確認する。
3. 対象repositoryのbaseを取得し、`ai/task/<UUID>` branchと `ai-task-<UUID>` worktreeを用意する。
4. heartbeatを維持しながらCodexが実装と関連テストを実行する。
5. runnerの追加構文チェック等を行い、実際の失敗があれば診断を返して設定された回数まで修正する。
6. runnerが変更をcommitし、指定repositoryのtask branchへpushしてPRを作成または照合する。
7. 構成された公開フローがCIと候補SHAを確認し、mergeとdeploymentを進める。別のCodex content reviewは挟まない。
8. 実際の反映結果を確認し、task状態とredaction済みの結果を保存する。Botが指定channelへ通知する。

Codexは開発作業、runnerは公開を担当する。Codexがcommit、push、PR、merge、deploymentを独自に重複実行しない。taskの内容だけから公開先やdeployment用コマンドを組み立てない。

## runner設定

runner設定はprocess environmentから読み取り、dotenvを暗黙に読み込まない。資格情報の値は文書やコマンドの共有出力へ記載しない。

| 設定 | 用途 |
| --- | --- |
| `AI_TASK_RUNNER_API_BASE_URL` | Control Plane URL。HTTPS、またはlocalhostのHTTP |
| `AI_TASK_RUNNER_API_TOKEN` | runner専用API認証 |
| `AI_TASK_RUNNER_ID` | 1〜64文字の英数字・ドット・ハイフン・アンダースコア |
| `AI_TASK_RUNNER_REPO_ROOT` | 既存source repositoryの絶対path |
| `AI_TASK_RUNNER_WORKTREE_ROOT` | task worktreeを作成する既存directoryの絶対path |
| `AI_TASK_RUNNER_CODEX_PATH` | Codex実行ファイルの絶対path |
| `AI_TASK_RUNNER_GIT_PATH` | Git実行ファイルの絶対path |
| `AI_TASK_RUNNER_GH_PATH` | GitHub CLI実行ファイルの絶対path |
| `AI_TASK_RUNNER_GCM_PATH` | Windows向けの任意の互換設定。指定する場合はGit Credential Managerの絶対path |
| `AI_TASK_RUNNER_CODEX_HOME` | 省略時は `CODEX_HOME`、さらに省略時は `~/.codex` |
| `AI_TASK_RUNNER_POLL_SECONDS` | 5秒以上、既定5秒 |
| `AI_TASK_RUNNER_CODEX_TIMEOUT_SECONDS` | 正の有限秒数、既定1800秒 |
| `AI_TASK_RUNNER_MAX_ATTEMPTS` | 1〜10回、既定5回 |

directory/fileの存在と種類は設定エラーとして検証する。symlink/junction、repository内の実行ファイルやCodex home、包含関係のあるrootを位置だけで拒否しない。Windows/Linuxを対象とし、GCM設定はWindowsでも省略できる。公開にはGitの通常の認証設定を使用し、明示されたGCM pathは互換設定として検証・保持する。LinuxではGCM設定を使用しない。GitとGitHub CLIのpathは両環境で必須。

Codexの子processは通常のローカル環境を引き継ぐ。明示的なCodex home設定だけを上書きし、`CODEX_SQLITE_HOME`、認証用環境変数、Git credential helper設定を除去しない。shell環境は `inherit="all"` と `ignore_default_excludes=true` を指定する。ユーザー自身の設定とhooksの通常の信頼処理はCodexに委ねる。

この実行はsandboxによる隔離を提供しない。起動ユーザー、環境変数、利用可能な認証、接続先は運用者が承認範囲に合わせて構成する。CLIの設定仕様は[公式ドキュメント](https://developers.openai.com/codex/noninteractive)を参照する。

## 起動と検証

通常の起動entry pointは `python scripts/ai_task_runner.py --once`。1 invocationで最大1 taskを処理する。連続稼働の起動管理やdeployment用設定は運用環境側で用意する。

変更後のoffline check:

```text
python scripts/check_ai_task_local_runner.py
python scripts/check_ai_task_linux.py
python scripts/check_ai_task_publish.py
python scripts/check_ai_task_github.py
python scripts/check_ai_task_auto_merge.py
python scripts/check_ai_task_deploy.py
```

これらのcheckはfakeや一時fixtureで検証する。checkの実行と実際のrunner起動、認証済み外部操作、productionへの反映を区別する。

追加のPython構文チェックは変更された現存ファイルをcompileする。削除・rename元の欠落ファイルはskipし、通常のsymlinkとPythonのencoding宣言を扱う。ファイル数やサイズを変更ポリシーとして制限しない。これはtask固有のテストに代わるものではなく、Codexは必要なproject testを実行する。

## エラー・停止・再試行

入力エラー、コマンド失敗、timeout、認証不足、lease喪失等は、秘密値を除いた実際の診断として記録する。変更内容を理由にrollbackしない。失敗したworktreeと他者の変更を保持し、作業内容を破棄するcleanupを自動実行しない。

leaseを維持できない場合やキャンセル時はprocess treeを停止し、停止結果を確認する。停止できたことを確認できない場合や外部操作の結果が不明な場合は、確認が必要な状態として引き渡す。ローカルprocessの停止だけではremote deploymentの停止を証明できない。

再試行は既存のtask branch、PR、commit SHA、deployment結果を照合し、重複した公開や別候補への置換を避ける。古いworktreeの削除、force push、公開先の変更を回復策として勝手に行わない。

## deploymentと状態

実運用への反映は構成された対象別adapterが担当し、merge SHAと実際に反映されたSHAを一致させる。Control Planeへの `deploying` 記録とdeploymentの完了記録を分け、完了証明を得てから `completed` にする。設定不足やhealth/migration失敗は実行エラーとして報告する。

状態は `queued`、`running`、`testing`、`ready_for_review`、`deploying`、`completed`、`failed`、`needs_human`、`cancelled`。`ready_for_review` はPR公開に関する既存の状態名であり、runnerに別の内容審査を追加する指示ではない。通常の操作失敗は診断を返して再試行し、設定回数を使い切った場合は `failed` とする。`needs_human` は人間の操作・判断を待つ既存の状態であり、編集ファイルの種別から自動判定しない。
