# P1c-2A.1 deployment evidence index

P1c-2Aのsame-SHA reconciliationは、実体を残さず成功した場合にも`active/`と`operations/`へv1文書を残していた。両directoryを一括で読むreaderは8,192 entriesを超えるとfail closedする。P1c-2A.1は上限を増やさず、頻繁な成功reconcileの記録を固定サイズのrecent領域と累積rollupへ移す。backup/release/image/stagingのcleanup executorは追加しない。

実装は[`ai_task_storage_evidence.py`](../../scripts/ai_task_storage_evidence.py)、pureな[`ai_task_storage_evidence_classification.py`](../../scripts/ai_task_storage_evidence_classification.py)、固定rootの[`ai_task_storage_evidence_store.py`](../../scripts/ai_task_storage_evidence_store.py)。容量admissionは引き続き[P0 guard](storage-capacity.md)、cleanup可否は[retention planner](storage-retention.md)の独立した判定である。

## 実測したscheduler cadence

2026-09-28 10:26:22 UTC（19:26:22 JST）、production SHA `f2478bf184769961c9119c0ab2d58b61f13df75a`に対するread-only調査で確認した。

| 項目 | 実測 |
| --- | --- |
| timer | `ichiyon-ai-runner.timer`、active/enabled |
| service | `ichiyon-ai-runner.service`、`Type=oneshot`、`User=ubuntu` |
| timer設定 | `OnBootSec=15s`、`OnUnitInactiveSec=15s`、`AccuracySec=1s` |
| ExecStartPre executable | `/usr/local/sbin/ichiyon-ai-runner-sync` |
| ExecStart executable | `/home/ubuntu/.local/share/uv/python/cpython-3.12-linux-x86_64-gnu/bin/python3.12` |
| 既存terminal v1 | 21件、うちsame-SHA success 20件 |
| 既存active文書 | 22件（terminal companionと進行中operation） |
| 既存pending | 0件 |

15秒はserviceがinactiveになってからの待ち時間であり、実行中のdeploymentを15秒ごとに重ねる設定ではない。直近12件のsame-SHA成功receiptは09:55:41.847994〜10:23:38.834837 UTCに開始し、11個の開始間隔の平均は**152.453349秒**、中央値152.103133秒、範囲143.604878〜160.293557秒だった。operation所要時間の平均は119.766524秒、完了から次の開始までの平均は32.428370秒だった。

この窓の実測増加速度は**23.613781 operation/時**。その速度が続く場合、空から8,192件は**346.916066時間＝14.454836日**、観測21件からは**346.026755時間**である。全same-SHA 20件を使うと初期の長い間隔を含んで370.339126時間となる。いずれも観測した窓からの外挿であり、将来のcadence保証ではない。過去の15〜16秒という仮定を現在の増加速度として扱わない。

既存21件のv1はchecksumが有効で、第一観測の20件は第二観測でもbyte単位で不変だった。実装・CI中にも旧writerは自然に追加するため、rollout直前に再度inventoryを採取する。

## 固定namespaceと互換性

```text
/home/ubuntu/ichiyon-storage-evidence/
  active/*.json                 # 既存v1 companion/incomplete、変更しない
  operations/*.json             # 既存v1 terminal、変更しない
  .pending/*                    # 既存v1 incomplete、変更しない
  v2/index.sqlite3              # 新writerの固定index・lossless archive・rollup
```

root/v2 directoryはubuntu owner、0700、DBは0600。親symlink、DB/SQLite sidecarのsymlink・hardlink・owner/mode不一致・別mountを拒否する。path、retention値、SQLite filenameはtask本文、environment、CLIから指定できない。PostgreSQLやapp DBはこのindexに使わない。

実測したdeployment helperはPython 3.8.10 / SQLite 3.31.1、RunnerはPython 3.12.14 / SQLite 3.53.1。helper側の古いstdlib SQLiteでも動くSQLを用い、`RETURNING`や`STRICT` tableへ依存しない。

v1 envelope `ichiyon-deployment-evidence` version 1と、そのactive→terminal checksum連鎖は引き続き読む。新しいoperationはv2へ記録し、既存v1のrewrite、rename、import後の削除はしない。旧readerはv2を解決できないため、旧collectorへ戻すことは新規objectのcleanup evidenceを失うdowngradeである。cleanup実行を許可する互換性と解釈しない。

## A/B/C分類

| 分類 | 保存対象 | raw保持 |
| --- | --- | --- |
| A | release/backup/image、migration、残留stagingなどのpersistent objectを持つoperation | active payloadとterminal payloadをlosslessに保持。自動削除なし |
| B | failed/cancelled、rollback、異常終了、health等のincident、存在確認・publicationが不完全なoperation | losslessに保持。success rollupへ混ぜない |
| C | 同じSHAのreconciliation・health・cleanupが成功し、persistent object不在を実測で証明できたoperation | 全SHA合計で直近64件のrawと、SHAごとの累積success rollup |

同じSHAだけではCにならない。初期と終了時のcurrent/release identity、Docker image ID・tag binding、release/backupの名前集合を比較し、記録した各stagingの不存在をno-followで観測する。inventory/inspect失敗を不存在として扱わない。phase順序、最終health、cleanup完了が証明できない場合もCへ送らない。終了時に新しく作られたtagだけでもobject変化として扱う。

active operationは最大64件でadmissionを止める。終了したownerの残留activeを年齢・PIDだけで消す機能はない。catch不能な中断やpublication失敗は調査対象として残す。この上限は不明なoperationを自動処分する代替ではない。

## Index、rollup、atomic compaction

SQLiteのmeta、active、receipts、object_index、rollups tableがdurable構造を持つ。operation IDには単調generationとrandom部分を使い、transaction内で重複・再finishを拒否する。payload、terminal envelope、object-index edge、rollup、counter metadataをそれぞれcanonical JSONのSHA-256で検証する。checksumは破損検知であり、同じprivileged userによる改竄への署名ではない。

Cが完了するたびにrollupのcountを1増やし、次のchain rootを保存する。

```text
SHA256(canonical_json(previous_root, receipt_sha256, operation_id, ordinal))
```

rollupはschema/version、target/previous SHA、最初と最後の完了時刻、count、succeeded、health_verified、root digestを保持する。failureはこのchainへ入れない。64件を超えたCの最古rawだけを、参照indexがなくC proofが有効であることを再確認して退役させる。rollup更新、raw退役、activeからterminalへの遷移、counter更新は**同じtransaction**でcommitする。compactionはdeploymentが持つ既存FD9 flockの下で行い、独立したcleanup timerを設けない。

`journal_mode=DELETE`、`synchronous=FULL`を使用する。crash後のwriterはSQLiteのjournal recoveryによりcommit前後いずれかの整合した状態へ戻す。未完了activeを成功へ推定しない。既存v2 namespaceからDBだけが欠落した場合は新DBとして作り直さずfail closedする。

readerは`mode=ro`、`query_only=ON`、明示的read transactionでsnapshotを固定する。hot journalの復旧のために書込み権限へfallbackしない。破損したchecksum/index/counter/schemaや読取り不能を検出した場合は証拠不明として扱い、cleanupを許可しない。DB全体を毎回JSON配列へ展開せず、recent/activeは固定上限、A/Bとrollupは128件以下のpage、current objectの逆引きはobject_indexを使う。

A/Bのlossless rawと関連indexは削除しない。C履歴が20,000回増えてもrecent rawは64件、同一SHAのrollupは1件となる。異なるreleaseのA/Bやrollup総数は保存義務に伴って増えるため、evidence全体の物理bytesが永遠に一定になるという意味ではない。これらのarchive・保持設計はP1c-2B以降で扱う。

## Cleanup plannerとの境界

現在のrelease/backup/image/stagingからindexを逆引きし、lossless receiptのactive/terminal連鎖、owner/boot/PID/start ticks、object identityを再検証する。indexにキーがあるだけではownershipではない。v1とv2で矛盾した所有者がある場合もfail closedする。

collectorがcleanup provenanceとして渡せるv2 receiptはA/Bだけである。C raw、rollupのcount、chain root、monitorの数値はcleanup authorityにしない。receiptの保存先が変わっても、preservation期間、current/rollback/retained reference、不在確認、唯一の復旧資料でない証明、incident review完了などの条件は短縮・省略しない。

## Storage monitor

`totals.evidence.allocated_bytes`は引き続き`du -sx -B1`。既存`operation_entries`は**v1 terminal entry件数**の意味を維持し、`legacy_terminal_entries`、`legacy_active_entries`、`legacy_pending_entries`を併記する。v1は凍結されたnamespaceとして8,192件以内で数え、上限超過や走査中変更を0へ変換しない。

v2はread-only Store summaryから次を取得する。

- `index_status=verified`、schema version、DB bytes。
- counters: generation、active、A、B、C_total、recent、rollups、object_bindings。
- active/recent/pageの固定limits。

初回v2作成前は`index_status=not_initialized`。新規v2-only環境でlegacyの3directoryがすべて存在しない場合は`legacy_status=not_present`、legacy件数0とする。一部だけ欠落したlegacy namespaceは計測不能として扱う。既存namespace内のDB欠落、破損、busy、hot journal等は`index_status=unavailable`と`supplementary_errors=evidence_index`で表し、未検証counterを掲載しない。legacy件数が採取できればそれは残す。monitorはindexを作成・修復・compactせず、容量statusはP0の判定を維持する。supplementary errorは容量不足の実測を意味しない。

## Offline検証とrollout

```text
python scripts/check_ai_task_storage_evidence_store.py
python scripts/check_ai_task_storage_evidence_classification.py
python scripts/check_ai_task_storage_evidence.py
python scripts/check_ai_task_storage_evidence_v2.py
python scripts/check_ai_task_storage_cleanup.py
python scripts/check_ai_task_storage_retention.py
python scripts/check_ai_task_storage_monitor.py
python scripts/check_ai_task_deploy.py
```

20,000回のC state transition、A/B保持、active、duplicate ID、corrupt receipt/index、compaction途中の例外・process crash、readonly snapshot、現在objectからの逆引きをfixtureで確認する。stressではfsync時間とreaderの増加特性を混同しないよう、fixtureだけsavepoint batchを使う。productionはoperationごとのFULL commitであり、実process crashは別testで検証する。

ローカル実測では、完全なfrontend payloadを使う20,000件のfixture生成は27.607秒。collector＋plannerは64件時0.112秒／peak 2,424,282 bytes、20,000件時0.111秒／peak 2,427,812 bytesだった。どちらもC raw 64件・rollup 1件で、A、rollback失敗B、active各1件とobject bindingを維持し、cleanup分類とread-only filesystem fingerprintは不変だった。これはfixtureと計測環境での結果であり、productionの処理時間保証ではない。

CI後、actual PR headに対するfresh P0を確認してmergeし、既存Runnerのidle catch-upに配備させる。current SHAを入力した軽いreconcile予算を新規deploymentの証明にしない。rollout前後にv1 inventoryのcount・hash・metadataを比較し、既存receiptが変わっていないことを確認する。複数回のidle catch-up後にC_total/rollupが増え、legacy rawが増えず、recent上限が守られること、evidence reader/planner/monitorが正常なことを確認する。app health、production exact SHA、migration exact set、DB/VPN identityも通常どおり確認する。

backup/release/image/stagingの自動削除、Docker prune、legacy objectの強制cleanup、preservation期間短縮はこの実装に含めない。通常deployが自分の一時領域を片付けること、および検証済みCの内部raw rowをrollupへcompactすることは、production objectのcleanupとは区別する。

P1c-2Bへはobject/incident archiveの長期保持・独立バックアップ、残留activeの証拠を保持した明示的回復手順、maintenance window内のfresh参照確認、exact path cleanup executorと部分失敗監査を残す。
