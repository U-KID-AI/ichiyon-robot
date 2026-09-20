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


## Phase 3C-2E: fixed deployment adapter (offline review only)

Generic Codex/task execution still cannot access SSH, production or secrets.
Only the separately reviewed fixed deployment adapter may perform production
operations after a future explicitly authorized activation. Its deploy() input
is only the reviewed lowercase 40-character merge SHA and heartbeat stop_event.
Transport configuration comes from trusted runner process environment, without
dotenv. Paths/service names/operation vocabulary are fixed in reviewed code;
task or Discord text cannot select them.

The existing reviewed merge -> mark_deploying -> deploy -> exact SHA proof ->
mark_completed ordering is unchanged. Phase 3C-2E implements an offline-reviewed
adapter but does not wire it into runner startup (Phase 3C-2F) and does not
authorize a live deployment test. Stale deploying automatic Control Plane reclaim
is still NOT enabled; expired leases require human inspection.

App cutover includes only admin, bot and bot-irsia. DB and youtube-vpn-proxy are
infrastructure, remain owned by legacy Compose, and must not be recreated or
restarted. The dirty legacy repository is never a release source. Releases use
an exact-main clean archive, immutable image, fixed persistence mounts, validated
backups and migration/health proof before atomic current-pointer publication.
Automatic destructive DB restore is forbidden. Failure attempts app-only rollback;
migrations are not reversed, so schema backward compatibility needs human review.

Same-SHA current retries revalidate actual health/image/migration/infrastructure
state without restarting apps. Complete prepared releases and complete validated
backups can be reused. An incomplete published release or incomplete backup
fails closed for human inspection. Backups publish atomically from private staging
after full dump/archive/metadata validation; interrupted staging remains for audit.
Normal completion removes only this attempt's fixed-prefix temporary paths.
The protocol establishes umask 077 before creating any deployment files.
No marker file alone constitutes success. SSH loss is not cancellation proof:
the remote lock serializes surviving children, and a future retry re-probes state.
Migration containers have a fixed SHA-derived name. Reconciliation proves exact
image, operation and network identity; running or ambiguous containers block
deployment and rollback without being killed. Successful exited containers require
actual database version proof; only proven failed target containers may be recreated.
Previous releases require only the audited immutable artifact contract, with no
new deployment marker files.

Transport settings: AI_TASK_RUNNER_DEPLOY_SSH_PATH, SSH_HOST, SSH_USER,
SSH_KEY_PATH and KNOWN_HOSTS_PATH (each with AI_TASK_RUNNER_DEPLOY_ prefix).
SSH_USER must be ubuntu. Optional TIMEOUT_SECONDS (positive, at most 7200) and
MAX_OUTPUT_BYTES (8192 through 1048576) use the same prefix. Credential file
contents are never read by configuration validation. No concrete host, key path
or credential value belongs in repository configuration.

Deployment preflight requires existing normal release/shared/backup directories,
shared mode 700, shared .env mode 600, an existing immutable current release,
Python 3, git, flock, curl, tar and Docker Compose supporting !reset/!override.
This phase validates these assumptions with offline fakes and static inspection;
prior release artifact formats, production compatibility and migration rollback
compatibility remain independent
human audit items before Phase 3C-2F activation.
