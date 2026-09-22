# AI開発エージェント向けルール

- 変更前に既存実装と関連文書を読み、確認できない事項は要確認として扱う。
- ユーザーが承認した作業範囲に従う。runner taskは専用branch/worktreeを使い、共有worktreeを指定された場合は担当範囲と他の作業者の変更を尊重する。
- 通常のデスクトップCodexと同じように、必要なファイルの作成・編集・削除・rename、調査、ローカルコマンド、テストを実行する。
- workflow、migration、Docker/Compose、scripts、bot/admin、Minecraft pack、設定、テストをファイル種別だけで除外しない。symlink、junction、通常のユーザー設定・project設定もrunnerの変更ポリシーで拒否しない。
- 無関係な変更や他者の変更を取り消さない。失敗した場合も作業内容を保持する。
- 関連テスト、compile、checkを実行し、変更内容、実行した検証、未確認事項を報告する。
- 承認範囲内でローカルの環境変数、認証、ツールを利用できる。秘密値をログ、Discord返信、PR本文、公開成果物へ出さない。
- runnerでのtask publicationはrunnerが担当する。実装用Codexはcommit、push、PR作成、merge、deploymentをrunnerへ委ねる。
- runnerは変更内容の許否を判定する自動content reviewを行わない。task identity、設定の入力エラー、process timeout、lease、公開先とSHAの確認は維持する。

## 実行モデル

Codexは `danger-full-access` と `approval_policy="never"` で実行する。これは承認済みユーザースコープ内での非対話実行であり、操作対象を勝手に拡張する許可ではない。runner専用の編集パス制限、環境変数除去、Codex設定層の検査は設けない。

通常のユーザー・project設定を読み込み、ローカル開発に必要なコマンドを使用する。変更内容に対する旧来の段階別権限表や拒否ルールは使用しない。運用は `docs/AI_RUNBOOK.md`、共通知識は `docs/AI_CONTEXT.md`、責務は `docs/AI_RULES.md` を参照する。
