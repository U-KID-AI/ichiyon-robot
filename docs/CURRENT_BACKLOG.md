# Current backlog — 2026-09-30

この文書を現時点の残タスクの **source of truth** とする。判定はコード、checker、現行CI、GitHubのPR/commitを照合して行い、古いTODOや初回作業の制約だけから未実装と判定しない。「実装済み」はproductionへの適用やMinecraft実機QAの完了を意味しない。

監査基準はfetch済み`origin/main`の`d93718673158ae886b1b598473d9a7c8b688a732`（[PR #109](https://github.com/U-KID-AI/ichiyon-robot/pull/109)）。今回の実装は、このmainから作成した`codex/backlog-cleanup-20260930`に積む。既存`codex/minecraft-fixes-batch-20260924`とその12 commitは保持し、reset/rebase/削除を行わない。

## 実装済み

| 項目 | 現行コード・根拠 | 状態 |
| --- | --- | --- |
| PostgreSQL、guild別repository、Discord OAuth管理画面、権限/feature flag、DB runtime、mode/auto post/deck search、Compose | `migrations/001_initial_schema.sql`と後続migration、`bot/repositories/`、`admin/auth.py`、`admin/main.py`、`bot/services/runtime_db.py` | main実装済み。v2の初期計画を未実装一覧として扱わない |
| Bot instance、Bot/guild scope、Bot切替、ユーザー管理、voice lines | migration 025〜031、`admin/bot_context.py`、`admin/bots.py`、`bot/repositories/permissions.py`、`check_bot_instance_config.py`/`check_bot_scoped_guild_settings.py`/`check_admin_bot_switching.py`/`check_admin_user_management.py` | main実装済み。一般管理者の許可範囲制御は既存機能 |
| X更新通知、取得開始日付きdeck search、コピー/一括ON/OFF、特殊効果の個別倍率上限 | `bot/services/x_update_notifications.py`、`deck_search.py`、各admin/repository、migration 024、`runtime_db.py` | main実装済み。古いv3の「後続」記述は当時の記録 |
| Windows/Linux AI Runner、task受付/lease、publication、exact-SHA deployment、idle reconciliation | `scripts/ai_task_runner.py`、`ai_task_runner_config.py`、`ai_task_local_runner.py`、`check_ai_task_linux.py`、`.github/workflows/checks.yml` | main実装・CI済み。特定Windows PCの常時起動はproduction必須ではない |
| managed Minecraft release、DB素材保存、operation/digest/health証明 | `scripts/ai_task_minecraft_deploy_managed.py`、[managed release](operations/managed-minecraft-release.md) | main実装・CI済み。通常ルートからlegacy Git-pack/SSHへfallbackしない |
| lead locator、Pikachu、cosmetics/avatars/posters/video/Bridge等のpack実装とcheckers | `minecraft/`、各`check_minecraft_*.py`/`.mjs`、`.github/workflows/checks.yml` | main実装・CI済み。lead/Pikachu checkerのCI未登録という古い記述は解消 |
| storage P0 | [PR #103](https://github.com/U-KID-AI/ichiyon-robot/pull/103)、`ai_task_storage.py`、[capacity](operations/storage-capacity.md) | bytes/inodesとapp停止前の容量guardをmainへ統合済み |
| storage P1a | [PR #104](https://github.com/U-KID-AI/ichiyon-robot/pull/104)、`ai_task_storage_retention.py`/`ai_task_storage_retention_graph.py`、[retention](operations/storage-retention.md) | read-only inventoryと参照graphをmainへ統合済み |
| storage P1c-1 | [PR #105](https://github.com/U-KID-AI/ichiyon-robot/pull/105)、`ai_task_backup.py`/`ai_task_storage_cleanup.py`、[recovery](operations/storage-recovery.md) | FULL/split復元契約、隔離restore試験、plan-only eligibilityをmainへ統合済み |
| storage P1c-2A | [PR #106](https://github.com/U-KID-AI/ichiyon-robot/pull/106)、`ai_task_storage_evidence.py`/`ai_task_storage_monitor.py`、[foundation](operations/storage-foundation.md) | durable receipt、attestor、容量monitorをmainへ統合済み |
| storage P1c-2A.1 | [PR #107](https://github.com/U-KID-AI/ichiyon-robot/pull/107)、`ai_task_storage_evidence_store.py`/`ai_task_storage_evidence_classification.py`、[evidence index](operations/storage-evidence-index.md) | SQLite index、lossless A/B、bounded C recent/rollupをmainへ統合済み |
| storage P1c-2B | `scripts/ai_task_storage_maintenance.py`/`ai_task_storage_disposition.py`、`check_ai_task_storage_maintenance.py`/`check_ai_task_storage_disposition.py`、[maintenance](operations/storage-maintenance.md) | 今回実装。独立保管/restoreのfresh attestor、fresh eligibility、receipt/archive保持graph、明示window、exact-path executor、partial failure audit、stale/active処理、fixtureとCIを含む。本番削除は未実施 |
| Minecraft診断改善 | `scripts/minecraft/minecraft_control_api.py`/`minecraft_diagnostics.py`、`check_minecraft_control_status.py`/`check_minecraft_diagnostics.py`、[diagnostics](MINECRAFT_DIAGNOSTICS.md) | #46/#47の有用部分を今回mainへ移植。既存`/status`/`/restart`認証とBridgeの契約を保持 |
| AI task失敗通知の診断順 | `bot/services/ai_tasks.py`、`check_ai_task_discord_channel.py` | #56の有用部分を今回移植。原因を先に示し、全量のredaction済み診断attachmentを維持 |
| YouTube Cookie監視 | `bot/services/youtube_cookie_monitor.py`、`check_youtube_cookie_monitor.py`、`.env.example`、[voice/music](voice-vc-commands.md) | 定期検査、エラー分類、担当Bot、排他、cooldown、通知をmainに実装・CI済み。Cookie自動更新は下の外部前提待ち |

現行mainのGitHub checks成功を確認し、今回変更分は対応checkerとPR CIで検証する。詳細結果は[BACKLOG_TEST_RESULTS.json](BACKLOG_TEST_RESULTS.json)とPR checksに記録する。Windows上で実行できないLinux descriptor/flock試験をPASSと置き換えず、Linux CIで確認する。

## コード完成・production作業待ち

| ID | 残る作業 | 完了条件 |
| --- | --- | --- |
| PROD-STORAGE | P1c-2Bをproductionへ配備し、独立archiveと隔離restore rehearsal、operator dispositionを発行して、新しいinventory/planとmaintenance windowで明示実行する | 固定evidence attestorが証拠を検証できること、planのexact対象とfresh eligibilityが一致すること、成功/partial failure監査が保存されること。backup/release/image/stagingの本番削除は今回一切行わない |
| PROD-ARCHIVE | productionの再帰backupを独立recovery archiveへ切り替える | 元inventoryとdelta、archive publish、FULL/split restore、全保持参照の証明が成立してから切替。未成立中はFULL scopeと既存backupを維持する。[storage recovery](operations/storage-recovery.md)と[maintenance](operations/storage-maintenance.md)を参照 |
| PROD-MC-DIAGNOSTICS | Control API診断モジュールの配備と専用read-only secret、実container/port、startup banner形式、管理networkを確認する | `/status`の既存呼び出しとmanaged releaseが成功し、`/diagnostics`が認証済みのread-only観測だけを返すこと。内向きUDP応答を外部接続やScript polling成功と誤認しない |
| PROD-RELEASE | 今回PRがmainへ入った後、通常のexact-SHA releaseでapp/runnerを更新する | migration、startup SHA、app/Bot healthと対象別deployment証明。コード/CI成功だけで稼働更新済みと扱わない |

`ACTIVE`やstale/owner不明operationは時刻だけで終了扱いにせず保持する。partial failureは消えたmemberと失敗点を監査に残し、残存targetを再評価する。monitorやidle catch-upにcleanupを自動接続しない。A/B receiptはlosslessで保持し、archiveの参照がないという証明を不完全なsamplingで代用しない。

## Minecraft人間実機QA待ち

| ID | 対象 | 人間が確認すること |
| --- | --- | --- |
| QA-LEAD | molcar/molcar2/molcar3/gonta | 既存個体、新規spawn、egg表示、静止/歩行/旋回、プレイヤー/フェンスへのlead、透明化、Content Log。モデル/collision/AI/texture/騎乗の回帰も確認する。[lead anchors](minecraft-lead-anchors.md) |
| QA-PIKACHU | Pikachu表示・操作 | egg、歩行/回転の感触、連打/同tick input、chunk reload、sleep/起床/離脱/pathfindingと同期。[Pikachu](minecraft-pikachu.md) |
| QA-PACKS | cosmetics/avatar/poster/wall/video、直近Vibrant Visuals変更 | productionに配備されたmanifest/world参照と実際の表示・Content Logを照合する。fixtureとheadless検査は端末上の描画証明ではない |
| QA-NETHERNET | MCXboxBroadcastの外部回線friend join | モバイル回線での実joinとsignaling/ICE結果を確認する。loopback Bedrock UDP成功だけでは完了しない。[NetherNet diagnostics](operations/nethernet-diagnostics.md) |

実機QAの完了証拠が取得できていない項目を保持する。過去のホスト名、pack version、成功ログを現在の実測値として再利用しない。

## 外部前提待ち・未実装

| ID | 残件 | 必要な外部前提（1件） | 前提成立後の実装 |
| --- | --- | --- | --- |
| EXT-YOUTUBE-COOKIE | YouTube Cookie完全自動更新 | **運用専用のログイン済みCookie更新元を1つ確定し、更新processから読める状態で提供する**。例は専用Firefox profileで、個人browser/default profileを推測して使用しない | その更新元だけから隔離tempへ抽出し、検査成功後に既存Cookieへatomic publishする。失敗時は既存Cookieを維持し、排他/secret redaction/fixture/CI/env example/docsを追加する |

`try_update_youtube_cookie()`は現在`UPDATE_NOT_CONFIGURED`を返し、`auto_update_configured=False`にする。repository、env example、Composeに専用Firefox profileの設定はなく、今回のWindows hostにも`%APPDATA%/Mozilla/Firefox/profiles.ini`が存在しない。現在のCookieファイルは認証入力であって自動更新元の証明ではない。外部前提がないまま更新成功を返すダミーや個人profile探索は追加していない。既存監視と失効通知は利用できる。

## 将来構想（今回の必須残件と分離）

| 構想 | 現行との境界 |
| --- | --- |
| 全Bot設定の統合横断一覧、倍率上限の一括編集、限定機能の別対象への複製 | Bot選択、許可範囲制御、個別上限、通常のコピー/一括ON/OFFは実装済み。追加UIは仕様を決めて別taskで扱う |
| X API契約/レート制限に応じた監視数の自動調整 | 現行interval/新着取得/取得後filterとは別の最適化。利用契約と負荷を測定してから設計する |
| 任意のAI taskへのtrusted診断snapshot提供、外向きUDP probe、Broadcast build identityの自動証明 | 今回の固定read-only endpointと別のconsumer/外部観測設計。公開先やvantage point、非秘密build証拠を推測しない |
| recovery archiveのcontent-addressed重複排除やreceiptの新しいlossless archive writer | P1c-2Bの保持graphとexact object cleanupは実装済み。将来の保存形式変更はmanifest、参照count、crash recovery、restore契約を別に定義する |
| 共有基盤のcontainer/service名の変更などnaming改善 | [naming audit](naming-audit.md)の後方互換migration案。稼働名を一括変更しない |

## GitHub / stale PR / branch整理

| 対象 | 実コード比較に基づく判定 |
| --- | --- |
| [#46](https://github.com/U-KID-AI/ichiyon-robot/pull/46) | CPU/memory取得はmainに既存。未吸収だったloopback実測runtime version/player countの厳密解析と観測metadataを現行`/status`へ移植し、`friend_join=not_tested`で実際の確認範囲を区別する。古いbranchの直接merge/rebaseはしない |
| [#47](https://github.com/U-KID-AI/ichiyon-robot/pull/47) | 固定read-only diagnosticsの有用部分をmainへ移植し、分離secret、bounded収集とfixture/CIを追加。現行Bridge/managed releaseの成功契約を保持 |
| [#49](https://github.com/U-KID-AI/ichiyon-robot/pull/49) | genericな診断抑制は現行のredaction済み全量attachmentにsuperseded。不要と確認して理由付きでclose済み |
| [#56](https://github.com/U-KID-AI/ichiyon-robot/pull/56) | 原因の表示順改善だけ移植。短文だけに置換して全量診断を失う案は採用しない |
| default branch | `feature/v1-basic-bot`から`main`へ変更済み。旧default固有の2 merge commitはmerge-baseから旧tipへのtree差分が0で、有効な固有変更がないことを確認した |
| remote branches | **削除実施0件**。mainのancestor、unique commit 0、open PR headではない、default/protectedではない条件を全て満たした候補は159/223件。[候補一覧](REMOTE_BRANCH_CANDIDATES.md)を保存。実行直前に全条件を再確認する |

#46/#47/#56のcloseは移植結果を含む新PR公開後、未移植の有効差分が残っていないことを確認して行う。必要変更を残したままcloseしない。PRの最終状態と新PR URLは今回PR/最終報告にも記録する。

## 文書の判定優先順位

1. この文書の分類と、リンクした現行コード/CIを使う。
2. 運用手順は`AI_RUNBOOK.md`、managed release、storage maintenance等の現行契約を使い、各操作前に実環境を確認する。
3. `v2-roadmap.md`、`v3-foundation-design.md`と各`v2-*.md`の初回Phase記録はhistorical。削除せず保存するが、「後続」「未実装」「今回は変更しない」を現在の残件・権限制限へ引き継がない。

旧AI Runner change-policy、編集path制限、変更内容に対する自動content reviewを復活させない。cleanup対象の固定path/identity/reference検証はデータ削除の契約であり、Codexの編集権限を制限する旧policyとは別である。
