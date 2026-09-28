# Production app OCI storage capacity

対象は、いちよんロボ本体のproduction app OCIと、そこに同居するLinux AI Runner。Minecraft BDSホストの容量判定ではない。

backup scopeの分離と復元契約は[storage-recovery.md](storage-recovery.md)、参照を維持した保持判定は[storage-retention.md](storage-retention.md)を参照。P1c-1は契約・隔離restore試験・plan-only判定までを追加し、現行deployのbackup scope、P0の必要容量、production上のデータ配置は変更しない。

## 障害から守る境界

2026-09-28の障害では、10:57 JSTにRunnerのJDK展開とDockerログで最初のENOSPCが起きた。10:59にもavailableは約233 MiBしかなかったが、11:10のdeploymentはappを停止し、約338 MiB必要なpersistence backupを約242 MiBで中断した。その後のcontainer再作成もENOSPCで失敗した。backupが最初にdiskを満杯にした、という順序ではない。

P0は次の段階でavailable bytesとinodeを再測定し、足りない場合は次の重い処理へ進まない。

| phase | 次に進める処理 | 不足時 |
| --- | --- | --- |
| Runner | 新規fetch/worktree/Codex等の重い作業 | 新たな重い作業を開始しない |
| deploy開始 | release staging、Git取得、source展開 | staging/build/app stop/backup/migration/recreateへ進まない |
| build直前 | target sourceを使ったDocker build | buildを開始しない |
| app停止直前 | backup、migration、app切替 | `quiesced=1`にせず、appを1つもstopしない |
| same-SHA reconciliation | 現在releaseのsourceとhealth照合 | build/backup用の予算は要求しない。必要な照合用作業領域は確保する |

Runnerの判定phaseは`runner`。Linuxでtrusted runner設定のrepo rootが `/home/ubuntu/ichiyon-ai-runner-src`、またはそこへ解決されるpathの場合に適用する。各fetch試行、worktree作成、Codex試行、追加テストの前に確認する。Windowsと別repository rootのLinux runnerはこのproduction用判定を実行しない。容量不足は既存の失敗報告経路へ返し、同じclaim内でCodexやdeploymentを自動retryして容量をさらに使わない。

deployment lock、GitHub mainのexact SHA、immutable image/release、migration exact set、DB/VPN identity、healthとrollbackの既存検証は維持する。lock競合は `LOCK_BUSY` として容量不足から区別する。guard失敗時にappが未停止ならrollbackのcontainer再作成も行わない。

## 判定と診断

容量は文字列の`df`出力から解析せず、filesystemのavailable bytesとavailable inodeを取得する。release、backup、shared、Docker root、temporary/staging/worktree、Runner source、helper/infra/pointerを置く`/home/ubuntu`の各書込み先をfilesystem別にまとめる。同一filesystemのavailableを複数回足さず、そのfilesystemに追加される要求量を合計する。既存release、既存backup、既存imageは現在の使用量に含まれ、追加量として再加算しない。

取得不能、未対応の値、計測失敗はfail-closedとし、不明な値を0や十分な容量として扱わない。容量閾値とreserveはinstalled runnerから送られるtrusted codeの定数で管理する。task本文や対象releaseの任意codeで変更できない。

不足診断は `DEPLOY_ERROR=INSUFFICIENT_STORAGE`、`STORAGE_CHECK_PHASE`、available/requiredのbytes・inode、定義済みのfilesystem用途名を使う。測定不能で数値を取得できない場合もphaseと理由は返す。秘密値、DB内容、env内容、task本文、任意のpathを診断へ含めない。成功証明は従来のexact SHAのまま。容量取得の失敗と不足はどちらも進行を止めるが、診断の理由は区別できるようにする。

## 必要容量の計算

実装は `scripts/ai_task_storage.py`。標準ライブラリだけで動作し、remote protocolはinstalled adapterと同じdirectoryのこのmoduleをSSH stdinへ埋め込む。target/previous releaseのstorage codeは実行しない。未展開のshell templateを直接実行しても容量判定を通過できない。

各filesystemの値は `os.statvfs` の `f_bavail × f_frsize` と `f_favail` を使用する。root予約blockを一般ユーザーのavailableへ足さない。`st_dev`が同じpathは1組にまとめ、採取中に通常サービスの書込みが進んだ場合に備えてavailableの最小値を採用する。read-only filesystem、inode統計が取得できないfilesystem、missing pathも中止対象になる。

容量の単位はbytes、MiB = 2^20 bytes、GiB = 2^30 bytes。次の入力はguard実行時に再計測する。

| 記号 | 入力 |
| --- | --- |
| `S` | current source、Runner source（`.git`除外）、存在するtarget sourceそれぞれの保守的な割当見積り/tar見積りの最大値 |
| `N` | 上記sourceのfile/directory等の最大entry数 |
| `P` | 実際のbackup scopeを`--dereference`相当でたどったtar上限見積り。sparse fileも見かけのsizeで計上し、header・長いpath・末尾paddingを加える |
| `D` | PostgreSQLのread-only queryによる現在DBの論理size |
| `I` | 現在の3 appのimage、存在するtarget image、source payloadの4倍の最大値。Dockerのfull image sizeを使い、共有layerが再利用できることを前提に削らない |
| `J` | 存在するJDK imageのsize。未取得なら`I`を代用 |

source未取得の`runner` / `deploy-start`ではsource成長を見込み `S`と`N`を2倍にする。`pre-build` / `pre-stop`では実際のtarget sourceを再計測する。全ての項目を、その書込み先のfilesystemへ計上する。

| 追加量 | 配置先・計算 |
| --- | --- |
| source staging / Git取得 / new release | `runner` / `deploy-start`でreleasesに`3S`。source成長2倍適用後の値。Git/archive/展開の同時存在を見込む |
| 新image / temporary layer / build cache | 新imageが必要な`runner` / `deploy-start` / `pre-build`ではDocker rootに`3I`。image、展開中layer、cacheの3枠 |
| backup / DB dump / manifest | backupsに`P + 2D + 16 MiB`。dump圧縮による縮小を期待せず、既存backupを再利用できる場合もこの余裕を残す |
| 稼働中sharedの増加 | sharedに`ceil(P / 10)` |
| app再作成 | Docker rootに`max(256 MiB, ceil(I / 4))` |
| rollback再作成 | 上記と独立してもう1枠。backupが書けるだけでは停止を許可しない |
| 一時ファイル | `/tmp`に`max(64 MiB, ceil(S / 2))` |
| helper / infra snapshot / current pointer | `/home/ubuntu`に1 MiB。release等が別mountでも、この実際の書込み先を判定する |
| Runner Git取得 | `runner`でRunner sourceのfilesystemに追加`S` |
| Runner worktree / test出力 | `runner`でworktreesに追加`2S` |
| Runner JDK等の一時展開 | `runner`で`/tmp`に追加`max(512 MiB, 2J)` |

`pre-build`では作成済みsource/stagingを追加量から外し、`pre-stop`では作成済みimage/build領域も追加量から外す。どちらも既に減少したavailableを測り直してから、残りのbackup・切替・rollbackに必要な量を判定する。rollback用imageそのものは既存layerの参照を維持するため新たな全量copyは計上しない。

さらに、各filesystemへ一度だけ `max(1 GiB, total bytesの5%)` の安全余裕を加える。これは計測されたピークではなく、通常サービスの書込み、見積り誤差、Docker metadataと復旧操作に残す運用上の保守値である。image全量を使った3倍のbuild枠と独立したrollback枠を先に積み、その上にreserveを置く。小容量の別mountでも1 GiBの最低余裕を満たさない場合は中止する。

inodeも同じfilesystem単位で積む。source stagingは`3N + 256`、新buildは`max(32768, 4N)`、backup32、shared256、再作成/rollback8192、一時ファイル1024、helper/infra/pointer用64。Runnerではsourceに`N + 256`、worktreeに`2N + 1024`、一時展開に8192を追加する。さらに各filesystemへ一度だけ `max(8192, total inodeの1%)` を残す。bytesに余裕があってもinode不足なら進めない。

same-SHAの`reconcile`は稼働appを切り替えず、releasesに`2S`と`2N + 128` inode、temporaryに16 MiBと32 inode、helper用に1 MiBと64 inodeを計上する。各filesystemのreserveは64 MiB / 128 inodeとし、new deploy用のbuild・backup・rollback余裕を要求しない。照合で書き込まないbackup/sharedの別filesystemは判定対象外とする。Dockerはhealth確認の一時container用に判定する。source照合の約0.294 GiBの実測一時増加を無視するわけではない。

これらの係数・reserveはtrusted codeの定数としてoffline testで固定する。依存関係やbuild方式が大きく変わる場合は実際のピークを別途測定し、式とtestをreviewして変更する。過去の通常deployで共有layerが使えたという理由だけで必要量を下げない。

## 初回rolloutと失敗時の運用

古いinstalled runnerからの初回deployでは新しいguardが使われない可能性がある。merge後、正式なdeploymentの直前にread-onlyでavailable bytes、inode、Docker使用量と今回の追加量を採取し、同じ計算式で十分な余裕を証明する。idle catch-upがmainを自動追従する環境ではmerge前にも確認する。調査時点の約9.45 GiB availableを現在値として使わない。

不足または取得不能なら現在のappを動かしたまま人の判断を待つ。prune、backup/release/log削除を容量guardの復旧手段として自動実行しない。既存taskのworktreeや過去task statusも書き換えない。retry時は最新の値を再測定し、容量が増えたことを推定だけで扱わない。

成功後はmerge SHA/current/imageの一致、admin/bot/bot-irsiaのhealthとrestart count、DB migration exact set、DB/VPNのcontainer ID・StartedAt・restart countの維持を確認する。

容量guardは測定時点での必要量判定であり、filesystemのblock予約ではない。判定後の他processによる予想外の大量書込みや未計測の新しいbuild特性まで保証するものではない。reserveを残し、停止直前に再測定する。専用Runner host/volume、quota、継続監視は次の課題とする。

hostのsystemd `ExecStartPre`など、Python Runner起動前のinstalled launcherによるsource同期はこのtask開始guardより前に動く。launcher自体の容量判定は別途運用設定として扱う。また、開始済みのCodexの任意コマンドにdisk quotaを設定する機能ではない。

## Offline検証

`python scripts/check_ai_task_storage.py`、`python scripts/check_ai_task_deploy.py`、`python scripts/check_ai_task_local_runner.py`を使う。実際にdiskを満杯にせず、fake filesystem統計とmock操作で次を検証する。

- 開始時の容量不足ではbuild、app stop、backup書込み、migration、recreateに進まない。
- 開始時は十分でもbuild後に容量が減れば、停止直前の判定で止まる。
- backupだけ書けてもrollback用の余裕が不足する場合、およびinode不足の場合はappを停止しない。
- 同一filesystemのavailableを重複計上せず、別filesystemではそれぞれの必要量を満たす。
- 容量取得失敗を十分な空きとして扱わず、秘密値や任意pathを診断に出さない。
- 十分な容量なら既存protocolを通り、same-SHAは不要なbuild/restartをしない。
- `LOCK_BUSY`、rollback、migration、health、exact SHAとinfra identityの既存契約を維持する。

CIの`.github/workflows/checks.yml`でもstorage checkを実行する。これらのoffline checkの成功とproduction反映の成功は別々に記録する。

## P1設計メモ: backup scopeとretention

P1aのread-only planner、参照graph、提案policyと`data/backups`の実測調査は[storage-retention.md](storage-retention.md)を参照する。P1aは削除もproduction deployも行わない。

P0ではbackup内容も保持世代も変更しない。現行のtar対象は `data`、`assets/images`、`secrets`、`.env`。`data/backups`もそのまま含める。調査では同directoryのpayloadが286,769,111 bytes、全tar payloadの約86.8%であり、354,109,440-byte tarがmanifest上22コピーあった。この重複量を、そのまま安全に削除可能な量とは扱わない。

`data/backups`を除外できるかは、以下をP1で確認してから決める。

1. 各subdirectoryの作成元・読込み元・復元手順を特定し、live runtimeに必要な唯一のデータか、再生成可能な作業backupかを分類する。
2. previous releaseへのrollback、pack復元、DB参照から必要になるassetが別に確実に保存されていることを検証する。単にdirectory名が`backups`だから不要とは判断しない。
3. 除外可能なものを別の管理領域へ移すか、version付きbackup manifestで明示的なscopeを定める。tar作成、`backup_validate`、復元手順、旧形式の互換性を同時に設計する。
4. offline restore試験でDB dump・env・secrets・live data・assetを戻し、previous imageとの整合性を検証する。復元試験が通るまで現在の包括backupを残す。

retentionはbackup・release・imageを一緒に扱うread-only plannerから始める。current、直前の既知正常release、保持backupの`previous`、明示的rollback pin、稼働中/停止中containerのimage参照をたどり、依存するrelease/imageを保持する。Dockerのreclaimable値はこの参照判定の代用にしない。共有layerは実解放量を二重計上しない。tar重複排除を行う場合も、単純なtar削除で各backupのchecksum/READY契約を壊さない。

中断stagingの回収は別の承認済み処理とする。operation ID、owner/lease、開始/終了時刻、deploy lockを使ってactive stagingと失敗残骸を区別し、障害証拠の保存期限と復元参照を確認する。P0は過去の`.prepare-*`や`.backup-*`を削除しない。既存protocolが正常終了時に自分で作ったtemporaryを片付ける動作は維持する。

ログrotation、Docker build cacheのbudget、available/inodeと増加率の監視、Runner作業領域の分離もP1候補。retentionの件数・期限・削除対象は、候補一覧と復元保証をreviewしてから決める。
