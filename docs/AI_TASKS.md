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

## Phase 1: タスク受付基盤（将来）

- DiscordからAI開発taskを受け付ける。
- task IDを発行する。
- task queueを設ける。
- task stateを保存する。
- taskごとに専用worktreeとbranchを自動生成する。

受付可能なタスク形式、認可ユーザー、保存先、再実行・重複排除方針は要設計。

## Phase 2: 実行とDraft PR（将来）

- Codexを非対話で実行する。
- 変更内容に応じてtestを自動選択する。
- 結果を確認してcommitする。
- pushする。
- Draft PRを作成する。
- テスト結果と変更内容をDiscordへ報告する。

任意shell実行の受付、秘密情報の受け渡し、本番環境への接続は設計対象外または禁止対象である。

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
