# Production storage maintenance — P1c-2B

P1c-2Bのコードはfresh eligibility、receipt/archive保持graph、明示maintenance window、exact-path cleanup、partial failure audit、stale/active operationの保持、fixtureとCIまで実装済み。**今回の作業ではproductionのbackup/release/image/stagingを一切削除していない。** 本番での保管原本・隔離restore・operator reviewの発行、配備と明示実行は別工程に残す。現時点の分類は[CURRENT_BACKLOG](../CURRENT_BACKLOG.md)を参照する。

実装は[`ai_task_storage_maintenance.py`](../../scripts/ai_task_storage_maintenance.py)、[`ai_task_storage_disposition.py`](../../scripts/ai_task_storage_disposition.py)、pure plannerの[`ai_task_storage_cleanup.py`](../../scripts/ai_task_storage_cleanup.py)。既存[P0 guard](storage-capacity.md)、[保持参照](storage-retention.md)、[復元契約](storage-recovery.md)、[durable index](storage-evidence-index.md)を維持する。monitor、timer、idle catch-upからcleanupを呼ばない。旧Runner change-policyやCodex編集path制限、自動content reviewを追加するものではない。

## 対象と証明

削除対象は既存collectorが識別した`releases`、`backups`、`images`、`staging`に限定する。release/backupは固定rootのexact SHA、stagingは既存protocolのexact path、imageは完全な`sha256:<64hex>`で照合する。wildcard、任意root、Docker prune、force removalを使わない。

`SAFE_TO_CLEAN`には既存の保持参照・minimum preservationに加え、次の独立した観測を必要とする。

| 証明 | 判定 |
| --- | --- |
| ownerと終了 | A/B lossless receipt、operation ID、boot/PID/start ticks、terminal連鎖を検証する。PID不在や古いmtimeだけで終了扱いにしない |
| 現在の参照 | current/previous、保持release/backup、container、migration、image、手動pin、fd/cwd/mmap、active operationを全て確認する。不完全なinventoryはcleanupを許可しない |
| 保持期間 | terminal完了後stagingは最低7日、backup/release/imageは最低30日。期間経過は十分条件ではない |
| 原本と復元 | source、独立保存payload、隔離rehearsalの全member/type/mode/size/hashが一致する。3 rootのdevice/inodeを別々に確認する |
| operator disposition | 対象object/operation/receipt digestへのbinding、唯一の復旧資料でないこと、incident review完了、復元/再現検証方式を固定領域から検証する |
| backup | READY/checksum、tar全payload、previous/rollback契約を実validatorで確認する。v2 backupには`pg_restore --list`と実際の隔離DB restoreの証明も必要 |
| image | 保存したDocker-save tarの全byte、manifest/config identity、全layerと`rootfs.diff_ids`を再検証する。operatorによるexact imageのrestore rehearsal証明も必要 |

参照元・保持対象が変われば、以前のoperator reviewが存在していてもそのplanを実行しない。sourceと保管payloadを同じinodeへaliasしたもの、symlink/hardlink、special file、mount/bind mountを証明に使用しない。

## 固定evidence namespace

```text
/home/ubuntu/ichiyon-storage-evidence/
  active/                         # legacy v1、保持
  operations/                     # legacy terminal v1、保持
  v2/index.sqlite3                # A/B lossless、C recent/rollup
  cleanup-dispositions/
    <digest([category,id])>.json   # operatorが対象ごとに発行
  cleanup-preserved/
    <64hex-archive-id>/
      verification.json           # 保管/隔離復元の記録
      payload/                    # release/backup/stagingの独立保存
      rehearsal/                  # その隔離復元
      image.tar                   # imagesの場合のexact Docker-save archive
  cleanup-audit/
    <random-32hex>.jsonl           # 実行ごとの追記監査
```

review directory、preserved root、audit rootはdeploymentの`ubuntu` owner・0700、review/verification/audit fileは0600・single linkを要求する。親componentはdescriptorでno-follow traversalし、review/proofを観測前後で読み直して差替えを拒否する。root/pathはtask本文やenv/CLIから変更できない。`cleanup-audit`は運用工程で先にprovisionし、executorが不明な親の下へ暗黙に作らない。

reviewとverificationは`{"body": ..., "sha256": ...}`のcanonical JSON envelope。checksumは破損検知であり、同じprivileged accountによる偽の承認に対する署名ではない。operatorが確認した事実だけを発行し、taskから受け取ったJSONや自己申告booleanを承認へ変換しない。

disposition body version 1は次を含む。

| field | 契約 |
| --- | --- |
| `binding` | `category`、`id`、`operation_id`、`receipt_sha256`、`object_sha256`。fresh collectorの同一object/terminal receiptと一致すること |
| `archive_id` | 固定`cleanup-preserved/<64hex>`を選ぶID |
| `content_sha256` | directoryはportable全member inventoryのdigest、imageはDocker-save tarの全byte digest |
| `verification_sha256` | 同じbinding/contentに束縛されたverification envelopeのdigest |
| `unique_data` | operatorが唯一の復旧資料ではないと確認して`false`にする |
| `incident_review_complete` | incident調査と証拠の保管を完了した場合だけ`true`にする |
| `disposition_validation` | `independent_restore_rehearsal`または対象契約が許す`reproducible_artifact_verified`。backup/imageは前者を必須とする |

verification bodyは同じbinding、`content_sha256`、`restored_content_sha256`を持つ。backupは`database_restore_verified=true`、imageは`image_restore_verified=true`を、実際のrehearsal完了後に記録する。attestorはdirectory全内容とbackup validator、image tar identity/layersをfreshに再検証する。DB/imageの実restoreは自動cleanup内で再実行せず、運用者が隔離工程の結果を発行する。

`disposition_template()`はレビュー用の未承認templateを返し、`unique_data=null`、`incident_review_complete=false`、`disposition_validation=null`のままではcleanup authorityにならない。legacy/不明operationへ後付けterminal receiptを作らず、reviewや保存原本が欠ける対象は`NEEDS_REVIEW`に保つ。

`/home/ubuntu/ichiyon-recovery-archives/<digest>`はFULL/split backupのrecovery archive契約であり、上記cleanup用の独立保存領域と区別する。off-host durabilityは別の検証事項。同じhost上のcopy/restore一致だけでoff-host復元可能と報告しない。

## Fresh planとmaintenance window

CLIの既定はread-only plan。snapshot、disposition、exact treeを読み直して、`cleanup`と`evidence_retention`を出力する。production objectは変更しない。

```bash
umask 077
python scripts/ai_task_storage_maintenance.py > "$REPORT_FILE"
python -c 'import json,sys; json.dump(json.load(open(sys.argv[1]))["cleanup"], open(sys.argv[2], "w"), sort_keys=True)' "$REPORT_FILE" "$PLAN_FILE"
```

apply入力はreport全体ではなく、抽出した`.cleanup` envelopeを使用する。`sha256`をレビューして別途渡し、UTC Unix secondsで開始/終了を指定する。windowは正の長さで最大3,600秒、現在時刻は`start <= now < end`。planは採取後300秒以内で、空planや重複target、変更されたdigestを拒否する。古いplanをtimestampだけ編集して使わない。

以下は運用者が証明と新しいplanを用意した後の**production削除command**であり、今回実行していない。

```bash
python scripts/ai_task_storage_maintenance.py \
  --apply-plan "$PLAN_FILE" \
  --expect-plan-sha256 "$REVIEWED_PLAN_SHA256" \
  --window-start "$WINDOW_START_EPOCH" \
  --window-end "$WINDOW_END_EPOCH"
```

executorは既存`/home/ubuntu/ichiyon-deploy.lock`の同じinodeへnonblocking exclusive flockを取得する。lockはubuntu owner・0600・single linkを確認し、kernelのholder観測が自processだけであることを独立確認する。競合・観測不能時は削除へ進まない。

lock保持中、**targetごと**にcollectorとdisposition attestorを再実行する。fresh classification、object digest、exact treeを承認planの同じtargetと比較し、追加候補を勝手に削除しない。windowとsnapshot age 300秒以内をtarget開始および各unlink/rmdir/image removal開始直前に再確認する。fresh収集だけでwindowを使い切れば削除しない。

review/planのobject digestは`lock_held_during_observation`だけを除外する。idle planからexecutor自身のexclusive lockへ変わる観測を同じ対象として照合するためであり、参照・active・validation・allocation等の変化は除外しない。collectorのcleanup evidenceは引き続き完全なfresh nodeとinventoryへ束縛し、own lockのkernel holderも独立検証する。

## Exact executorとpartial failure

directoryは全absolute componentを`O_NOFOLLOW`で開き、descriptorから全memberのdevice/inode/mode/size/mtime/ctimeと全file hashを採取する。実行直前のtree一致に加え、memberごとのidentityを照合し、検証済みdescriptorを基準にunlink/rmdirする。`/proc/self/mountinfo`を使って同deviceのbind mountも検出し、target自身または配下にmountがあれば拒否する。削除途中にもmount tableを再観測する。

imageは完全image IDに対する`docker image rm`だけをargv/shellなしで起動し、force/pruneは使わない。stdout/stderrは公開しない。開始前にwindow/TTLを確認し、command timeoutは60秒。開始したDocker処理の完了がwindowの終了後になる場合はあるため、windowは各変更の開始期限であり、完了時刻の保証ではない。Docker logical sizeを回収bytesと同一視しない。

全関係processが同じdeploy flockへ参加することをproduction運用の前提とする。参加しないprivileged processによるcompareとunlinkの間の強制差替えまで、filesystem APIだけで排除できるとは主張しない。maintenance中に別のroot操作で対象、mount、lock fileを置き換えない。

監査は新規0600 JSONLにhash chainで追記し、作成directoryと各recordをfsyncする。`started`、`target_started`、`member_started`、`member_removed`、target結果とterminal結果を記録する。監査には内容byteやsecretを入れず、identity/hash/relative memberだけを残す。

対象内の一部削除や再検証失敗は`failed_or_partial` / `REPLAN_REQUIRED`、後続targetは`not_attempted` / `EARLIER_FAILURE`、全体は`partial_failure`。同じproofで自動retryせず、監査と残存memberを確認し、新しい保存/復元証明・inventory/planを用意する。process crashはterminal監査の欠落と最後のmember開始/完了で判断し、監査不在を成功に変換しない。

## Receipt/archive保持graphとstale operation

`collect_receipt_archive_graph()`は既存object、全pageのA/B lossless receipt、active、legacy v1、recovery archiveのedgeを収集する。samplingされたreportだけで参照不在を証明しない。不明edge、走査不完全、active/owner不明、incident review未完了、保存期間不足、off-host restore未証明は`KEEP`を返す。

A/B raw receiptには`LOSSLESS_RECEIPT_REQUIRED`を付け、今回executorで削除しない。archiveも参照がなく最低30日・incident review・off-host復元を全て証明できた場合に`RETIREMENT_PROPOSAL`を返すだけで、自動削除しない。producerがまだ発行していないoff-host/incident proofをcollectorが推測で`true`にしない。Cのbounded recent/rollupは既存transactional compactionを維持する。

active/unknown/stale operationは年齢やPID不在だけではterminal authorityを持たない。operationの調査はreceipt/関連objectを保持して行い、maintenance executorはterminal receiptやleaseを合成・更新しない。復旧可能性が確認できない残留stagingは保存期間を過ぎても保持する。

## 検証とproduction引継ぎ

```text
python scripts/check_ai_task_storage_maintenance.py
python scripts/check_ai_task_storage_disposition.py
python scripts/check_ai_task_storage_cleanup.py
python scripts/check_ai_task_storage_retention.py
python scripts/check_ai_task_storage_evidence.py
python scripts/check_ai_task_storage_evidence_store.py
python scripts/check_ai_task_storage_evidence_classification.py
python scripts/check_ai_task_storage_evidence_v2.py
python scripts/check_ai_task_storage_monitor.py
python scripts/check_ai_task_storage.py
python scripts/check_ai_task_deploy.py
```

fixtureはfresh参照変更、期限切れ/不正window、identity/content差替え、partial failure、active/stale保持、receipt/archive edge、symlink/hardlink/special file/mount、flock競合、audit chain/fsync、独立copy/restoreとbackup/image検証を扱う。削除試験はTemporaryDirectoryだけを使用する。Linux descriptor/flock部分はWindowsではskipし、`.github/workflows/checks.yml`のLinux jobで実行する。実DB restoreは既存`check_ai_task_backup_restore.py`の隔離PostgreSQL試験も使う。

productionでは配備前に現在の稼働SHA/health、P0 peak容量/inode、独立保管原本とrehearsal、operator review、固定evidence directoryを確認する。maintenance windowとfresh planを用意してから明示applyし、監査、残存参照、app/Bot/DB healthを確認する。receipt/archive graphのKEEPや候補0件を故障と扱わず、証拠の欠落を埋める。今回のfixture/CI成功は本番削除や復旧試験済みの証明ではない。
