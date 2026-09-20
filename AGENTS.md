# AI開発エージェント向けルール

- 変更前に既存実装と関連ドキュメントを調査する。
- 推測でファイル構成、API、本番状態を決めない。確認できない事項は要確認とする。
- AI専用のbranch/worktreeで作業する。
- 無関係な変更を混ぜない。
- 変更後は関連テスト、compile、checkを実行し、結果を確認する。
- `.env`、secrets、SSH鍵、Token、Cookie、その他の認証情報を読まない、表示しない、コミットしない。
- 本番デプロイ、本番再起動、本番DB migrationを勝手に行わない。
- Minecraft本番worldを勝手に変更しない。
- Discordから任意のshellコマンドをそのまま実行する設計にしない。許可された固定操作と入力検証を使う。
- 危険操作や外部副作用のある操作は人間承認を要求する。

調査・編集・検証はAI専用worktreeで行う。commit、push、Draft PRは `docs/AI_RULES.md` で許可された場合のみ行う。mergeとproduction操作は行わない。

## Phase 3C-1D: durable deployment lifecycle

Generic Codex/task execution may not access production, SSH, secrets, restart,
migration or rollback. Production deployment may only be performed later by a
separately implemented fixed-operation deployment adapter. Its only task-derived
input may be a validated reviewed merge SHA; deployment configuration must be
internally configured and trusted. Discord/task text must never specify shell
commands, argv, production hosts, SSH paths, deployment paths, environment
variables, SQL or rollback commands.

Phase 3C-1D does not implement or authorize real deployment transport. The existing
guarded merge runner records the exact reviewed merge metadata durably in the
Control Plane before invoking any injected deployer. The lifecycle is
`testing -> deploying -> completed`; completion requires an exact deployed SHA
matching the stored merge SHA. Without a deployer it fails closed for human
inspection. Generic task execution gains no merge or production authority.
Stale deploying leases become `needs_human`; automatic reconciliation/reclaim is
deferred to the fixed idempotent deployment adapter phase.
