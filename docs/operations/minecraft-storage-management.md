# Minecraft容量管理

2026-10-27の容量整理後に追加した再発防止の実装。既存データの移行・削除、本番BDSの停止・再起動・配備はこの変更の検証では実行していない。以下の機能は、更新したhelperを承認済みの通常経路で導入してから本番に効く。

## 保存と復旧

`scripts/minecraft/minecraft_storage.py` は、同一SHA-256の内容を `PROJECT/.minecraft-storage` に一度だけ保存する。cosmeticsとnative releaseの `original/storage-snapshot.json` は、ファイルのpath・hash・size・mode・mtimeとstore identityを記録する小さなmanifestである。

- 元データとblobを照合し、manifestを確定してから従来の `missing.json` 完了markerを書く。中断したバックアップは復旧原本と扱わない。
- `state.json` にmanifestのSHAを束縛する。別世代の有効なmanifestへの置換も拒否する。
- 復旧はlive変更前にCAS lockを取得して全blobとsnapshot identityを再検証し、world復旧hook・pack削除・独立コピー完了まで同じlockを保持する。lock競合や検証失敗ではlive変更に入らない。復旧ファイルは独立したコピーにする。live pack・world・復元先をhardlink共有しない。SHA一致を理由に稼働データを共有書込みしない。
- 従来の全量copy型 `original/` も新readerで復旧できる。既存世代を勝手にCASへ変換しない。
- native rollbackが旧 `original/behavior_packs/...` 実体を必要とする場合は `backup_source()` がその必要subtreeだけを独立materializeする。変更されたmaterialize結果を上書きしない。
- Native releaseの停止済みfull backup、world rollback、現行・直前の配備情報は維持する。通常配備のbackup/lock/stop/start/検証/rollbackの契約を省略しない。
- 同じ内容・構成・mode・生manifestのlive packは再copyを省略する。catalog digestの一致だけでは省略しない。

`.minecraft-storage` は復旧原本であり、消してよいキャッシュではない。OS lockはプロセス終了で解放される。store内のblobのGC・削除機能はない。storeとCAS manifestを切り離して保存・削除しない。private素材を含み得るためstoreは非公開権限とし、Git除外する。

**コードの後退にも注意:** 旧 `minecraft_cosmetics_apply.py` だけでは新CASバックアップを読めない。CAS世代がある間は `minecraft_storage.py` と対応readerを保持する。旧コードへ戻す前に対応readerで必要な復旧を完了し、独立した復旧点を確認する。バックアップの存在だけで旧reader互換を仮定しない。

## 隔離試験と再利用

`check_junket_isolated_resilience.py` は、検証済みBDS実行ファイルを `PROJECT/.minecraft-storage/runtime/<sha256>/` に一度だけ独立コピーし、コンテナへreadonly file bindする。既存の本番inodeを共有しない。world・設定・可変packは従来どおり環境ごとに独立させる。

同じoperation、full backup、candidate ZIP、stage、生runtime、Python/probeコード、対応adapter入力の成功証明があり、対象コンテナが停止し、残した環境が証明と一致する場合は結果を再利用する。結果に `fresh_bds_test_executed=false` と `new_environment_created=false` を付ける。新たな実機検証成功として報告しない。未知adapterや入力差異、失敗・改変された環境は再利用して上書きしない。

コードを変えて再試験が必要な場合は、既存の限定再試験helperの対象契約を満たす場合だけ利用する。適合しなければ容量判定後に必要な新規隔離環境を作り、失敗環境を保持する。正常な小修正で毎回full cloneを作る運用に戻さない。

## 容量警告・作成制限

GBは10進。Windowsの空き100,000,000,000 bytes未満、Ubuntuの空き10,000,000,000 bytes未満で `LOW_DISK_SPACE` を返す。ローカルnative transport helperは警告をstderrとmetadataにも記録する。

```powershell
python -B scripts/minecraft/minecraft_storage_plan.py --project . --capacity-only
```

新しい隔離BDS環境では、作成前に「10GBの余裕＋推定作成容量」を満たすことを確認する。満たさなければ新環境は作らない。完全一致の成功証明の再利用は低容量時にも可能。本番復旧の経路はこのテスト用制限を呼ばず、`purpose='recovery'` は警告のみとする。実際のディスク枯渇によるOS書込み失敗まで解消する仕組みではない。

## 整理候補はread-only

```text
python3 -B scripts/minecraft/minecraft_storage_plan.py --project /home/ubuntu/minecraft-bedrock-creative --dry-run
```

このコマンドに `--apply` は存在しない。削除、Git操作、Docker変更、サービス停止、world/DB書込みをしない。必要なprocess情報を読める権限で実行し、権限不足やDocker取得失敗を空の参照一覧に置き換えない。

- 現行・latest・直前の正常状態、明示pin、world/warp/native由来、失敗/処理中/不明状態、processのfd/cwd/mmap、コンテナmount参照を保持する。
- `minecraft-retention-pins.json` のoperation UUIDまたは対象operationの絶対pathを保持参照として読む。例：`{"operations":["<実在するoperation UUID>"],"paths":["<保持するoperationの絶対path>"]}`。実在値は運用者が指定し、サンプルをそのまま本番へ置かない。
- `.reuse` の環境参照も保持する。CAS store、live data、backups、候補ZIP・private入力は候補範囲外。
- 対象はstage/original/dataの**確認対象領域**。operation全体やoriginal全体を削除可能とは判定しない。proof/job/log、world、metadata、固有素材は個別に保護する。
- `REVIEW_CANDIDATE` は「成功済み・保持参照なし・使用観測完了・最低7日経過」の調査候補。7日は作成証明ではなく、dir更新日時からの保守的な確認開始基準。年齢だけで削除しない。
- リンク・共有inode・不完全metadata・不明な使用状況は `NEEDS_REVIEW`。WindowsではLinux process inventoryを取得できないため、容量警告は返すが整理の適格性は確定しない。
- 表示する候補容量は確認対象領域の規模であり、確定削減量ではない。`guaranteed_reclaimable_bytes=0`、`deletion_authorized=false` を維持する。

実削除は別の明示承認を受けた作業で、freshな正確なパス一覧、全対象hash、使用/復旧参照、独立保持元、unique/private/worldの有無を再検査して行う。定期ジョブやlow-space warningからcleanupを呼ばない。

## 作業終了の記録

配備と隔離helperは `storage-receipt.json` に、operation範囲のbefore/afterと増減、新規CAS blob量、再利用量、残した環境、復旧元、容量警告を記録する。巨大project全体を毎回走査せず、該当operationと新規共有データの量を分けて記録する。Windowsの実割当を取得できない計測ではnullを返し、論理bytesを物理bytesと偽らない。

通常のAI作業も終了時に、新規worktree/環境の有無、作成容量の測定範囲、残した成果・復旧元を記録する。owner/branch/未保存変更を確認して既存worktreeを優先し、必要な並行作業の分離やrunner契約だけを理由として新規作成する。

## 導入と残る制約

- Control APIへ `minecraft_storage.py` と新 `minecraft_cosmetics_apply.py` を同時導入する。native transportのTOOLS・install/compile一覧にも共通moduleを追加済み。隔離helper側にも同moduleが必要。
- この文書と同じrevisionのhelperを、通常の承認済み配備で反映する。sourceの統合だけで本番有効化済みとは扱わず、既存helperの最終proofを一次資料にする。
- native transportの新規準備は `--host <承認済みSSH接続先>` と `--dependency-baseline <保持済み成功世代UUID>` を明示する。依存moduleの原本世代を推測しない。既定の本番IPはコードへ埋め込まず、resume時も保存済みtransportと同じ接続先を指定する。
- stageの実体は既存native overlay・復旧・検証の読取り互換性のため維持する。異なるUUIDの配備を勝手に同じoperationへ置換しない。stage・可変world・必要なfull backupには今後も容量が必要。
- CAS化によって過去の重複データが自動的に減ることはない。最初のsnapshotは一意の内容を保存するため容量を使う。完全一致する2回目以降はblob追加0、変更されたファイルの内容とmanifestだけが増える。
- BDS binaryは1バージョンあたり共有実体1個。今回監査した約256MBのbinaryなら、17環境では約4.10GBの重複増加を避けられる。必要な共有1個分は保持する。
- `%USERPROFILE%\Minecraft-Preservation\<保全日時>` 等の検証済み保全バックアップは、この仕組みの対象外で変更禁止。

## 今回のoffline検証

CASの同内容再保存・独立復元・旧形式互換・mode保持・破損/別世代差替え拒否、crash後のlock解放、容量不足と復旧例外、保持graph、read-only bind引数、成功証明再利用を小fixtureで検証した。Linux上の実BDSコンテナ起動・mountのE2Eは実施していない。

関連する変更に限って次のcheckを選ぶ。常に全検証を繰り返す規則ではない。

```text
python -B scripts/minecraft/test_minecraft_storage.py
python -B scripts/minecraft/test_minecraft_storage_plan.py
python -B scripts/minecraft/test_junket_storage_reuse.py
python -B scripts/test_ai_task_junket_native_release.py
```

`check_minecraft_cosmetics_apply.py` はアプリ側の `bot` moduleも使うため、完全なrepositoryで実行する。他の作業コピーのmoduleを混ぜてテストを通過させない。
