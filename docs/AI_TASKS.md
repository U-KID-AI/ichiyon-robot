# AI開発タスクの責務と検証項目

この文書は現在のrunnerの責務をまとめる。旧来の段階別計画を実行時の拒否ルールとして使用しない。運用手順は `AI_RUNBOOK.md`、作業範囲と秘密情報の扱いは `AI_RULES.md` を参照する。

## 現在の責務

| 担当 | 内容 |
| --- | --- |
| Discord Bot | 指定channelと許可ユーザーの受付、状態照会、完了通知 |
| Admin / Control Plane | PostgreSQL保存、atomic claim、claim token、heartbeat、lease、状態更新 |
| Local runner | task identity、worktree、Codex起動、検証結果、再試行、公開の進行管理 |
| Codex | 承認済みスコープでの調査、編集、通常のローカルコマンドとproject test |
| Git / GitHub adapters | commit、task branchのpush、PR、CIと候補SHAの照合、merge |
| Deployment adapters | 対象環境への反映、healthと実際に反映されたSHAの確認 |
| Runtime helpers | UUID由来の名前、SHA、worktree名の衝突などの操作前提 |
| Diagnostics | 秘密値を除去しつつ失敗原因と検証結果を報告 |

Codexは `danger-full-access`、`approval_policy="never"` で実行する。通常のユーザー・project設定とローカル環境を使用し、runner独自の編集パス制限、設定層検査、環境変数の除去、変更内容に対する自動reviewは行わない。

workflow、migration、Docker/Compose、scripts、Bot/Admin、Minecraft pack、設定、テストの変更は通常の開発である。symlink/junctionやrootの配置だけを理由に停止しない。commit/push/PR/merge/deploymentは実装用Codexではなくrunnerが担当する。

## 継続して確認する項目

- task UUIDとbranch/worktree名の対応、入力設定の誤りを検出できること。
- ユーザー・project設定、認証用環境、通常のファイル配置で開発ができること。
- 変更の作成・削除・rename、symlink、encoding宣言を追加構文チェックが扱えること。
- Codexが必要なテストを実行し、構文エラーやコマンド失敗を診断として扱うこと。
- process timeout、子process停止、lease喪失時の中断が機能すること。
- ログ、API、Discord、PRに秘密値を出さず、実際の失敗原因を報告すること。
- task branch、commit SHA、PR、CI、merge、deployment結果が同じ候補を指すこと。
- 失敗時に編集内容と他の作業者の変更を保持すること。
- 再試行で公開や通知の重複を抑え、結果が不明な外部操作を確認できること。

## 運用環境で確認する項目

Codex/GitHubの認証、runner設定、deployment対象、適用済みmigration、Botのchannel権限、実際のhealthは運用環境で確認する。offline checkの成功はlive deploymentや本番接続の成功を意味しない。コード変更の依頼を、外部操作や本番への反映を実行した報告と混同しない。
