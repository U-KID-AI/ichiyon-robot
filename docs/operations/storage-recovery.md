# Production storage recovery contract (P1c-1)

この文書はP1c-1の復元契約とhistoricalな観測記録。後続P1c-2A/P1c-2A.1のv2 FULL writer、durable evidence/indexはmainに実装済み。[現在の基盤契約](storage-foundation.md)を併読する。P1c-2Bのfresh disposition/eligibility、保持graph、明示window、exact-path executorとpartial failure auditは[storage maintenance](storage-maintenance.md)を参照する。FULL scopeとproducer migrationのplan-onlyは維持し、cleanupコード完成とproduction実行を区別する。今回production削除は実施していない。

P1c-1はbackupの復元契約とcleanupの判定を実装する。productionのmove/copy/delete、scheduler変更、merge、deployを行わない。**現行deploymentのtar scopeは `data`、`assets/images`、`secrets`、`.env` のまま維持し、`data/backups`を除外しない。** 以下の履歴調査とfixtureの成功は、productionの全データを復旧できる証明とは区別する。

関連文書: [容量guard](storage-capacity.md)、[参照graphとretention](storage-retention.md)。P0の容量guardを回収executorに変えず、P1aの参照保護を緩めない。

## 一次資料と観測時点

今回のcode traceはbase `78e2d7040d75680aaf764410a19afbc8423374fb` と既存PC保存資料を使用した。productionへ接続して再計測していない。

| 保存資料 | この文書で使う事実 |
| --- | --- |
| `oci-disk-investigation-20260928/REPORT.md` / `APPENDIX.md` | 最初のENOSPCはJDK展開・Dockerログ。後続backupが約242 MiBで中断。48完了backup、旧形式2件、中断2件などの当時の在庫 |
| `storage-p0-validation-20260928/REPORT.md` | deploy/Runnerの事前容量判定、app停止前の再計測、rollback余裕。scope変更なし |
| `storage-p1a-validation-20260928/REPORT.md` / `inventory.csv` | 参照graph、legacy/不明なstagingの保持理由、世代単位のchecksum検証 |
| 同directoryの `backup-recursion.json` / `backup-recursion-provenance.json` / `BACKUP_RECURSION.md` | 2026-09-28 06:18 UTC時点の8 directory、96 JSON、各memberの一致と一回限りのproducer記録 |
| `storage-p1b-maintenance-20260928/REPORT.md` | fresh候補42 backup / 7.421448 GiBのみ削除。保持7完全backupのreadiness成功。release/image/stagingとshared/data/backupsは維持 |

上記directoryは調査PCの `C:/Users/syoub/codex-work/` 配下に保存されている。秘密値やDB/JSON本文をrepoへ複製しない。改変検出用の資料SHA256は次のとおり。

```text
backup-recursion.json
4695379dd034e48356ea3a9530114016f8f13dd0709bd2dd9ec8941482fb5679
backup-recursion-provenance.json
0632d4c46bdf1423bc3a776e32a2a4e2bf19c454f3ca72240ca332fb602759b2
inventory.csv
40ee9f2c38f77698483b4031f953107ef932f27162d86cdd886b9ee19665f594
```

P1b直後の空き16.229 GiB / 使用率63.89%は削除直後の値。後続通常deployment後の最終観測は空き15.292 GiB / 使用率65.97%で、idle巡回の一時sourceも含んでいた。これらをP1c-1時点のfresh容量として使わない。

## `shared/data/backups` の由来と読み手

P1a実測は12,762 regular files / payload 286,769,111 bytes。8 pack operation directoryの割当は324,411,392 bytes、96 flat JSONは589,824 bytes、rootを含む全体は325,005,312 bytesだった。全tar payloadの約86.8%はこの履歴資料だった。

以下の一回限りのscriptは保存済みsourceを読んだだけで、今回実行していない。`baseline` は当時のLIVE BDSから取得したpack原本、`source` はbaselineへ許可された変更を重ねた入力、`prepared-pack.zip` は当時のDB catalogとrevisionを含む生成物である。全directoryが単なるGit checkoutの複製という意味ではない。

| subtree | 割当bytes | 作成元 | 読込み元・固有の復旧資料 |
| --- | ---: | --- | --- |
| `molcar-accessories-v1-20260923` | 21,925,888 | `staging/molcar_accessories_v1_release/manage_release.py:stage/register` | 同scriptの`validate/register/compare/apply`。LIVE baseline、原素材`accessories.zip`、`database-before.json`、登録結果、生成ZIP。登録前DBと原素材を保持する |
| `molcar-creative-groups-20260923` | 47,136,768 | `staging/molcar_creative_groups_release/manage_groups.py:prepare/repair` | 同scriptの`apply/repair`。baseline/source、初回ZIPと修正版、両方のproof。初回prepared ZIPには不正なgroup指定があり、`repaired-pack.zip`と同等扱いしない |
| `mokuro-release-20260923` | 42,049,536 | `mokuro_release/manage_mokuro.py:prepare` | 同scriptの`apply`がZIPとcatalog/hash proofを読む。LIVE baselineに選択したGit変更を重ねたsourceを保持 |
| `mokuro-idle-walk-20260923` | 42,225,664 | `mokuro_idle_walk/release.py:prepare` | 同scriptの`apply`。LIVE baseline、4変更fileを重ねたsource、生成ZIP/proofを保持 |
| `mokuro-animation-ids-20260923` | 42,237,952 | `mokuro_animation_ids/release.py:prepare` | 同scriptの`apply`。LIVE baseline、2 RP変更fileを重ねたsource、生成ZIP/proofを保持 |
| `garbage-molcar-kuma-paw-20260923` | 42,979,328 | `garbage_molcar/release.py:prepare` | 同scriptの`apply`。LIVE baseline、限定した意味上の変更、baseline/new hash、生成ZIPを保持 |
| `garbage-pickup-mokuro-jump-20260923` | 43,130,880 | `garbage_pickup/release.py:prepare`の旧版 | 当時の`apply`。LIVE baseline、7変更file、生成ZIP/proof。旧pathのsourceは保存された作成記録で照合 |
| `garbage-follow-recovery-20260923` | 42,725,376 | 同scriptの後続修正版`prepare` | 後続`apply`。新しいLIVE baseline、1変更file、生成ZIP/proof。旧pickup directoryを上書きしたものではない |
| flat JSON 96件 | 589,824 | `admin/main.py:backup_json_file`、`bot/data_store.py:backup_json_file`と同じ形式。初期形式変換snapshotも含む | 通常読込みは現行`data/*.json`。backupの自動restore読込みは見つからないが、更新前の値は手動復元資料。全96件の個別producerは確定していない |

最初の2 scriptの保存元は `C:/プロジェクト/ichiyon-robot/staging/`、残りは `C:/Users/syoub/.codex/tmp/` 配下。旧pickup版とfollow修正版の対応は `backup-recursion-provenance.json` の2026-09-22T22:23/22:46 UTC作成記録で確認した。これらは現repoの通常deployment entry pointではないため、運用再開を促す実行コマンドは記載しない。

### runtime / pack deployment / rollback / 災害復旧の区別

| 内容 | runtime | Minecraft pack deployment | app rollback | 障害復旧・再生成 |
| --- | --- | --- | --- | --- |
| 現行`data`、env、secrets、assets/images | appの永続データ。現行機能が使うため包括保持 | DB catalogやimmutable sourceとともに必要 | shared persistenceを継続使用 | DB dumpと合わせて必須。manifestの対象から落とさない |
| 8 dated pack directory | 通常runtimeが読む経路は見つからない | 過去の一回限りのapply/repairは読む。現在のmanaged pack生成は読まない | previous Compose/imageへのapp rollbackでは通常展開しない | 過去LIVE baseline・登録前DB・修正経緯の復旧資料。byte一致再生成の証明なし。archive候補だが廃棄候補ではない |
| flat JSON history | 現JSONの通常読込みには不要。ただしproducerが今も存在する | managed pack生成には使わない | 通常rollbackで自動展開しない | 現JSON/現DBから過去値を戻せるとは限らない。継続追記され得るarchive資料 |

現在の読み込み経路は、[`admin/minecraft_cosmetics.py:records`](../../admin/minecraft_cosmetics.py) → [`bot/repositories/minecraft_cosmetics.py:assets/deleted_assets`](../../bot/repositories/minecraft_cosmetics.py) → [`bot/services/minecraft_cosmetics_pack.py:pack_zip`](../../bot/services/minecraft_cosmetics_pack.py)。Git builtins、PostgreSQL blobs/tombstones、immutable releaseのMinecraft sourceを使う。[`admin/minecraft_release.py:build_archive`](../../admin/minecraft_release.py) がこの経路でControl APIへ渡すZIPを作る。

`revision()` はDB sequenceを進めるため、code traceのためにexport APIやpack生成を実行しない。現在のpackを生成できても、過去のLIVE baselineや当時のcatalog/revisionを同じbyteに戻せる証明にはならない。

BDS側の [`minecraft_cosmetics_apply.py:_backup/_restore`](../../scripts/minecraft/minecraft_cosmetics_apply.py) は `cosmetics-applications/<operation>/original` を使用する。app側8 directoryとは別契約。BDS側の現存・完全性は今回測定しておらず、app側原本の代替と扱わない。

[`ai_task_deploy_remote.sh:on_exit`](../../scripts/ai_task_deploy_remote.sh) のapp rollbackはprevious Compose/imageでappを再作成し、shared tarを自動展開しない。一方、災害復旧用persistence tarのscopeにはこれまで履歴も含んでいた。通常rollbackが読まないという理由だけで、災害復旧の契約から削れない。

[`admin/main.py`](../../admin/main.py) のquotes/reactions/ng_words/kuji保存と、[`bot/data_store.py`](../../bot/data_store.py) を呼ぶJSON backendの更新は、現在も `data/backups` に更新前JSONを書く。legacy画面は既定で非表示でもcodeは残る。productionの全producerが停止したとは確認していない。**一度archiveしただけでdirectory全体を今後のbackupから除くと、それ以降のsnapshotが欠落する。** scope分離にはproducerの移行または毎回のdelta収録が必要。

### durableな重複保存の証拠と限界

P1aで `ichiyon-backups/60099292444e1b6719765b096b68cbb71fa05f89/persistence.tar` を検証した。tarは354,109,440 bytes、SHA256は `90ce86516b5227211bf4653d85b03cbff87b8444658bb1581cdc785869355203`。manifest一致に加え、`data/backups` 配下12,762 regular filesについてliveとのsize/hash一致、欠落0、不一致0を確認した。

これは同一host/filesystem上の別世代への収録を証明する。off-host保存、DB実restore成功、全recovery手順成功の証明ではない。P1bはその後に保持backup 7件のchecksum、`pg_restore --list`、全tar payload・終端、previous/image参照を確認したが、実DBへのrestoreは行っていない。P1c-1も過去の資料を現在のfresh inventoryとして扱わない。

## version付きbackupとrestoreの境界

[`scripts/ai_task_backup.py`](../../scripts/ai_task_backup.py) のv2は、live persistenceとrecovery archiveの役割をmanifestで明示するための契約である。既存6-file backupの読取りを維持する。READY単独、dump header単独、tarのmember名だけをもってrestore可能とはしない。

v1の固定6ファイルは `READY`、`previous`、`infra.json`、`production.dump`、`persistence.tar`、`checksums.sha256`。v2はこれに `manifest.json` を加える。v1のchecksum対象はdump/tar、v2はそれにmanifest/previous/infraを加えた固定5ファイルである。validatorはREADYの空file、exact file集合、previousのcanonical参照、target/previous双方のrelease metadataを確認する。保存済みComposeやvalidatorを実行することはない。

- v2にはversion、target/previous release、scope、各payloadのchecksum、restoreに必要な役割を明示する。unknown version、欠落、余分なscope、checksum不一致は成功扱いしない。
- full preservationでは現行4 rootをすべて収録し、`data/backups`も包含する。split形式は同じ復元結果を独立archiveと合成できることをvalidation/offline rehearsalで試す契約であり、production writerへの除外設定ではない。
- dumpはchecksumに加えて`pg_restore --list`を通す。tarは全regular payloadと終端を読み、scope、型、正規化pathを検証する。absolute path、`..`、symlink/hardlink、特殊file、重複path、file/directory衝突を拒否する。
- previous参照とprevious release/image/Composeに依存するrollback contractを保持する。rehearsalはisolated scratch内であり、productionのprevious pointerやshared persistenceを書き換えない。
- 旧manifestのないbackupにv2のscopeを推測で付与しない。現行6-fileは既存契約で検証し、それ以前のlegacyや不明な形式は引き続き参照を保護して`NEEDS_REVIEW`とする。

retention collectorのv2分岐はこのvalidatorを使い、`pg_restore --list`の成功を必須にする。hostにpg_restoreがない、archiveが読めない等の場合は`verified`にせず保護する。v1 collectorはP1aのdump header/checksum/tar scope検査を維持する。v1を新validatorで明示的に検証する場合は同じくpg_restoreを要求する。v2の`recovery_archive_ids`は参照証拠として出力するが、archive回収executorやarchiveの自動GCは実装しない。

v2 manifestの固定構造は次のとおり。任意のinclude/exclude式や任意のrecovery pathを解釈しない。

| field | 契約 |
| --- | --- |
| `format` / `version` | `ichiyon-production-backup` / `2` |
| `target_release` / `previous_release` | exact release SHA。backupのprevious参照と一致させる |
| `persistence.include` | `data`, `assets/images`, `secrets`, `.env` の固定集合 |
| `persistence.exclude` | full形式では空、split形式では正確に`data/backups`だけ |
| `recovery` | full形式ではnull。split形式では`archive_id`だけを持ち、固定root内のSHA256 IDへ解決 |
| `inventory` | 復元後の完全なtreeの`path`, `type`, `size`, `sha256`。persistenceとrecoveryを合成したmember集合・hashの一致を要求 |

split形式で`data/backups`がpersistenceから外れていても、固定archiveから同じpathへ復元され、merged inventoryから1 byteも欠けないことを検証する。archive不存在、不一致、未記録member、live側とarchive側の衝突は成功扱いしない。archive作成後に増えたJSONを古いinventoryに記載しないだけで見落とすことを防ぐため、**productionで使うwriterには採取時のsource全体とmanifestの一致が別途必須**。offline bundle validator単独で、現在live filesystemの全fileを保証するものではない。

fixtureでDB dump、env contract、secrets、runtime data、image/assets、previous参照の往復を確認する。fixtureのsecretは合成値だけとし、失敗時も値を診断へ出さない。実DB restoreを行う場合は、明示的なopt-inのdisposable PostgreSQLに限定し、production DBへ接続しない。

`restore_files()`はOS temporary領域の空directoryだけを受け取り、production rootへの復元を拒否する。固定metadataのcopyと検証済みtar memberの個別書込みだけを行い、tarの任意pathをそのままextractしない。POSIXではdirectoryを0700、fileを0600で作る。全memberとcopy後metadataのhashを検証し、sourceをもう一度validateして変化を拒否する。original owner/modeをproductionへ適用したり、Docker imageをimportしたり、previous release一式を自動復元したりする機能ではない。

### 実行済みrestore rehearsalと制約

[`check_ai_task_backup_restore.py`](../../scripts/check_ai_task_backup_restore.py) は2026-09-28、調査PCのPostgreSQL **16.15**を明示指定し、実DB fixtureのrestoreを成功確認した。最終test件数とplatform依存skipは実行ログ/CI結果を参照する。新規のprivate clusterをloopback・一時portで起動し、既存DB/container/serviceへ接続していない。終了時にこのclusterだけを停止してtemporary directoryを除去した。

- synthetic DBを実際に`pg_dump -Fc`し、v1、v2 full、v2 splitを`pg_restore --list`で検証した。
- v2 splitをscratchへ復元し、env、secrets、runtime JSON、assets/images、archive内の唯一の旧snapshotを含めた全file byteとprevious参照を比較した。`data/gacha_probability_archive.legacy.json`のような名前にarchiveを含むlive fileも保持した。
- 復元したdumpを別のdisposable DBへ実際に`pg_restore`し、行・bytea値、migration集合、sequenceの次値を比較した。
- checksum破損、READYだけ、欠落archive、unique fileのinventory除外、symlink/hardlink/path escape、不完全tar、禁止scope、source変化を拒否し、validatorがinputを変更しないことを確認した。

ローカルでoptionを省略するとDB接続を行わずDB rehearsalをskipする。実DB fixtureのopt-inは `python scripts/check_ai_task_backup_restore.py --postgres-bin <local PostgreSQL bin directory>`。これはローカルtoolの指定だけであり、production接続先を受け取るoptionではない。[CI](../../.github/workflows/checks.yml)はPostgreSQL binを明示指定してこの実DB rehearsalを必須実行し、synthetic validatorだけの成功で通過させない。実行証跡はPCの `storage-p1c1-validation-20260928/backup-restore-tests.txt` と `postgres-rehearsal-environment.json` に保存した。

これはsynthetic fixtureの復旧実証である。production dump、production env、実Docker image、BDSの過去baselineを使ったrestore成功は未確認であり、独立archiveのdurabilityやproduction rolloutの許可とはしない。release metadataの参照/hash検査は、実imageの存在、実Compose/envによる正常起動を代替しない。

fixtureが成功してもproduction scope縮小の許可にはしない。既存production dumpを使う隔離restore、過去pack原本の復元手順、継続producerの扱いが未証明なので、P1c-1にはproduction `--exclude=data/backups` を導入しない。archive参照だけで全履歴を復元できるという未証明の成功statusも返さない。

## recovery archive領域の契約（production producer切替待ち）

trusted production rootは **`/home/ubuntu/ichiyon-recovery-archives`** に固定する。task本文、manifest、CLIの任意pathからproduction移行先を選べる設計にしない。scope内の `shared/data` より外へ置き、archive領域そのものを通常persistence tarへ再収録しない。固定scopeとarchive IDだけの参照構造、symlink拒否により、rootを任意に指して再収録する経路を設けない。`data/backups`内部の過去ZIP/tar等のregular payloadは必要な履歴としてそのまま保存し、拡張子を理由に除外しない。

契約上のarchive layoutは `<trusted root>/<SHA256 archive_id>/recovery.tar`、`checksums.sha256`、`READY`。`archive_id`はrecovery.tar全体のSHA256そのものである。`recovery.tar`が `data/backups` の内容だけを持つこと、全payloadのhash・scope、READYを併せて確認し、checksum検証前からtar走査完了までのroot/member identityの変化を拒否する。

[`ai_task_recovery_plan.py`](../../scripts/ai_task_recovery_plan.py) の引数なしCLIは固定sourceと固定root、および未充足の移行条件を示す。sourceはmetadata/hashだけをread-onlyで走査し、symlink・特殊file・mount越境・採取中変化を拒否する。出力はinventory digest、件数・payload bytes・subtree名で、内容を出さない。`production_scope_change_allowed=false`、`migration_allowed=false`、`actions=[]`、`classification=NEEDS_REVIEW`を維持する。apply/move/copy/delete modeや任意source/destination path optionを持たない。

以下はproduction移行に必要な手順。FULL/split backup validator、producer migration planとP1c-2Bの個別保管/restore attestorは実装済みだが、productionのarchive原本発行、delta確認とproducer切替は今回実施していない。`migration_plan()`はactions空のplan-onlyで、前提未成立のscope縮小を起動しない。

1. 保存単位はfixed root直下のcontent digest ID。sourceには既知の `shared/data/backups` だけを対応させ、任意のtask pathをcopy対象にしない。root/componentのsymlink、mount越境、置換を拒否し、承認した所有権とmodeを固定する。
2. archive manifestはsource相対path、type、size、mode、SHA256、作成元scriptのrevision/hash、operation UUID、original/correctedの関係、復元先の相対path、必要なDB snapshot、生成器version、完了状態を持つ。任意の実行commandや秘密値をmanifestへ入れない。
3. 全payload・manifestのchecksum、tarの完全読了、copy前後のsource identityを検証してからREADYをpublishする。READYはcommit markerであり検証省略用ではない。途中状態は公開せず、incomplete/terminal証跡として保持する。
4. バックアップ側にはarchive IDとmanifest digestの参照を記録する。各参照先の存在・完全性をそのbackup採取時に確認する。archiveの保持条件は、それを参照する全backup・operation・手動復旧pinが不要と証明されるまで。参照counterだけを真実とせずfresh graphを走査する。
5. new JSON snapshot等のproducerがarchive rootへ安全に書く設計、または毎backupのdeltaをarchiveへ確定する設計を先に実装する。捕捉されない新fileが1件でもあればscope縮小を拒否する。過去8 directoryだけが対象なら、directory全体のwildcard除外を行わない。
6. sourceとarchive、隔離restore先の全member集合・metadata・hashを比較し、DB/env/secrets/assets/previous imageとの復旧を検証する。creative-groupsの誤った初回ZIPを現行推奨candidateと誤表示しない。BDSの実applyは別承認とする。
7. off-hostの保管と復元可能性を別に検証し、履歴が同一filesystem障害で消える問題を残さない。同じroot disk内で移すだけではdurabilityは増えない。
8. copy/archive作成・rehearsalが重複して存在するpeak容量とinodeをP0相当で検査する。移行直後は容量が増える。copy検証と復旧試験後も、source削除やfull backupの回収は別の承認・fresh plan・lock下の操作にする。

この順序なら将来の通常backupはlive persistenceとimmutable archive参照を収録し、古いarchive payloadを世代ごとに再度取り込む必要がなくなる。現段階ではその前提が未成立なので、再帰収録の**原因と解消条件**を確定し、productionの再帰収録解消を完了したとは報告しない。

## legacy / interrupted stagingとの関係

P1b後の保存planにはlegacy/関連backup 9件、release 49件、image 9件が`NEEDS_REVIEW`、中断staging 7件（約0.642 GiB）と補助fileの`ACTIVE`が残った。これらは当時の分類で、P1c-1のfresh production候補ではない。

archiveやcleanupの判定にはdeployment lock、operation UUID、boot ID/PID/start time、owner/lease、open fd/cwd/mmap、READY/checksum、target/previous/backup/container/migration/image pin、creation/completion証跡、障害資料の保持期間を使う。mtimeの古さだけで`SAFE_TO_CLEAN`にしない。保持参照がある対象、activeまたは読取不能なowner、未検証legacyは回収不可とする。

特に `.backup-e07c53d42f7d64239209b85a35374f0e3a0ab77c.Nb1T9zPB` は2026-09-28 ENOSPCの直接証拠。中断tarが読めないことは「不要」の証明にならない。所有者・terminal receipt・必要証拠の別保管が未確認の既存stagingは引き続き`NEEDS_REVIEW`であり、後から完了扱いのreceiptを推測で作らない。

`SAFE_TO_CLEAN`は必要条件を満たす計画上の分類で、単独では削除許可にならない。P1c-2B executorは明示maintenance window、同じdeploy lock、targetごとのfresh identity/reference/disposition再検証を実装済み。planner自身はunlink、rename、rmdir、Docker prune、DB更新を行わない。CLIの既定はdry-runで、review済みdigestとwindowを指定したapplyだけがexact targetを変更する。

実装は [`ai_task_storage_cleanup.py`](../../scripts/ai_task_storage_cleanup.py) のpure functionで、P1aの保持分類にadditiveな`cleanup`判定を返す。固定evidence rootは `/home/ubuntu/ichiyon-storage-evidence`、operation receiptは`operations/<32hex>.json`を想定する。receiptの自己申告だけを信じる設計ではなく、sourceの由来・checksum、snapshot/object digestへの束縛、process/lease/reference観測をcollectorが独立検証した結果を要求する。P1c-1にはこのreceiptのproduction writer/readerや任意JSONを受け付けるCLIを設けていないため、既存production世代を新たにSAFEへ格上げしない。

terminal receipt確認後の最低保存期間はstaging 7日、backup/release/image 30日。これは削除日程ではなく必要条件の一つで、参照、unique recovery data、未完了incident review、所有証拠の欠落があれば期間経過後も`NEEDS_REVIEW`、processやleaseが生きていれば`ACTIVE`になる。P1aで`KEEP_REQUIRED`/`KEEP_POLICY`ならcleanupは不可。rollback pinをcandidate側のreleaseと一緒に暗黙に解除しない。

保存済みP1b post snapshotをoffline再解析した結果は **SAFE_TO_CLEAN 0件**。これはfresh production inventoryではなく、当時の不足証拠を新規則でも保守的に扱う確認である。候補の存在を仮定してproductionで追加cleanupを試さない。

## P1c-2A〜P1c-2Bの実装とproduction引継ぎ

- productionの独立archive作成、全payload検証、隔離restore、producer/delta対応、archive参照付きscopeへの切替とrollback手順。確認完了までfull scopeを保持する。
- 運用が発行するoperation ownership/terminal receiptと、legacyの個別由来・復旧pin整理。過去receiptをmtimeから捏造しない。
- P1c-2Bのexact cleanup executor、maintenance window、fresh再検証、partial failure audit、receipt/archive保持graphはコード/fixture/CIに実装済み。本番では独立原本、隔離restoreとoperator dispositionを発行し、fresh planで明示実行する。
- image共有layerとcache参照を考慮したretention。Docker logical sizeをそのまま回収量としない。
- 容量/inode/peak予算の監視、journal/Docker/Runnerログrotation、必要時の通知。通知やtimerによる自動cleanupはこのPRに含めない。

P1c-1当時の「executor未導入」「mergeしない」はhistoricalな作業境界で、現在の未実装判定には使わない。P1c-2Bでもprune、cleanup timer、容量閾値による自動cleanup、production filesystem migrationは起動しない。コード/CIとproduction原本・復元・実行の証明を分け、現在の残件は[CURRENT_BACKLOG](../CURRENT_BACKLOG.md)を参照する。
