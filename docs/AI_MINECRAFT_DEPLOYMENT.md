# Minecraft deployment verification

AI Runner の `completed` は、レビュー済み merge SHA のコードが対象の本番環境へ実際に反映され、対象ごとの機械検証が成功した場合だけ許可する。

## Deployment targets

すべての AI task は既存の immutable application deployment を `apps` target として要求する。

レビュー済み merge の変更ファイルに `minecraft/` 配下が1件でも含まれる場合のみ、追加で `minecraft` target を要求する。

Minecraft に関係しない task では BDS adapter を初期化せず、BDS SSH・Docker・world 状態を completion の条件にしない。

Minecraft task の completion には `apps` と `minecraft` の両方の verified target が必要である。

## Trusted Minecraft runtime configuration

Minecraft deployment の接続先やパスは task text や Codex の出力から取得しない。

Runner の trusted environment から以下を取得する。

- BDS SSH executable
- fixed SSH host
- fixed SSH user
- dedicated SSH private key
- fixed known_hosts file
- BDS data root
- world name
- Docker container name

SSH は BatchMode、IdentitiesOnly、StrictHostKeyChecking、fixed known_hosts、ForwardAgent disabled、ClearAllForwardings を使用する。

task text から任意の host、path、container、command を選択することはできない。

## Exact reviewed source

`scripts/ai_task_minecraft_runtime.py` の `ExactMergeSource` は、固定された repository と origin/main に対して reviewed merge SHA を検証する。

Minecraft artifact はその exact commit から `git archive` で取得する。

pack archive は transport 前に検証し、以下を拒否する。

- path traversal
- absolute path
- symlink
- hardlink
- special file
- Minecraft pack root 外のファイル
- file count / archive size limit 超過
- malformed manifest
- malformed or duplicate JSON keys

archive から deterministic SHA-256 tree digest を生成する。

## BDS transaction

`scripts/ai_task_minecraft_deploy_remote.py` が BDS 上の transaction を担当する。

repository archive に含まれる behavior pack / resource pack を generic に処理する。

各 pack の `manifest.json` から UUID と version を取得し、

- `world_behavior_packs.json`
- `world_resource_packs.json`

の一致する UUID の version を同期する。

無関係な world pack entry は保持する。

AI Runner が管理した pack の状態は BDS data root の
`.ichiyon-ai-managed-packs.json`
に記録する。

### Actual-content no-op

manifest version だけではなく、配置済み pack のファイル集合と内容を repository artifact と比較する。

pack tree と world references が既に一致する場合は no-op とし、BDS container を停止・再起動しない。

### Changed deployment

実際の差分がある場合のみ、

1. 現在の BDS health を確認
2. staging を作成
3. 現在の managed pack / world references / state を backup
4. BDS container を停止
5. validated pack を配置
6. world references と managed state を atomic write
7. BDS container を起動
8. sustained health check
9. 配置済み pack tree と world references を再検証

を行う。

transaction に失敗した場合は backup から rollback を試み、成功 proof を返さない。

deployment adapter は raw remote error や秘密情報を completion message に流さず、failure closed とする。

## Runtime proof

Minecraft deployment 成功時の remote protocol は固定された以下の proof のみを受理する。

- `MINECRAFT_DEPLOY_RESULT=SUCCESS`
- exact `DEPLOYED_COMMIT_SHA`
- exact `MINECRAFT_TREE_SHA256`
- `MINECRAFT_CHANGED=0|1`

余分な行、SHA mismatch、tree hash mismatch、不正な changed flag は失敗として扱う。

`completed` に進めるのは、要求された全 target の exact deployment proof が揃った場合だけである。

deployment 開始後の failure は completion に進まず `needs_human` とする。

## Idle catch-up

Runner が task を claim せず `NO_TASK` で終了する invocation のみ、trusted local `origin/main` に対して Minecraft catch-up を best-effort で行う。

task を claim した invocation では catch-up を実行しない。そのため、非Minecraft task が BDS SSH・Docker・world 状態へ依存することはない。

catch-up failure は ordinary task の completion result を変更しない。

成功済み digest を永続的な「処理済み」判定には使用せず、後続 run でも actual remote content を再検証できる。

同一 tree の場合は BDS restart を行わない。

## Verification performed

2026-09-22 に本番 BDS への実 transaction を実施し、以下を確認した。

- reviewed main SHA:
  `a713665ab6136d56143b5ff4d11248015cbce6b7`
- `ichiyon_avatar_bp`: `1.0.28`
- `import_structures`: `1.0.27`
- `ichiyon_avatar_rp`: `1.0.30`
- world behavior/resource pack references synchronized
- managed state created
- deployed tree hash:
  `c1300d86e7bfa3d794a9350d63402ae3b9aebbf8a740f394ccfb73f34e24c676`
- BDS container returned to `running`
- Docker health returned to `healthy`

同じ exact tree を再度 deployment した際には container `StartedAt` が変化せず、actual-content no-op が BDS を再起動しないことも実機確認した。

rollback path は offline safety tests で検証しているが、本番 BDS を意図的に破損させる fault injection は実施していない。

## Tests

CI の AI automation safety checks で以下を実行する。

- `scripts/check_ai_task_minecraft_deploy.py`
- `scripts/check_ai_task_minecraft_runtime.py`

既存 AI Runner / deploy / Linux checks と合わせて実行する。

AI Runner の local test registry は引き続き repository Python を実行せず、sandbox 外では static syntax compilation のみを行う。この安全境界は変更しない。

## Human verification boundary

machine verification は pack の exact content、world references、container startup、health までを保証する。

Minecraft クライアント上での見た目、音、操作感など、本質的に gameplay を必要とする最終確認は人間による確認対象として残る。