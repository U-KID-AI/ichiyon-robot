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

## 長時間処理・本番投入時のリソース節約

ユーザーの時間、Codexの利用枠、ツール実行回数は有限。**検証のための検証や、待機中に行う目的のない調査を増やさない。** 既存の実行器・デプロイhelperが持つ確認と復旧機構を再実装・重複実行しない。

- **最小の調査で着手する。** 既存ドキュメントと対象コードを一度確認し、実行経路と最終的な成功条件を決める。同じ情報を別コマンド、別ホスト、別APIから繰り返し取り直さない。
- **CIがすべてPASSなら迅速にリリースへ進む。** ユーザーが本番投入を承認している場合、具体的な障害・未解決の必須条件がない限り、追加の隔離環境試験、手動の実機試験、別レビューを勝手に挟まない。既存CI、標準のmigration・backup・rollback・health確認は省略しない。
- **デプロイは既存の入口から一度だけ実行する。** Managed Minecraft Releaseでは `docs/operations/managed-minecraft-release.md` の既存helper/runnerを用い、同じSHAの二重リリース、手作業の独立SSH配備、Git原本によるDB素材の上書きをしない。
- **長時間処理中はそのプロセスに実行を委ねて待つ。** 正常に進んでいるバックアップ・ビルド・migration・パック反映を、別SSHセッションで繰り返し `ps`、`docker ps`、`readlink`、ファイル一覧、Control API等に問い合わせて監視しない。元の実行の待機・終了結果を優先する。
- **Codex自身の追加確認は原則「開始前の必要最小限」と「終了後の結果確認1回」。** helperの最終出力がSHA、migration、operation、digest、BDS health等を証明するなら同じ項目を個別に再照会しない。証拠が欠けている場合に限り、欠けた項目だけを一度問い合わせる。エラーや停止・停滞の客観的兆候がある場合は対象を絞った追加診断を許す。
- **残枠不足・長期待機時は引き継いで終了する。** 実行中のリモート処理をむやみに中断したり重複起動したりせず、現在のSHA・operation ID・到達段階・次に参照すべき結果を短く報告して会話を終える。「処理中」を「成功」と書かない。監視するためだけにCodexを動かし続けない。
- **報告は節目・失敗・完了時のみ簡潔に行う。** 進展のない「引き続き監視します」の反復や、完了した項目の再説明を避ける。

ここで抑制するのは**エージェントによる重複した手動確認と無目的なツール実行**である。デプロイ実行器自身の必要なpoll、heartbeat、ロック、復旧、完了証明は削除・省略しない。

## Minecraftの容量管理

- 小修正は所有・ブランチ・未保存差分を確認して既存worktreeを再利用する。並行作業の隔離、異なる基点、runner専用契約などの必要性がなければ新規clone/worktreeを作らない。
- 作業開始時に `python -B scripts/minecraft/minecraft_storage_plan.py --project . --capacity-only` 等で空きを一度確認する。Windows 100GB未満、Ubuntu 10GB未満は警告。Ubuntu 10GB未満で新規大容量テストを作らない。復旧経路は停止しない。
- 最小限のofflineテストを先行し、同一入力・runtime・probeの検証済み環境/証明を再利用する。失敗環境や固有worldは上書きしない。cached proofを今回実機テストした結果と報告しない。
- 本番変更前の復旧ポイント、現行・直前成功・world由来・明示pin・private原本を保持する。稼働データをhardlink共有しない。`.minecraft-storage` は復旧原本であり削除可能キャッシュではない。
- 整理候補はdry-runのみ。年齢・catalog digest・空き不足だけで削除しない。実削除は別の明示的な依頼とfreshな参照/hash/復旧確認を要する。pruneや自動削除を追加しない。
- `%USERPROFILE%\Minecraft-Preservation\20261027-115643-JST` は変更禁止。private素材・音源・world・CAS blobを公開GitHubへ追加しない。
- 完了時に、新規作成容量・残した環境・復旧元を記録する。配備/隔離helperの `storage-receipt.json` を一次資料とし、同じ大規模計測を繰り返さない。
- 詳細は [Minecraft容量管理](docs/operations/minecraft-storage-management.md)。ローカル実装と本番適用を区別する。
