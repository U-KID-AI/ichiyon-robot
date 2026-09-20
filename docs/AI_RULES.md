# AI権限と安全ルール

AIの操作は、影響範囲に応じて3段階に分ける。判断できない操作は上位レベルとして扱い、人間承認を求める。

## Level 1: 自動実行可能

- repository read/search
- AI専用worktree内の編集
- static analysis
- compile
- unit/check系テスト
- `git diff` / `git status` 確認

実行前に既存実装と関連文書を調査する。秘密情報を読む、表示する、ログへ出す操作は含まない。テストやcheckが外部環境・本番データへ副作用を持つ場合はLevel 3相当として扱う。

## Level 2: PRまでは自動化可能

- AI専用branch作成
- commit
- push
- Draft Pull Request作成
- テスト結果と変更内容のDiscord報告

対象はレビュー可能なコード・文書変更に限る。無関係な変更を混ぜず、commit前に差分と対象ファイルを確認する。PR作成後もmergeやdeployは自動で進めない。

## Level 3: 人間実行へ引き渡し

- `main` へのmerge
- production deploy
- production restart
- production DB migration実行
- production secrets変更
- production firewall/network変更
- Minecraft本番world変更
- production data deletion
- production rollback
- その他、production環境に副作用を与える操作

productionに関するLevel 3操作は、人間の承認を得てもAI Runner自身は実行しない。AI Runnerは `main` merge、production deploy、production restart、production DB migration、production secrets変更、production firewall/network変更、Minecraft production world変更、production data deletion、production rollbackを実行しない。必要になった場合は、対象、影響、必要な操作、検証結果、復旧方法を提示し、taskを人間へ引き渡す。実際の操作は人間が外部で実行する。将来この方針を変更する場合は、別途明示的な設計変更が必要である。stagingの扱いは今後のPhaseで別途定義し、productionと混同しない。

## 共通禁止事項

- `.env`、secrets、SSH鍵、Token、Cookie、認証情報を取得・表示・コミットしない。
- Discordから受け取った文字列を任意shellコマンドとして実行しない。
- 推測でAPI、環境状態、ファイル構成、権限を決めない。
- 本番worldや本番DBを検証用データとして扱わない。
- 承認されていない外部送信、削除、再起動、デプロイを行わない。
## Phase 2A

AI Task Control Planeは専用Bearer tokenと固定endpointで保護し、任意SQL、任意status、任意column、任意shellを公開しない。claim tokenとleaseを全Runner更新で検証し、期限切れtaskをqueuedへ戻さない。Phase 2AではCodex、Git、GitHub、worktree、production/staging操作を実行しない。IP制限はX-Forwarded-Forをアプリケーションで信頼せず、将来reverse proxyまたはfirewallで行う。
## Phase 2B Local Runner

Windows Local Runnerは専用API tokenだけを使い、DBへ直接接続しない。Codex argvは固定し、任意flag、任意shell、任意Git操作、task本文由来のpath・command・flagを許可しない。`--dangerously-bypass-approvals-and-sandbox`、danger-full-access、add-dir、worktree、skip-git-repo-checkは禁止する。commit、push、PR、merge、staging、production操作はPhase 2Bで実行しない。
RunnerはPATH上の`git`や`python`を実行せず、検証済み絶対pathと`sys.executable`を使用する。Git argv、test argv、process tree停止argvは固定allowlistとし、Codex childだけcredential helperを無効化する。

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
