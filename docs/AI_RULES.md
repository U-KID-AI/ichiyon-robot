# AI開発の実行ルール

## 承認範囲と開発作業

runnerは、認証されたユーザーの依頼を通常のデスクトップCodexと同じ開発作業として扱う。Codexは `danger-full-access`、`approval_policy="never"` で、承認済みユーザースコープ内の調査、編集、ローカルコマンド、テストを実行する。

workflow、migration、Docker/Compose、scripts、bot/admin、Minecraft pack、設定、テスト等の作成・変更・削除・renameは通常の開発対象である。ファイル名、変更内容、symlinkやjunctionであることを理由に変更を拒否・復元したり、人間対応へ送ったりしない。worktreeは作業場所であり、runner独自のfilesystem sandboxではない。

ユーザーの依頼は作業指示である。依頼文字列をshellへ直接渡す受付にはせず、Codexへデータとして渡し、Codexが必要なコマンドを選ぶ。公開処理の引数へ依頼本文をそのまま埋め込まない。

## 設定と認証

Codexは通常のユーザー・project設定、hooks、ツール、環境変数を利用する。runnerは `--ignore-user-config`、独自の設定層検査、credential helper無効化、秘密らしい変数名による環境除去を追加しない。ユーザー自身の設定やOSのアクセス権は引き続き適用される。

承認範囲内で認証や設定を利用することと、秘密値を公開することは別である。秘密値をログ、Discord返信、PR本文、公開成果物へ含めない。報告・エラーには共通の `redact_secrets` を適用し、失敗原因を必要以上に隠さず伝える。runner設定自体はprocess environmentから読み取り、dotenvを暗黙に読み込まない。Codexの開発コマンドが必要なローカル設定を使うことは制限しない。

## 検証と公開

Codexはtaskに合ったテストを実行する。runnerのPython構文チェックは追加の品質確認であり、実行可能なテストや編集対象のallowlistではない。構文エラー、コマンド失敗、timeoutなどは修正と再試行のための診断として扱う。変更内容の許否を判定する自動content reviewは行わない。

commit、task branchへのpush、PR作成、merge、deploymentはrunnerの公開フローが担当する。公開先、task/branch名、lease、対象commit SHA、PR metadata、CI結果、deploymentの完了証明を扱う責務は、編集権限の判定とは分離する。Codexがこれらの公開操作を独自に重複実行しない。

実運用環境への反映は構成済みのdeployment adapterが担当する。task本文を新たな公開先やdeployment用shellとして実行しない。ローカル開発で使うコマンド、migrationやCompose定義の編集を、実運用環境への反映と混同しない。

## 保持する実行時チェック

- task UUIDと `ai/task/<UUID>`、`ai-task-<UUID>` の対応。
- 必須設定、存在する設定先、URL、数値、SHAなどの入力形式。
- 既存worktreeとの名前衝突、process timeout、子process停止、出力上限。
- API認証、claim tokenとlease、公開対象と結果の照合。
- 秘密値のredactionと、失敗時の作業内容の保持。

これらは実行と公開を正しく完了させるためのチェックであり、旧来のchange-policyや段階別の編集拒否を復活させるものではない。
