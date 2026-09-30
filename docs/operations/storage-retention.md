# Production app storage retention planner

P1aはproduction app OCIのbackup、release、Docker image、deployment stagingを参照関係から分類する。削除機能、retentionの自動実行、deploy、DB変更を持たない。P0の容量guardとbackup scopeは変更しない。Minecraft BDSホストのretentionは対象外。

`DELETE_CANDIDATE`は採取時点の**一組の候補計画**を表す。個々のpathを、そのまま削除してよいという許可ではない。P1bで実行する場合はfresh inventory、同じ保持policy、依存関係、active operationを再検証し、承認した対象一覧と一致することを確認する。

## 実装と実行境界

collectorは`ai_task_storage_retention.py`、参照graphと保持policyは`ai_task_storage_retention_graph.py`。graph moduleの`build_plan(snapshot, policy=None)`はJSON互換の採取結果だけを受け取り、filesystem、process、Docker、networkへアクセスしない。戻り値は各対象の分類、理由、参照、容量集計を含む。

collectorは固定したproduction pathとDockerの参照用interfaceを読む。任意のshell commandを受け取らず、削除・rename・prune・build・restart・container作成を行わない。JSONの内容や`.env`、秘密値、DB dump内容、container環境変数を報告に出さない。backupのchecksumは実ファイルから検証し、tarは展開せず読む。`READY`という名前の存在だけで復元可能と判断しない。

現行のunversioned backupについて、`validation=verified`は決められたfile集合、previous参照、infra metadata形式、dump/tar全体のchecksum、dumpの`PGDMP` header、tar memberの型/pathと必要scopeを検証済みという意味。`restore_validation=metadata_checksums_tar_scope_dump_header_not_full_restore`を併記する。v2は`ai_task_backup.py`による全inventory照合、完全tar読取り、必須の`pg_restore --list`も追加する。production固定pathではDB container内の`docker exec -i ichiyon-robot-db pg_restore --list`だけを使用し、hostへのclient追加やDB接続・restoreは不要。代替fixture pathはlocal clientを使用する。`pg_restore`が利用不能ならv2は検証不成立とし、header検査へ後退しない。いずれもDB復元やprevious image起動の証明ではない。releaseもmetadata/layout/image参照を検証し、保存されたvalidatorやCompose codeを実行しない。v2と独立archiveの契約、現行形式の追加検証、隔離restore試験は[storage-recovery.md](storage-recovery.md)を参照。

各backupのdump/tarはコピーごとに全体hashを計算する。同じSHA256と確認したtarのmember/scope検証だけを、その1回の採取中に共有する。異なるコピーのhash確認を省かず、次回実行へcacheを持ち越さない。

production測定は、PCでreviewしたsourceをSSH stdinで実行する。remoteへplannerをinstallせず、`python3 -B`相当でbytecodeも書かない。plannerの配備を目的とする新releaseやDocker imageは作らない。Discord AI Task経由でheavy taskを開始しない。通常サービスの書込みやSSH監査ログは観測中も進むため、採取時刻と観測間の変化を記録する。

CLIに削除modeやpolicy書換えoptionはない。出力はstdoutのJSONのみ。以下はrepo rootのPowerShellで5 moduleをmemory上に束ねる例。`APP_OCI_READ_ONLY_ALIAS`を既存の承認済みapp OCI用SSH aliasへ置き換える。認証やhost keyを新しく設定する例ではなく、BDSのaliasを使わない。`sudo -n`はDocker layer metadataと全processの参照を読むために用い、権限不足を推定で補わない。

```powershell
$appSshAlias = 'APP_OCI_READ_ONLY_ALIAS'
$bundle = @'
from pathlib import Path
collector = Path("scripts/ai_task_storage_retention.py").read_text(encoding="utf-8")
print("import sys, types")
for name in ("ai_task_storage_cleanup", "ai_task_backup", "ai_task_storage_evidence_store", "ai_task_storage_evidence_classification", "ai_task_storage_evidence", "ai_task_storage_retention_graph"):
    source = Path("scripts", name + ".py").read_text(encoding="utf-8")
    print("m = types.ModuleType(" + repr(name) + ")")
    print("sys.modules[m.__name__] = m")
    print("exec(compile(" + repr(source) + ", '<' + m.__name__ + '>', 'exec'), m.__dict__)")
print("exec(compile(" + repr(collector) + ", '<retention-collector>', 'exec'))")
'@
$bundle | python - | ssh -o BatchMode=yes -o StrictHostKeyChecking=yes $appSshAlias 'sudo -n python3 -I -B -'
```

module sourceはPCのreview済みcheckoutから読む。remote releaseの同名moduleをimportしない。出力を保存する場合もPC側のファイルへ保存する。

**mainのidle catch-upが自動deployする環境では、このP1aをmergeするだけでもproduction deployを誘発し得る。** 「P1aのためにdeployしない」という条件がある場合はPRとCIまでで保持し、承認された通常releaseへの統合時にmergeする。容量guardによる拒否を、deployを発生させない仕組みの代用にしない。

## 分類と参照graph

| 分類 | 意味 |
| --- | --- |
| `KEEP_REQUIRED` | current、runtime、rollback、restore、明示的pinから必要 |
| `KEEP_POLICY` | 必須参照に加え、安全余裕として保持する世代 |
| `DELETE_CANDIDATE` | 完全に読めた参照graph上で保持対象から参照されず、所有・形式・容量を検証できた対象 |
| `NEEDS_REVIEW` | legacy、不完全、破損、不明な参照、所有不明、復元契約未確認 |
| `ACTIVE` | active、または処理中の可能性を排除できない対象 |

不正・legacy metadataを、pinがあるから正常なbackupに格上げしない。`NEEDS_REVIEW`のまま既知の参照先を保護する。不明なmetadataがrelease/image参照を隠し得る場合、そのcategoryの削除候補判定を抑止する。採取失敗は空のinventoryとして扱わない。

参照の起点と向きは次の通り。

- `ichiyon-current`と稼働appのSHA → release → `immutable-image.txt`のimage。
- current backupの`previous`と既存rollback証拠 → 直前release → exact image。単にmtimeが2番目のreleaseを「既知正常」としない。
- 保持backup → 対象release、`previous`、復元に必要なrelease/image metadata。
- 各releaseの`rollback-images.txt` → literal image ID。既存protocolの明示pinとして、所有releaseが候補でもpinを勝手に解除しない。
- 全running/stopped container → image。migration/deployment containerのrelease情報も保護対象。
- rollback tag、legacy restore metadata、その他の明示pin → 参照先。
- staging → 対象SHAや読めた復元参照。active/所有不明stagingを年齢だけで削除候補にしない。

Docker imageの参照はtagの表示文字列だけで判断せず、取得時点のimage IDへ解決する。同じIDへ複数tagやreleaseが向いていても一度だけ計上する。解決不能なpinは不要な参照ではなく検証不成立。

その他の明示pinは、存在する`/home/ubuntu/ichiyon-retention-pins.json`の`releases`/`backups`/`images`配列から読む。plannerはこのfileを作らない。image pinはexact image ID、release/backupはinventory IDに解決できる必要があり、不正または解決不能なら候補判定を抑止する。Docker tagは`rollback`を含むものと`:pin-`を既存pinとして保護する。

## 提案する追加保持policy

必須graphを先に保護した上で、既定の提案は次の和集合とする。

1. 完全なbackupの最新3世代。
2. 採取日を含む直近7 UTC暦日それぞれの最新の完全backup。該当日のbackupがなければ捏造しない。
3. 完全なreleaseの最新3世代。
4. 直前の既知正常release/imageと、そのrollbackに必要なbackup。

これは「最新3件より古ければ削除する」という規則ではない。current、retained backupのprevious、container、migration、rollback pin、legacy復元要件は件数・日数より優先する。追加保持数を変更しても必須参照を外せない。legacyとincompleteは別の確認が終わるまで保持する。週次・月次やoff-host復旧点の長期保持は別policyで追加し、単一hostの短期retentionを災害対策の代わりにしない。

出力の`created_at`は名前に反して厳密な作成証明ではない。releaseはdirectory mtime、完全backupはREADY mtimeを並び順の観測値として使い、`timestamp_source`にそれぞれ`directory_mtime_not_creation_proof`、`ready_mtime_not_creation_proof`と記録する。`ctime`もinode変更時刻であり作成日時ではない。imageの`created_at`はDockerのCreated値。採取開始/終了は`captured_at`/`captured_end_at`のUnix秒。保持policyの日次境界だけはUTC暦日で固定する。

個々のbackup内の同じtarを別々に消す方法は採らない。各世代の`READY`/checksum契約を保ったままbackup単位の候補を決める。将来content-addressed storageで重複排除するならmanifest version、参照count、crash recovery、restoreを別に設計する。

## 容量表示の読み方

filesystem容量は論理file sizeと割当bytesを区別する。候補集合のdev/inodeを重複排除し、集合外hardlinkやopen file、計測不完全を保守的に扱う。親directoryと子directoryを別々に加算しない。backup/releaseの確定候補容量とDocker見積りを別列に出す。

全ての走査済みentryのdev、inode、mode、size、mtime、Linux ctimeを採取終了時に再確認する。generation内容の変化、currentの変化、release/backup世代一覧やDocker image/container参照の変化はinventory不完全として候補を抑止する。stagingの変動は別の観測errorとして記録する。processは採取前後の`/proc`でcwd、fd、mmapを読む。fdを閉じてもmmapに残るfileも参照中として扱い、permissionなどでprocess参照を完全に読めなければ`process_reference_inventory_unavailable`として全候補判定を停止する。

`recovery.filesystem_candidate_allocated_bytes`は候補nodeの割当合計、`filesystem_confirmed_bytes`と`filesystem_by_category`は集合外hardlink/open参照を除きinodeを重複排除した採取時点の値。後から開かれるfileや書込みに対する予約・保証ではない。`filesystem_estimate_status`と`filesystem_caveat`も一緒に読む。

Dockerでは候補imageのfull size合計と解放量は異なる。image IDを重複排除し、layerの親を含めたChainIDで共有を区別する。保持imageから参照されるlayerを解放見積りから除く。layer sizeが不足・矛盾する場合は不明とする。`recovery.docker.unique_layer_upper_bound_bytes`はoverlay2の非圧縮layer metadataに基づくlogicalな上限推定であり、filesystem実blockの上限を証明するものではない。imageからの参照がなくなってもbuild cache等がlayerを保持し得るため、`guaranteed_reclaimable_bytes`は0として扱う。`docker system df`のreclaimableを安全な削除容量へコピーしない。

## interrupted stagingの扱い

P1c-1では既存P1a分類を保ったまま、`cleanup`に`SAFE_TO_CLEAN` / `NEEDS_REVIEW` / `ACTIVE`の追加判定を出す。`ai_task_storage_cleanup.py`自体は純粋な判定関数である。P1c-2A/P1c-2A.1は正式deployのreceipt writerと固定領域のread-only attestor/indexを追加し、`durable_operations`とnodeの`operation_id` / `ownership_verified` / `owner_state`へ検証結果を出す。P1c-2Bは別entry pointの[maintenance executor](storage-maintenance.md)を実装し、operator disposition、独立保管/restore、lock下のfresh eligibility、exact tree/windowとpartial failureを検証する。古いproduction objectへ必要証拠を推測で付与せず、古さやlockの空きだけで`SAFE_TO_CLEAN`にしない。`SAFE_TO_CLEAN`は採取時点の提案であり、明示applyと実行直前の再検証が必要である。

現protocolは`.prepare-<sha>.*`、`.release-<sha>.*`、`.backup-<sha>.*`、helper/infra/pointerを作る。random suffixやmtimeだけではoperation ID、owner、lease、終了を証明できない。deploy lockが今空いていても、そのpathが別processから再使用されない証明にはならない。

P1aはstagingを`ACTIVE`または`NEEDS_REVIEW`にする。`ready`と`checksum_manifest_present`は存在の観測であり、staging tar/checksumの完全検証結果ではない。`operation_sha`、`ownership`、`lock_held_during_observation`、読めたcurrent/release/backup参照を添える。既存stagingにはdurable operation owner/terminal receiptがないため`ownership=unknown`、fd/cwd/mmap、protocol processのSHA、lockから稼働可能性が見える場合は`possible_active`とする。`.backup-*`内の`READY`があってもpublish前かもしれず、未完了であっても復旧に唯一必要な資料かもしれない。2026-09-28の中断backup等の障害証拠は、別の調査記録と照合して保持する。

P1c-2Bの明示maintenanceは、少なくとも次をすべて要求する。timerによる自動回収は実装しない。

1. 作成時に永続化したoperation UUID、host boot ID、PID/start time、owner/lease、対象SHA、作成したpathの一覧が一致する。
2. terminal状態が記録され、owner processとleaseが終了している。lock取得下で再検証し、active operationと競合しない。
3. current、保持release、backup、container/migration、explicit pinからの参照がない。
4. symlink、mount越境、path置換を拒否し、検証したinodeと実行直前の対象が一致する。
5. 障害資料としての保存期間と調査完了を確認し、必要な証跡を別のdurable領域に保存・検証済み。
6. backupなら完了/中断、checksum、restore要否を確認し、`READY`だけやmtimeだけで判断しない。

既存の未記録stagingへ、後付けで「古いからowner不在」と推定する規則は適用しない。active/stale/owner不明は保持する。minimum preservation、固定evidenceとbackup契約は[storage recovery](storage-recovery.md)、新しいdisposition/plan/window/監査の実行手順は[storage maintenance](storage-maintenance.md)を参照する。

## `shared/data/backups`の再帰収録調査

2026-09-28、production appの現在SHA `60099292444e1b6719765b096b68cbb71fa05f89`で、内容を公開せずpath・size・hash・許可したmetadataだけを確認した。対象は8個のpack作業directoryと96個のlegacy JSON snapshot。pack作業物の実割当は324,411,392 bytes、JSONは589,824 bytes、root directoryを含む全体は325,005,312 bytes。tar内のregular file payloadは286,769,111 bytes。

| pack作業directory | 割当bytes | 作成元・読込み元と復元上の扱い |
| --- | ---: | --- |
| `molcar-accessories-v1-20260923` | 21,925,888 | 一回限りの`manage_release.py`がLIVE baseline、原素材bundle、登録前DB snapshot、登録結果、生成ZIPを保存。`register`/`compare`/`apply`が読み込む。登録前DBと原素材を現在のGitから再生成できるとはしない |
| `molcar-creative-groups-20260923` | 47,136,768 | 一回限りの`manage_groups.py`がLIVE baseline、source、prepared/repaired ZIPとproofを保存。`apply`/`repair`が読む。初回ZIPには既知の不正なgroup指定があり、修正版との対応が復旧資料として必要 |
| `mokuro-release-20260923` | 42,049,536 | 一回限りの`manage_mokuro.py`がLIVE baselineへ選択したGit変更を重ねて生成。`apply`は保存ZIPとhash/catalog proofを読む |
| `mokuro-idle-walk-20260923` | 42,225,664 | 一回限りの`release.py`がLIVE baselineと4つの変更fileから生成。保存ZIP/proofをapply時に読む |
| `mokuro-animation-ids-20260923` | 42,237,952 | 一回限りの`release.py`がLIVE baselineと2つのRP変更fileから生成。保存ZIP/proofをapply時に読む |
| `garbage-molcar-kuma-paw-20260923` | 42,979,328 | 一回限りの`release.py`がLIVE baselineと限定した変更から生成。baseline/new hashと生成ZIPを検証しapply |
| `garbage-pickup-mokuro-jump-20260923` | 43,130,880 | 一回限りの`release.py`がLIVE baselineと7つの変更fileから生成。保存ZIP/proofをapply時に読む |
| `garbage-follow-recovery-20260923` | 42,725,376 | 上記scriptの後続修正が新しいLIVE baselineと1つの変更fileから生成。保存ZIP/proofをapply時に読む |

これらはdirectory名から推測したものではない。既存PCに残る一回限りのscript、2026-09-22 UTCの作成記録、production上のfile構成・proofの対応を確認した。定期的にこの8 directoryを作るcodeは現在repoにも全Git履歴の該当文字列検索にもなかった。過去scriptは今回実行していない。

96個のJSONは、`admin/main.py`の`backup_json_file`と`bot/data_store.py`の同名関数が現在のJSONを書き換える直前に保存する形式。adminのquotes/reactions/ng_words/kuji更新、およびbot側の該当JSON管理が呼出元。通常読込みは`data/*.json`で、backupを自動復元する読込みは見つからなかった。ただし更新前の内容を現在のJSON/DBから再生成できるとは限らず、手動復元資料として保持要否を決める。初期形式変換snapshotも含むため、全96件の作成者をtimestamp形式だけで同一関数へ断定しない。

### runtimeとrollback

3 app containerの`/app/data`はshared dataへbind mountされるが、mountされた全fileが通常runtimeで読まれることを意味しない。現在のmanaged pack生成は次の経路を使う。

- `admin/minecraft_cosmetics.py:records`はGit由来のbuiltinsとPostgreSQLのasset blobs/tombstonesを取得。
- `bot/repositories/minecraft_cosmetics.py`がtexture/geometry/icon等をDBから読み、`bot/services/minecraft_cosmetics_pack.py:pack_zip`がimmutable releaseの`minecraft` sourceと結合。
- `admin/minecraft_release.py`はこの経路でpackを生成し、Control APIへ渡す。dated `data/backups`は参照しない。
- BDS側の復元codeは`minecraft_cosmetics_apply.py`の`cosmetics-applications/<operation>/original`を使用する。app hostの8 directoryとは別契約。今回はBDSへ接続しておらず、そのbackupの現存・完全性を証明していない。
- appのdeployment失敗rollbackはprevious Compose/imageでappを再作成し、通常はshared tarを展開しない。ただし災害復旧用の`persistence.tar`は現在`data/backups`を包括収録するため、restore契約を変更せず一部だけ取り除くことはしない。

現在の正常packをGit + 現DBから生成できることと、過去のLIVE baselineや登録前DBをbyte-identicalに再現できることは別。revision、当時のDB内容、手作業変更、生成器versionが必要であり、8 directory全体を「再生成可能なcache」としない。生成を試すためにexport APIや`revision()`を呼ぶとDB sequenceを更新するので、今回実行していない。

### 既存の別copyと提案

current世代の`ichiyon-backups/60099292444e1b6719765b096b68cbb71fa05f89/persistence.tar`は354,109,440 bytes、SHA256 `90ce86516b5227211bf4653d85b03cbff87b8444658bb1581cdc785869355203`。`READY`が存在し、実tar全体のhashがmanifestに一致した。tarを展開せずmemberを読み、`data/backups`のregular file **12,762件すべて**についてlive fileとのsize/SHA256一致を確認。欠落・不一致は0。これは別backup世代への保存を証明するが、同じhost/filesystem上のcopyでありoff-host災害耐性やrestore試験成功の証明ではない。

結論は **B: 別のdurable領域へ移すべき**。即時の無条件除外Aは採らない。継続して変化するlive dataと、履歴として保持するpack operation原本・旧JSONを、manifest付きの独立した復旧資料へ分ける設計を提案する。現在の包括backupを保持したまま、移行先の全file hash、operation UUID、original/correctedの区別、DB snapshot、復元手順を検証し、独立したoffline restore試験を通してからbackup scope versionを更新する。移行先が同一root上なら最初は容量が増えるため、そのピークもP0 guard相当で評価する。

特に`molcar-creative-groups`の初回prepared ZIPと修正版は同等の復旧候補ではない。古い手順に従った再適用で不正なgroup指定を戻さないよう、証跡と修正版を一緒に保持する。生成ZIPやsource copyだけを将来省く案は、当時の入力と生成器からhash一致を再現できた部分に限定する。P1aではscope変更、移動、除外、削除を行わない。

## 検証とP1bへの引継ぎ

`python scripts/check_ai_task_storage_retention.py`でfixtureによる分類、current/rollback/retained backup/container保護、legacy/破損、active staging、重複ID/shared layer、read-only境界を検証する。P0のstorage/deploy回帰checkも維持する。

P1bへ渡すのは採取時刻、policy、snapshot/plan hash、具体的な候補ID/path、参照理由、filesystem確定bytes、Docker上限/不明、active/不明対象の除外理由。source・出力のhashはPC側の証跡採取手順で記録する。P1bはこの一覧に基づいてfresh planとの差分をreviewし、別途承認された対象だけを扱う。

**P1bでbackupを削除する前に、残すbackupによるoffline restore確認を行う。** DB dumpの復元、env/secrets/live data/asset、previous release/imageとmigration整合、必要なpack原本・復元metadataが揃うことを、productionと隔離した環境で確認する。checksum/header検証だけでこの前提を満たした扱いにしない。restore確認結果と保持backupのSHA256を承認対象に含める。shared/data/backupsと所有不明stagingは、今回の再帰調査だけを根拠に削除対象へ追加しない。
