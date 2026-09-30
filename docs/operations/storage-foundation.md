# Production storage monitoring and P1c-2A boundaries

P1c-2Aはcleanupに必要な証拠と容量監視を追加し、mainへ統合済み。P1c-2Bでは[fresh maintenance](storage-maintenance.md)のdisposition attestor、保持graph、明示window、exact-path executorとpartial failure auditを実装済み。backup/release/image/stagingのtimerによる自動削除、Docker prune、空き容量を理由にした自動cleanupは行わない。[P0容量guard](storage-capacity.md)が引き続き新規Runner作業・deployを許可または拒否する。[cleanup planner](storage-retention.md)自身はplan/eligibilityだけで、monitorのWARNING/CRITICALは削除許可ではない。本番の保管/restore/review発行とapplyは別工程で、今回production削除は実施していない。

P1c-2A.1では、頻繁なsame-SHA成功receiptの増加を[durable indexとsuccess rollup](storage-evidence-index.md)で扱う。以下のv1説明は既存receiptの契約として残し、新operationはv2 indexへ記録する。既存v1 fileを移動・書換え・削除しない。

## 監視の起動・固定path

production source root `/home/ubuntu/ichiyon-ai-runner-src` から起動するLinux Runnerだけが [`ai_task_storage_monitor.py`](../../scripts/ai_task_storage_monitor.py) のworkerを開始する。新しいtimerやserviceは作らず、既存Runner invocationの間で監視を継続する。短いidle巡回間は次のinvocationに引き継ぎ、長いtask中も300秒周期で観測する。Windows/local checkoutではworkerを起動しない。

| path | 用途 |
| --- | --- |
| `/home/ubuntu/ichiyon-storage-monitor.lock` | 監視worker間の重複計測防止。deployment lockとは別の固定file |
| `/home/ubuntu/ichiyon-shared/data/storage-monitor.json` | 直近1回の数値report。app内では `/app/data/storage-monitor.json` |
| `/app/data/storage-monitor-notification.json` | botの通知試行/成功時刻とstatus/reasonだけを保存するcooldown state |
| `/home/ubuntu/ichiyon-runner-diagnostics/runner.log` | 既存journalとは独立した、容量上限付きの新しいRunner診断stream |

root/pathをtask本文・通知payload・CLIで指定する経路はない。reportはregular file、親directoryのsymlinkを拒否してatomic replaceする。publisherのownerで作成しmode 0644とするのは、既存app bind mountから数値metadataを読むため。env、token、DB credentials、task本文、ファイル内容はreportへ入れない。

監視用lockはpersistence scope外に置く。reportのpublishだけは**既存deployment lockの非blocking shared flock**を取り、backup tar収録とreportのtemporary file作成が競合しないようにする。deployment lockを作成・削除・truncateせず、保持者を中断しない。P0計測、du、Docker queryの間はdeployment lockを保持しない。

lockが使用中ならworker内で最大120秒だけpublicationを待つ。Runner終了時はworkerへstopを通知し最大1秒joinした後、既に採取済みのpending reportだけを非blockingで1回publishする。終了時に再計測や待機retryを行わない。取得できない場合は前reportを保持し、次の巡回が再計測する。

monitorの例外は固定メッセージと例外型だけをjournalへ記録し、Runnerのtask outcomeやP0 guard判定を変更しない。監視が成功したという理由でP0を省略することもない。

## 取得値と容量判定

P0の`measure('runner', None)`から、未知の新imageを必要とするRunnerとnew deployの両方を`evaluate()`する。current SHAに対する軽いsame-SHA reconcileとして計算しない。同一deviceのavailableを複数回足さず、P0のfilesystem groupingを維持する。

- root、Docker root、releases、backups、shared、Runner source/worktrees、temp/controlのtotal/available bytes、available inode、使用率を記録する。
- reportの`usage_percent`は`(total - available) / total`であり、reserved blockも使用側に含む。`filesystem_usage_semantics`を添える。通常の`df`のusedとreserved扱いが異なることに注意する。
- backup/release/shared/worktree/temp/evidenceの`du -sx -B1`による割当bytesと、evidenceのlegacy entry件数・v2 index countersを記録する。`operation_entries`はv1 terminal件数の意味を維持する。v2はread-onlyで検証し、破損・読取り不能時は`index_status=unavailable`と`supplementary_errors=evidence_index`を出し、未検証counterを掲載しない。mountを横断せず、scope外のfileを辿らない。permissionや走査中変化で取得不能なら0と捏造しない。
- `docker system df`のimage/container/volume/build cache size/countを取得する。CLIの丸めたdecimal sizeをbytesへ換算した**logical estimate**であり、filesystem実割当・reclaimableと区別する。共有layer/cacheを足して回収可能容量にしない。

| status | 条件 |
| --- | --- |
| `OK` | Runner/new deployが両方P0を通過し、追加の既存P0 reserve以上のbytes/inode余裕が残る |
| `WARNING` | 両方P0を通過するが、いずれかのfilesystemで`available - required`がP0の既存bytes reserveまたはinode reserveより小さい |
| `CRITICAL` | Runner/new deployのいずれかがP0上bytes/inode不足、またはP0の計測が成立しない |

WARNINGのreserveはP0の`max(1 GiB, totalの5%)`と`max(8192 inode, total inodeの1%)`を再利用する。新たな「使用率80%」等の独立閾値を追加しない。P0が不足しても取得可能なroot/filesystem/du情報を残す。補助duやDocker分類だけが取得不能の場合はP0結果を変えず、その欠測を明示する。

## 既存Discord経路と通知隔離

[`bot/services/storage_monitor.py`](../../bot/services/storage_monitor.py) を [`main.py`](../../main.py) の独立した1分loopから呼ぶ。通知担当は`ichiyon`だけで、既存`AI_TASK_DISCORD_GUILD_ID` / `AI_TASK_DISCORD_CHANNEL_ID`に一致するchannelを使用する。mentionsは全面無効、任意の外部文字列やfile contentsを通知へ転送しない。

WARNING/CRITICALを通知し、同じstatus/reasonは1時間cooldownとする。WARNINGからCRITICALへの悪化は直前通知が成功済みなら即通知できる。transport失敗・不確実な送信は5分間retryしない。送信前に試行時刻をatomic保存し、再起動後も短時間にspamしない。state保存に失敗したら送信せず、memory上でもretry間隔を維持する。

採取から20分を超えたreportは`CRITICAL / MONITOR_REPORT_STALE`とし、元の採取時刻を表示する。reportが初めからない場合もbot起動後20分の猶予を置き、計測不能として通知する。これは監視停止の警告であり、現在の容量不足を実測したという意味ではない。channel解決・送信・state保存・report validationの例外はloop内で閉じ、BotやRunnerを停止しない。

## ログと証拠の保持

appのadmin/bot/bot-irsiaだけにDocker `json-file`の`max-size=10m` / `max-file=5`を設定する。DB/VPNのlogging設定は変更しない。既存appは明示的な上限がなかったため、通常deploymentで再作成されたappから適用する。過去のjournalや障害資料をvacuumする手順は追加しない。

Runnerに追加する新しい診断fileは10 MiB × current + 5 rotated files、1 messageは32 KiB程度までに制限する。directory 0700、file 0600、symlink/hardlink/異なるownerを拒否する。新しいstream内だけをrotationし、既存journal・過去Codexログ・障害資料を削除しない。既存system journalのrotation方針はhost設定の確認・別の適用記録と合わせて扱う。

deploymentのdurable receiptはログrotation対象にしない。P1c-2A.1はobjectを残さないと検証できたsuccessful reconciliationだけを直近64件のrawと累積rollupにまとめ、object/incidentのlossless receiptと既存v1は保持する。monitorのevidence総量・件数は運用判断用であり、自動回収triggerではない。

P1c-2Aのv1 writerはsame-SHA reconcileでもactiveとterminalの2文書を残し、一括inventoryの上限8,192件に到達する問題があった。P1c-2A.1の2026-09-28実測では、timerはservice終了後15秒、実際の直近operation開始間隔は平均152.453秒であった。この窓からの外挿は空から上限まで346.916時間（14.455日）であり、15〜16秒をoperation間隔とする旧仮定は現在の実測値ではない。新方式の分類、限界、atomic compaction、legacy互換性は[専用設計](storage-evidence-index.md)を参照する。

既存Codex stdout/stderr捕捉は64 KiBの上限を維持する。過去taskの最終出力・diff・診断artifactの世代保持は変更しない。host journalにはP1c-2Aで明示的な新上限を配備せず、既存のsystemd管理を維持する。P1c-2Bの保持graphはA/B receiptをlosslessでKEEPし、archiveの未証明参照を保護する。evidence全体の物理bytesや既存task artifact総量を永遠に一定とする機能ではない。

## Deployment evidence契約

固定rootは`/home/ubuntu/ichiyon-storage-evidence`。deploymentのubuntu owner、directory 0700、file 0600を強制し、symlink・別owner・hardlink・不正な親pathを拒否する。taskやCLIからrootを変更できない。新しい正式deploymentは既存FD9のexclusive flockとP0 admissionを通った後にoperationを開始する。LOCK_BUSYや初回P0拒否はoperation開始前なのでreceiptを作らない。

既存v1は`active/<operation_id>.json`をatomic更新し、最後に`operations/<operation_id>.json`へterminal receiptを一度だけpublishした。envelopeは`schema=ichiyon-deployment-evidence` / `version=1` / `payload` / canonical payloadの`sha256`。1文書256 KiB、eventは最大32件。P1c-2A.1の新operationは同じpayload/bindingをv2 index内に保存する。途中のpending file、activeだけの状態、READY単独はterminal receiptではない。checksumは破損検知であり、同じprivileged deployment userによる書換えに対する署名ではない。

payloadはoperation ID、boot ID、owner PID/start ticks、target/previous SHA、created/completed時刻、phase、terminal state、rollback result、lockのdevice/inode、各stage/releaseのdevice/inode/ctime、image ID、migration container ID、reference snapshot digestを保持する。env、credentials、task本文は保持しない。ownerの生存・開始tick・親process chain・FD9の実FLOCKを更新ごとに照合する。PIDの一致だけでは受理しない。

新規`.prepare-*` / `.release-*` / `.backup-*`だけを作成直後にoperationへbindする。開始時に存在したpathやimageを新規所有として採用しない。Docker inventoryとinspectが成功した場合だけ不存在を確認できるとし、取得失敗を不存在へ変換しない。tagが新しくても開始時の全image ID集合に存在したimageは新規所有としない。既存legacy stagingのmtimeや名前だけを根拠とした昇格はない。

success / failed / cancelledを記録し、rollbackは別の結果fieldに残す。catchできない中断はactive/incompleteとして残す。healthとcurrent pointerの確定後はevidence失敗を理由に旧appへ戻さず、処理は失敗として報告する。finish失敗を成功exitへ変換しない。

collectorはactiveとterminalのchecksum連鎖、厳密schema、owner/mode、identityとreference digestを読み直す。receipt採取の前後でfile signatureを確認し、途中で変わればsource stabilityとownershipの証明を無効にする。terminal receiptがあるだけでSAFE_TO_CLEANにはならない。保持期間、現時点の参照不在、唯一の復旧資料でない証明、障害調査完了などP1c-1の残条件を維持する。

## v2 FULL writerとrecovery producer

正式deployのbackupはv2 FULL manifestと全file inventoryを生成する。`data/backups`は引き続き`persistence.tar`へ全収録し、excludeは空、独立recovery archive参照は未使用。previous/infra/manifest/dump/tarをchecksumに含め、tarを最後まで読み、固定DB containerの`pg_restore --list`を実行してからREADYを最後にpublishする。実DBへrestoreしない。metadata/inventoryは8 MiBまでとし、P0既存metadata reserve内に収める。

新readerは既存v1入力とv2 FULL入力を検証できる。これはv2導入前の古いcodeがv2を読めるという意味ではない。旧app/imageへの通常rollbackは維持されるが、deploy writer自体をv2未対応版へ戻す場合はv2 backup世代の読み方を確認する。過去backupを変換・削除しない。

adminとbot/data_storeのJSON history producerは`bot/recovery_history.py`へ集約した。allowlistはquotes/reactions/ng_words/kuji.jsonのみ、出力は従来のdata/backupsに固定する。任意pathやtask入力は受け取らない。root app containerが作るfileは0644、directoryは0755で、hostのubuntu backup writerが全文を読める既存契約を維持する。host shared rootのアクセス制限は変更しない。numeric notification stateもpending段階から0644にして同じ契約を守る。

recovery archiveの固定rootは`/home/ubuntu/ichiyon-recovery-archives`。`migration_plan()`は引き続きplan-onlyでactionsは空であり、productionのproducer切替・既存dataの移動・除外を自動実施しない。producerだけをrollbackしてもlegacy出力をそのまま利用できる。切替には原本inventoryとdelta capture、独立archive publish、隔離FULL/split restore、旧backup保持と参照graphの証明が必要であり、未完了の間はFULL scopeを維持する。P1c-2Bの`cleanup-preserved`は個別cleanupの保管/復元証明で、このproducer migrationの完了を意味しない。

## 検証と運用上の限界

`python scripts/check_ai_task_storage_monitor.py` はP0再利用、bytes/inode、WARNING/CRITICAL、計測不能、同一filesystem、report publication、deployment lock競合、固定通知経路、cooldown、transport/state失敗、runtime隔離をfixtureで検証する。Linux固有のflock/symlink動作はLinux CIで検証する。実通知先へ試験メッセージを送るtestではない。

Runner schedulerを長く停止すればworkerも停止するため、独立したhost監視の代替ではない。reportのstale警告にはapp/botが稼働している必要がある。P1c-2Bのfresh eligibility、receipt/archive保持graph、maintenance window、exact-path executor、partial failure auditは[storage maintenance](storage-maintenance.md)に実装と検証を記録する。monitorからcleanupを呼ぶ経路はない。残るproduction実行・実機/外部前提は[CURRENT_BACKLOG](../CURRENT_BACKLOG.md)で区別する。
