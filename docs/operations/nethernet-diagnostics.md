# MCXboxBroadcast / NetherNet 外部回線診断

## 2026-09-28 の読み取り調査

利用者報告は「同じスマートフォンの自宅Wi-Fi・直接IP接続は成功、モバイル回線のフレンド経由はNetherNetエラー」。今回は端末を操作できないため独立した再現は行っていない。

基準main: `f0b53bca237b7cdcfc86628eb4b6c0240c263665`（fetch後にHEADとの一致を確認）。
Runner設定のBDS SSH接続を利用。過去文書の接続先はhost keyが一致しなかったため利用せず、検証を無効化していない。

| 項目 | 実測 |
| --- | --- |
| Broadcast | `mcxboxbroadcast`, build 154 / `git-master-5dc1f86`, Bedrock 1.26.50 (2193) |
| 稼働イメージID | `sha256:d4b03ab4a5b046c5ac2b19afabb329157d3d417f61d92c114c96f912ffa4f0fe` |
| JAR SHA256 | `e785d47be8a724dc0537808d29e0fbb9b0e4e22b9bf80b0b9a20f21321736586` |
| transport / WebRTC | `dev.kastle.netty` 1.7.4 / `dev.kastle.webrtc` 1.0.4（該当buildの依存定義と稼働クラスを照合） |
| Compose | `/home/ubuntu/mcxboxbroadcast/compose.yml`, service `mcxboxbroadcast` |
| Mount | `/home/ubuntu/mcxboxbroadcast/config` → `/opt/app/config` |
| ネットワーク | host、ICE UDP 19140–19149、debug-mode=false |
| ホストFirewall | INPUTにUDP 19140:19149 ACCEPTとESTABLISHED/RELATED許可、末尾REJECT。OUTPUT policy ACCEPT |
| 経路 | IPv4 defaultあり、IPv6 defaultなし |
| Broadcast状態 | running、restart count 0、起動2026-09-25 09:33:37 UTC、healthcheckなし |
| BDS状態 | `minecraft-bedrock-creative` running / healthy、restart count 0、起動2026-09-28 00:59:05 UTC |
| BDS公開ポート | 19134 TCP/UDP、8420–8449 UDP |

Docker直近15,000行にCONNECTREQUEST / CONNECTRESPONSE / CANDIDATEADD / host・srflx・relay candidate / selected pairの記録は0件。保存されたgzipログも検索したが候補・接続シグナリングの記録はなかった。直近ログにはsession update例外11件とHTTP接続timeout等があるが、ユーザーの試行時刻や回線との対応は不明。ClassCastExceptionは0件。サーバーを再起動せず調査した。

古い`log4j2-nethernet-trace.xml`は存在するが、現在のJava起動引数・環境に適用設定はない。稼働版のソースではdebug-modeはStandalone loggerのみを変更する。transportのtraceにも正常時の全シグナリングとselected pairはないため、単なるdebug切替では比較に不足する。

**原因は未特定。** ホストの許可ルールはクラウド側のNSG/security list、キャリアNAT、TURN割当て、双方向疎通の証明ではない。IPv6 defaultなしだけでも今回の原因とは断定できない。TURN、Firewall、IPv6、ICE範囲、BDS設定は変更していない。

一次ソース:

- [Broadcast buildの元commit](https://github.com/MCXboxBroadcast/Broadcaster/commit/5dc1f86)
- [transport 1.7.4](https://github.com/Kas-tle/NetworkCompatible/tree/d48f7d3869a22bfb5b2ff3d1e77484d877b3b05c)
- [WebRTC 1.0.4](https://github.com/Kas-tle/webrtc-java/tree/778a8218c235cc8e0fd56f2a7c2f08cd705799a0)

## 診断の実装

`scripts/nethernet/build_diagnostics.py`は稼働JAR全体・対象クラス・上流ソースのSHA256を検証し、同版の3クラスに観測処理だけを追加したoverlay JARを生成する。上流ソースの取得先は固定commit。元JAR、接続処理、ICE設定、認証キャッシュは置換しない。未知版へ流用せず再調査する。

`Diagnostics.java`は次をUTC、起動UUID、connection IDでJSONLへ記録する。

- CONNECTREQUEST受信、CONNECTRESPONSE送信試行、双方向CANDIDATEADD、CONNECTERROR。
- SDP内とtrickleのcandidate種別host/srflx/prflx/relay、UDP/TCP、IPv4/IPv6、ポート。
- TURN取得/解析/RPCエラー、取得ICE server数、SDP設定・answer生成の失敗段階。
- ICE gathering/state、peer state、candidate error code、handshake timeout、data channel成立。
- 状態変化時と1秒ごとの`getStats`。transportの`selectedCandidatePairId`で選択ペアを判定し、local/remote候補、nominated、要求/応答数、送受信bytesへ対応付ける。close時は定期処理を取消す。

送信「試行」は相手への配送成功とは異なる。取得失敗・回線途中の欠落・ログrotationは欠測として扱う。candidate pairの存在だけで選択成功と判断しない。BDSへの転送後やゲーム参加の成功はスマートフォンの結果と照合する。

IP・candidate ID・TURN URLは起動ごとのsaltで仮名化。SDP、identity、ufrag/password、TURN認証、Xboxアカウント識別子、任意のエラー本文、raw statsを出力しない。診断専用directoryは0700、JSONLは2 MiB×4世代、最長24時間で記録終了。一般のBroadcastログとは別で、root DEBUG/TRACEは不要。ログの実体は認証cacheと同じbind mount内に残るが、収集対象は診断JSONLのみ。

## Runner向け配備引継ぎ（未実行）

通常Runnerはcommit / push / PR / CI / merge / app deploymentを担当する。このrepoの既存app/pack adapterは外部Broadcast Composeを管理していない。**app deployment成功だけではこの診断を有効化したことにならない。** 下記はmerge後にRunner/運用側で実行する追加工程。実装Codexから本番へのコピー・再起動はしていない。本番反映SHAと診断有効化の証明が揃うまで有効化完了と通知しない。

1. merge SHAのcheckoutから、BDSの稼働コンテナにある`/opt/app/MCXboxBroadcastStandalone.jar`をローカルへ取得する。認証cacheは取得しない。JDK 21とPython 3.8以降で実行:

   ```sh
   python3 scripts/nethernet/build_diagnostics.py --original-jar /private/MCXboxBroadcastStandalone.jar --output /private/nethernet-diagnostics.jar
   ```

2. 下記offline検証を実行し、merge SHA・元JAR SHA・overlay SHA256を記録。JARと`compose.diagnostics.yml`をBDSの`/home/ubuntu/mcxboxbroadcast/config/nethernet-diagnostics.jar`と`/home/ubuntu/mcxboxbroadcast/compose.diagnostics.yml`へ配置。元Composeとconfigのbackupを保持し、他の環境変数・mount・command変更がないことを確認。overrideは調査したローカルimage IDに固定し、mutableなmasterタグの更新を取り込まない。pullしない。rollback前にもbase Composeのimageが元image IDを指すことを確認する。
3. BDS側で既存サービスだけを再作成（フレンド経由接続は一時的に利用不可）:

   ```sh
   cd /home/ubuntu/mcxboxbroadcast
   docker compose -f compose.yml -f compose.diagnostics.yml up -d --no-deps --pull never mcxboxbroadcast
   ```

4. 新しいJSONLの`diagnostics_ready`、`ice_servers`とその数、Broadcast running、通常のsession開始を確認。収集メタデータでBDSのStartedAt/healthが変わっていないことを確認する。起動失敗や`NETHER_DIAGNOSTICS_UNAVAILABLE`なら試行案内を出さず下記rollback。本番でXbox認証とスマートフォン接続を行う検証は未実施。
5. 試行前に有効期限24時間以内とログの残容量を確認。下記2試行後すぐに収集する。診断対象はBroadcastのみ。world、pack、Web素材、BDS、bot/adminの再配備をこの追加工程へ含めない。

rollback / 診断終了はbase Composeだけで同じserviceを戻す:

```sh
cd /home/ubuntu/mcxboxbroadcast
docker compose -f compose.yml up -d --no-deps --pull never mcxboxbroadcast
```

元JAR/configは変更しないため、overlayファイルを削除する必要はない。診断ログと配備記録は保持。`down`やvolume/world操作は不要。

## スマートフォンから一度の比較試験

診断有効化済みの通知を受けてから、同じ端末・同じMinecraftアカウントで次を連続して行う（各回1回、計2接続）。

1. 自宅Wi-Fiを有効にし、フレンド一覧のBroadcastから1回参加。成功したら10秒程度待って退出。接続開始時刻（秒まで・タイムゾーン付き）、結果、退出時刻を控える。
2. 30秒空けてWi-Fiをオフ、モバイルデータ通信を確認。同じフレンドから1回参加。失敗表示または90秒まで待ち、開始/終了時刻と正確な表示を控える。成功した場合も報告する。
3. 「Wi-Fi開始/終了/結果、モバイル開始/終了/結果」をDiscordへ返信。自動再試行・他端末の試行を混ぜない。

運用側はRunner環境からread-onlyで収集（新規private directoryを指定）:

```sh
python3 scripts/nethernet/collect.py --ssh-from-runner-env --output /private/nethernet-trial
python3 scripts/nethernet/summarize.py /private/nethernet-trial/events-*.jsonl --start 2026-09-28T10:00:00+09:00 --end 2026-09-28T10:01:30+09:00
```

上の日時は例。Wi-Fiとモバイルそれぞれの実測windowで1回ずつ集計する。BDS上で直接収集する場合はSSH optionを省略。収集時のrotationや書き込み途中行は`invalid_lines`や欠測として報告し、接続0件を成功扱いにしない。

比較順序: request未着 → answer生成/送信試行 → 双方向candidate → server数/relay候補/ICE error → pair requests/responses/選択ペア → data channel成立 → 端末の参加結果。TURN serverが列挙されたことはTURN allocation成功を意味しない。relay候補がない場合もログの有効期間・欠測を先に確認する。

## 検証

```sh
python3 scripts/check_nethernet_diagnostics.py
javac -proc:none --release 21 -cp /private/nethernet-diagnostics.jar:/private/MCXboxBroadcastStandalone.jar -d /private/check-classes scripts/nethernet/DiagnosticsCheck.java
java -Dichiyon.nethernet.diagnostics=/private/check-events -Dichiyon.nethernet.seconds=1 -cp /private/check-classes:/private/nethernet-diagnostics.jar:/private/MCXboxBroadcastStandalone.jar ichiyon.nethernet.DiagnosticsCheck expiry
python3 scripts/check_nethernet_diagnostics.py --events /private/check-events/events-0.jsonl
```

check-eventsは毎回新しいdirectoryにする。fixturesは実ネットワークへ接続せず、unsigned connection ID、SDP/trickle、IPv6 relay、statsの選択ペア対応、秘密値非出力、期限切れ、稼働依存とのclass loadingを検証。実際のWi-Fi/モバイルやTURN接続の成功を保証するテストではない。

Discord最終通知にはRunnerが確認した本番merge SHA、Broadcast overlay SHA/有効化時刻、BDS health、原因未特定であること、上記2試行を記載する。未配備のSHAを「反映済み」と記載しない。

今回の実行結果: Python regression 7件、稼働JARに対するJDK 21 overlay compile、Java fixtureでpatched signaling dispatch/元のinactive時挙動/候補と選択ペア/秘密値非出力/期限切れを確認して合格。Python構文チェックとgit diff --check、実機の既存Compose＋overrideのconfig -qも合格。実機で未有効化の収集は想定どおり失敗として検出した。CIにはoffline Python regressionを追加したが、GitHub CI自体はRunnerのPR公開後に実行される。
追加で、外部通信を無効化した一時JDKコンテナ内で稼働JARのnative PeerConnection生成、getStats callback、close/disposeのsmoke checkも合格。実際のICE接続ペア確立は端末試験待ち。
