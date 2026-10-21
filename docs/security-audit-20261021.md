# いちよんロボ セキュリティ監査 — 2026-10-21

## 2026-10-22 継続作業（Runnerへの公開引き渡し時点）

以下は前回報告の追補。後段の「本番未反映」「未ビルド」「PyNaCl例外」は
**10月21日の履歴**であり、この追補の状態と区別する。公開先mainの基準を
`d8055ce1848fd72db82a38a69a2eaea606d21c63`へ進め、他の作業者のJUNKET追加を保持した。
本番反映はユーザーが明示承認。アプリのcommit/PR/CI/merge/deployは既存Runnerへ委ねる。
この時点ではアプリのリリース完了をまだ証明していない。

| ID | 再評価・追加対応 | この時点の本番状態 |
| --- | --- | --- |
| F01 | 更新CAを必須化したDocker実ビルド成功。certifi 2026.10.19、Node/Denoのストアも対象 | アプリ切替待ち。EOLホストのCA更新も残る |
| F02 | 実イメージでNode 22.23.4を確認 | アプリ切替待ち |
| F03 | Pillow 13.0.0でLinux画像・Minecraft検査成功 | アプリ切替待ち |
| F04 | production DBを16.15へ更新、既存volume保持、バックアップをpg_restore --listで検証 | production更新済み、stagingは別途必要 |
| F05 | Discordの登録callbackはHTTP。利用可能なブラウザーにDeveloper Portalの認証済みセッションなし。Bot向け公式Edit Application APIはredirect_uris更新を提供しない | 未解決。ログインを壊すHTTPSへの一方的切替はしていない |
| F06 | production DBを127.0.0.1:5433に限定。OID 10のbootstrap roleはSUPERUSERを外せないため、独立したichiyon_runtimeを新規作成。既存秘密値は変更せず、制限付き接続を保護された設定へ追加 | DB公開範囲は修正済み。アプリroleの切替は通常リリースで行う |
| F07 | アプリはDebian 13.7。FFmpeg 7.1.5にはDASH等の未修正問題があり、公式署名を検証した9.0.2を採用。必要なaudio/video codecを維持し互換性試験 | Ubuntu 20.04ホスト2台のEOLは未解決。復旧console/snapshotの経路未確認につき強行upgradeしない |
| F08 | Alpine 3.24 / OpenVPN 2.7.7のVPN image実ビルド成功。CONNECT先IPを固定したHTTP経路用に80番を許可 | VPNの本番切替・再接続確認待ち |
| F09 | yt-dlpとFFmpegをprocess-local egress proxy経由に統一。毎接続のDNS判定、非公開/混在answer拒否、検査済みnumeric IP接続、redirect/segmentの再検査。VPNにもnumeric IPのみ渡す。管理hostの別名・raw TCP/file/data入力を拒否 | アプリ切替待ち。公開HTTPS sampleの抽出・再生は実成功 |
| F10 | TLS拒否の実試験をFFmpeg 9.0.2でも確認。平文HTTP音源自体は暗号化されない | アプリ切替待ち |
| F11 | legacy 16 routeの認可回帰試験成功。現在のflagは無効 | アプリ切替待ち |
| F12 | Origin/Referer CSRF検証とPOST logoutをLinuxでも検査 | アプリ切替待ち |
| F13 | session/state期限、secret検証、Cookie属性を検査 | アプリ切替待ち。HTTPS未導入の制約はF05 |
| F14 | bounded upload/bodyとprocess-local rate limitを検査 | アプリ切替待ち。分散/slow-client対策はedgeで継続 |
| F15 | hashlock、action SHA、権限縮小に加え、runtime imageからpip/setuptools/ensurepipを除去。installer同梱の旧msgpack/urllib3等が本番runtimeへ残らない | アプリ切替待ち |
| F16 | no-new-privilegesを設定。production immutable mountは維持 | 非root化とstagingのrepo RW mount縮小が残る |
| F17 | discord.py本体2.7.1とvoice依存を明示指定し、PyNaCl 1.6.2 / davey 0.1.6へ。依存解決正常、実際のVoiceClient暗号化APIで往復・改ざん拒否を確認。GHSA除外を削除 | アプリ切替待ち。pip-auditは除外なし0件 |
| F18 | BDS 1.26.36.1、オンライン認証有効。自前BDSに該当すると確認した未修正CVEはない。公開参加の方針は勝手に変更しない | worldとaddonを保持。mutable latestと更新運用の課題は残る |
| F19 | OAuth/DB例外redaction、音声URL/cookieのログ抑制を維持 | アプリ切替待ち |
| F20 | Unicode tokenの401回帰試験成功 | アプリ/Control API切替待ち |
| F21 | CIにauth/media/crypto/egress/pip-audit/実DB role検査を組み込み、最新mainのMinecraftチェックも保持 | GitHub CIの実行待ち |

追加検証:

- 専用WSL2 Ubuntu 24.04（公式rootfs checksum照合）でDocker実ビルド成功。アプリとVPNの両imageを検証した。
- workflow記載の118 command（Python 98、Node 20）をLinuxで実行。4 commandの初回失敗はWindows exportのCRLFとTLSエラー文言差を修正して再実行成功。Gitと同じclean filterを通したexportで検証し、検査を無効化していない。
- Windowsで失敗したbackup split 13ケース、backup restore 50ケース（実DB復元を含む）、nethernet 7ケースはLinuxで成功。DB復元用の実PostgreSQLは17、業務DB統合は16.15。GitHub CIでは16.15/PG16 toolsを使用する。
- resource-packの実base/head比較は公開PRのCIで行う。テスト本体は成功し、この変更でpack原本は変更していない。
- 新しいruntime roleでCRUD/sequenceを成功させ、DDL・migration ledger更新・server file読取・管理role継承を拒否するPostgreSQL試験成功。既存migration/backupの管理接続は保持する。
- FFmpeg 9.0.2でmedia security、voice pipeline、voice crypto、egress、Minecraft big/akki/wall video、molcar record、admin security、Compose/env scope検査成功。公開FFmpeg sampleをyt-dlpで取得し、証明書検証を有効にしたFFmpegで再生成功。別の公開sampleのCloudflare 403は迂回せず、提供元の公開sampleに切り替えた。
- 実imageのTrivy検査: Debian package 133件。Python脆弱性0件。OS候補はCritical 1 / High 55 / Medium 96 / Low 151 / Unknown 2（package重複を含む）、検出された版にベンダーfixed versionがある未適用更新は0件。これは「OSに問題なし」の意味ではない。FFmpegは公式sourceからのbuildなので、scannerの自動検出とは別に署名・version・修正commitを照合した。
- Gitleaks 4候補は既存example placeholder / test用fake key。新たな秘密値の混入は確認していない。秘密値を成果物に記載していない。

FFmpeg更新の根拠: [公式9.0.2と署名手順](https://ffmpeg.org/download.html)、
[DASH CVE-2026-75146 / Debianの7.1系修正待ち](https://security-tracker.debian.org/tracker/CVE-2026-75146)、
[PNG encoder CVE-2026-66040](https://security-tracker.debian.org/tracker/CVE-2026-66040)。
固定SHA256はinstall_ffmpeg_runtime.shに記載。CA更新・PyNaCl等の一次根拠は後段を参照。
[Discord公式Edit Current Application](https://docs.discord.com/developers/resources/application#edit-current-application)ではredirect変更不可。
[Let's EncryptのIP証明書](https://letsencrypt.org/2026/03/11/shorter-certs-certbot)自体は利用できるが、OAuth登録更新と自動更新経路も揃えてHTTPSへ移行する必要がある。

production DBの検証済みbackup: `/home/ubuntu/ichiyon-backups/security-db-20261021T183735Z`。
DBのrole分離手順は[security-runtime-db.md](operations/security-runtime-db.md)。
本番アプリのPR/SHA/CI/配備結果はRunner完了時の証拠で追記・報告する。

### 公開CI初回失敗の修正

PR #157 の run `39655717814` は `python-and-compose` のツール導入で停止した。
`ubuntu-latest` が Ubuntu 26.04（resolute）を選択し、APT が
`Unable to locate package postgresql-16` を返したため、このジョブを
事前検証と同じ Ubuntu 24.04 に固定した。PG16 の復元試験、DB role 検査を含む
全検査コマンドは維持している。ローカルで workflow YAML の読込、実行環境指定以外の
ジョブ定義が不変であること、`git diff --check` を確認。修正後の GitHub CI 完走と
公開・本番反映の確認は Runner が実施する。

---

### 公開再試行時の照合と引き継ぎ

Runner の attempt 3 は `No changes to publish` で停止した。再試行開始時の
worktree は clean で、HEAD `c183353` に PR #157 の `cf392aa` と
CI 環境修正 `6a89d78` が統合済みだった。修正の消失ではなく、統合後に
新規差分がない状態で公開処理へ進もうとしたことによる停止である。

指定された `publication.patch` の SHA256 は
`a378f19ac1b92d38e4a1179bcfb49a5e4332b68b20aa2a8da09b11767e111337`
と一致した。独立した一時 Git index に基準 `d8055ce` と patch を適用し、
現 HEAD と照合した。38 ファイル中 36 ファイルは完全一致し、残る差分は
workflow の Ubuntu 24.04 固定と上記 CI 失敗修正の報告のみだった。
実 worktree への二重適用・既存変更の巻き戻しは行っていない。

今回の追加変更はこの引き継ぎ記録のみ。既存の Linux 118 command、実 build、
DB 復元等の検証は重複実行していない。Runner は公開候補 SHA の CI 成功を
確認したうえで、承認済みの通常 app release を既存入口から継続する。
保護 env の `RUNTIME_DATABASE_URL` を Compose のアプリ接続へ反映し、
migration helper の元の `DATABASE_URL` を維持する。DB/VPN/BDS の再作成は
この再試行に含めない。実装作業側では CI 完走・本番切替の完了証拠を
取得しておらず、完了扱いにはしない。HTTPS OAuth 登録・EOL ホスト等の
残件は親 operator の並行対応として引き続き追跡する。

## A. 総合評価

**安全なソース修正とローカル検証を実施したが、本番の安全性を確認済みとは判定しない。** 本番・Minecraft/staging の SSH 読み取り専用調査で、平文 HTTP の管理画面、DB の全インターフェース公開、アプリ接続ロールの superuser、未適用のセキュリティ更新、サポート切れ OS を確認した。音楽 URL の SSRF も追加対策が必要。本番デプロイ、秘密値変更、DB 変更、サービス停止、ワールド操作は実施していない。

| 優先度 | 問題グループ数 | 判定 |
| --- | ---: | --- |
| P0 | 0 | 現在悪用可能な重大問題・秘密漏洩を確証付きで認定した件数。不存在の証明ではない |
| P1 | 10 | 既知の重大問題、通信・公開範囲・実行環境の問題 |
| P2 | 9 | 条件付きの認証不備、過剰権限、サプライチェーン等 |
| P3 | 2 | エラー処理・継続検査の改善 |

以下の F01–F21 を各 1 件と数えた。CVE 数、パッケージ別の重複検出数、修正済み件数とは異なる。ソース修正済みの項目も、本番では未反映。

### 範囲と確認方法

- 対象: `U-KID-AI/ichiyon-robot`、基準 SHA `2c9f836fee6e721a2676a1877747d17b50ba19ab`。専用 branch `codex/security-audit-20261021` で作業。元の作業ディレクトリの未コミット変更は変更していない。
- [AGENTS.md](../AGENTS.md)、[AI_RULES](AI_RULES.md)、[AI_RUNBOOK](AI_RUNBOOK.md)、既存運用手順に従った。公開操作は runner の責務のため、commit / push / PR / merge は未実施。
- 公式アドバイザリ、ベンダーの修正版情報、依存ロック、コード、Compose、唯一の workflow `checks.yml`、起動・更新スクリプトを照合した。情報の基準日は 2026-10-21。
- 既知 SSH ホスト鍵を厳格検証し、稼働パッケージ、コンテナ設定、SSH/iptables、DB の設定・ロール属性のみを読み取り。DB 業務データや秘密値を成果物へ出力していない。
- 公開管理画面の `/login` へ通常の GET を各 1 回行い、本番のポート 8000、staging の 8080 が HTTP 200 を返すことを確認した。ログイン試行・侵入テスト・負荷試験はしていない。
- OS パッケージの全一覧・Python の実測一覧は [inventory.json](security-audit-20261021/inventory.json)。CVE 候補は [os-scan.json](security-audit-20261021/os-scan.json)、Python 検査結果は [python-scan.json](security-audit-20261021/python-scan.json)、試験台帳は [tests.json](security-audit-20261021/tests.json)。秘密値、利用者データ、ソース一式を外部スキャナーへ送信していない。

### 実機と変更後の対比

| 対象 | 実測 / 変更前 | この変更の到達点 |
| --- | --- | --- |
| ホスト OS（2 台） | Ubuntu 20.04.6、kernel 5.15.0-1081-oracle、Ubuntu Pro 未接続 | 未変更。OS 更新または ESM の運用計画が必要 |
| Docker / containerd | 28.1.1 / 1.7.27 | 未変更。ホスト更新と併せてベンダー修正を適用・再検査する |
| Python | 本番 3.11.17 / staging 3.11.16 | `3.11.17-slim-trixie` と image digest を固定 |
| Node.js | 本番 22.23.3 / staging 22.23.2、元 Dockerfile は `node:22-slim` | 22.23.4 公式バイナリの SHA256 検証付き導入。CI も 22.23.4 |
| Deno | 本番 2.9.7 / staging 2.9.6、元ビルドはダウンロードした installer を実行 | 2.9.7 の公式 bin image と digest を固定、CA は system store を使用 |
| アプリ OS | Debian 13、303 パッケージ | ビルド時にディストリビューションの更新を適用。更新後の実 image は未ビルド |
| OpenSSL（アプリ） | 本番 `3.5.7-1~deb13u3` / staging `3.5.7-1~deb13u2` | Debian backport を基準に更新。本番 u3 は 9 月修正適用済み |
| OS CA / certifi | Debian `20250419` / Python `2026.7.22` | Debian CA `>=20250419+deb13u1` を必須化、certifi 2026.10.19 |
| FastAPI / Uvicorn / discord.py | 全 Python 実測は inventory 参照 | 0.142.2 / 0.54.0 / 2.7.1 と推移依存を hash 固定 |
| aiohttp / Pillow | 本番 3.14.4 / 12.3.0 | 3.14.5 / 13.0.0。画像・Minecraft 関連試験を実施 |
| PyNaCl | 1.5.0 | discord.py voice の制約で維持。F17 の明示的例外 |
| yt-dlp / FFmpeg | 2026.7.4 / `7.1.5-0+deb13u1` | yt-dlp を固定、FFmpeg 呼び出しを制限。未修正 codec CVE は残る |
| PostgreSQL（両環境） | 16.14、SSL off、拡張は plpgsql のみ、replication slot 0 | Compose/CI の image を 16.15 に指定。DB 本体は未更新 |
| VPN | Alpine 3.20.10、OpenVPN 2.6.20、tinyproxy 1.11.2、OpenSSL 3.3.7 | Alpine 3.24 digest + apk 更新。公式 package の OpenVPN 2.7.7 を対象。実接続互換性は未確認 |
| Minecraft BDS | 起動ログの Version は 1.26.36.1、`itzg/...:latest` / `VERSION=LATEST` | 未変更。ワールド・クライアント・pack 互換性検証を要する |
| PHP | ソース・manifest に実装なし、両ホストで PHP コマンド・dpkg パッケージなし、アプリ image もなし | 今回は未使用として対応不要 |

Node は CI と yt-dlp の JS runtime 用であり、公開 Node HTTP サーバーではない。リポジトリ内に npm の依存 manifest/lock はなく、Minecraft の JS は Bedrock API を利用する。npm audit の対象となる依存ツリーはない。

VOICEVOX `cpu-ubuntu24.04-0.25.0`、POT provider `1.3.1` は任意 profile として存在するが、今回の稼働一覧では未起動。MCXboxBroadcast build 154 / Java 21 は診断用 overlay の参照を確認した範囲で、稼働 JAR の検証はできていない。これらを「実機検査済み」とは扱わない。

## B. 発見した問題

### P1

| ID / 対象 | 内容・識別子・影響条件 | 対応状況・根拠 |
| --- | --- | --- |
| F01 CA 信頼ストア | eMudhra の中間 CA 秘密鍵侵害により、古い信頼ストアを利用した TLS が MITM の影響を受け得る。certifi は **GHSA-6q48-6c7w-9gw2**、CVE なし。OS、Python、Node、Deno のストアは別に評価が必要 | certifi 2026.10.19、Node 22.23.4、Debian/Alpine CA 最低修正版、Deno system store をソースへ反映。本番未更新。[一次報告](https://emudhra.we-have-a-problem.com/)、[certifi advisory](https://github.com/certifi/python-certifi/security/advisories/GHSA-6q48-6c7w-9gw2)、[Debian DSA-6572-1](https://security-tracker.debian.org/tracker/DSA-6572-1) |
| F02 Node.js 22 | 修正前の 22 系に DNS/TLS 検証、Brotli OOB write、keep-alive、snapshot 乱数等の問題。特に **CVE-2026-111497** は悪意ある圧縮データ処理で RCE の可能性。ただし当アプリでの攻撃到達を立証したものではない | 修正版 **22.23.4** へ。公式 checksum 照合、同版で Node チェック 7 本成功。本番未反映。[公式 10 月情報](https://nodejs.org/en/blog/vulnerability/october-2026-security-releases)、[22.23.4](https://nodejs.org/en/blog/release/v22.23.4/) |
| F03 Pillow | 12.3.0 に **CVE-2026-112395 / 112397 / 112400 / 112404 / 112895 / 112896 / 112897 / 112899**。TIFF/GIF/PDF/EPS 等の異常入力に関する OOB、漏洩、DoS。すべての形式・処理がアプリから到達可能とは限らないが、画像入力を扱うため優先更新 | **13.0.0** に更新し画像・Minecraft 関連試験成功。Python 3.11 対応を確認。メジャー変更のため Linux image でも最終確認が必要。[公式 release](https://github.com/python-pillow/Pillow/releases/tag/13.0.0)、個別 GHSA は python-scan.json |
| F04 PostgreSQL | 16.14 に権限・memory safety 問題。代表は **CVE-2026-16239 / 14669 / 14664 / 6471 / 14663 / 6464**。SQL 実行権限、特定型/拡張/psql script 等の条件が異なる。実際のアプリ DB ロールは superuser のため侵害時の影響が大きい | Compose/CI は **16.15**。本番 DB 更新は別の承認・メンテナンスが必要。plpgsql のみ、replication slot 0 を確認。[16.15 release](https://www.postgresql.org/docs/release/16.15/)、[16 系 security](https://www.postgresql.org/support/security/16/) |
| F05 管理画面の平文 HTTP | 本番 8000 / staging 8080 の公開 `/login` が HTTP 200。HTTP でセッションを使用すると経路上で盗聴・改変され得る。OAuth の Discord 側 HTTPS だけでは管理画面の通信を保護できない | **未解決**。TLS 終端、アクセス制限、正規 ADMIN_BASE_URL、Discord redirect URI を揃えて反映する必要がある。HTTPS 時 Secure Cookie を使うコード修正済み。実機 HTTP GET と [auth.py](../admin/auth.py) / [main.py](../admin/main.py) が根拠 |
| F06 DB 公開・秘密値・権限 | 両環境で **0.0.0.0 と IPv6 全体へ 5433 を publish**。POSTGRES_PASSWORD 環境変数はリポジトリの既知 default と一致。ただし保存済みロールの実パスワードは未検証。実際のアプリ DATABASE_URL で接続したロールの superuser / createdb / createrole / replication はすべて true | ソースで loopback bind、秘密値/接続先の必須指定へ変更。**稼働 DB のパスワード、権限、公開設定は未変更**。外部 TCP の単発接続は timeout だったが、全ネットワークから閉じている証明ではない。実機 docker inspect と読み取り SQL、[Compose](../docker-compose.yml) |
| F07 OS・コンテナ更新不足 | Ubuntu 20.04 の標準保守終了後で Pro 未接続。アプリ/DB/BDS の Debian パッケージにも修正待ちがある。ホストは SSH、コンテナは画像・音声等の外部入力を扱う。CVE の存在だけで公開 RCE と断定しない | アプリの apt 更新を追加。**ホスト・DB・BDS は未更新、Debian が未修正の FFmpeg/libxml2 等は残る**。全候補は os-scan.json。[Ubuntu lifecycle](https://ubuntu.com/about/release-cycle)、[Debian FFmpeg](https://security-tracker.debian.org/tracker/source-package/ffmpeg)、[OpenSSL tracker](https://security-tracker.debian.org/tracker/source-package/openssl) |
| F08 VPN / OpenVPN | Alpine 3.20 保守終了。OpenVPN 2.6.20 は **CVE-2026-13117**（動的 tls-crypt 条件）、**CVE-2026-84471 / 84732** 等より前。問題ごとに TLS 再交渉や不正 ACK 等の条件がある。確認した設定には tls-crypt/v2 がなく 13117 の当該機能条件は未充足 | Dockerfile を Alpine **3.24** に変更。公式 package は OpenVPN **2.7.7-r0**。**実接続未検証の更新候補**として扱う。旧設定の comp-lzo、cipher と 2.7 の圧縮/TLS 変更を staging で確認すること。[2.6 Changes](https://raw.githubusercontent.com/OpenVPN/openvpn/release/2.6/Changes.rst)、[2.7.7 Changes](https://raw.githubusercontent.com/OpenVPN/openvpn/v2.7.7/Changes.rst)、[Alpine support](https://alpinelinux.org/releases/) |
| F09 音声・音楽 URL / SSRF | アップロード音声を FFmpeg が playlist として解釈するとネットワークや別のローカルファイルを参照し得た。また、Discord から音楽機能を使用できる利用者の HTTP(S) URL を yt-dlp Generic / FFmpeg が処理し、私設アドレス・redirect・DNS 再解決を一貫して遮断する仕組みがない | ローカル音声は許可した format と file protocol のみ、配信入力は file protocol を禁止し実プロセス試験成功。**音楽 URL の内部ネットワーク SSRF は未解決**。URL 文字列だけの検査では十分でなく、VPN/proxy を含む接続先解決・egress 制御の設計と互換性試験が必要。[voice_audio.py](../bot/services/voice_audio.py)、[voice_music.py](../bot/services/voice_music.py) |
| F10 FFmpeg HTTPS 検証 | 稼働 Debian FFmpeg の TLS protocol は `tls_verify=0` が既定。音楽の HTTPS 接続が証明書を検証していなかったため、ネットワークを操作できる攻撃者による MITM が成立し得る | HTTPS 入力で `-tls_verify 1` を指定。自己署名 HTTPS を実 FFmpeg が拒否する試験成功。既存 HTTP 入力は維持しているため HTTP 自体の暗号化は提供しない。[FFmpeg protocol docs](https://ffmpeg.org/ffmpeg-protocols.html#tls)、[voice_music.py](../bot/services/voice_music.py) |

F02 の 22 系に該当する他の識別子は **CVE-2026-105482、106069、105481、106072、106074、106073**。同じ発表の 105483 は 24/26 系、106070/106071 は 26 系であり、22 系へ誤って適用していない。permission model / user-land snapshot の使用は確認されず、該当機能の問題と Brotli 等を分けて評価した。

F01 は「eMudhra と名の付く証明書をすべて手作業で削除する」変更ではない。ベンダーの CA 更新を使用する。certifi の初回修正は 2026.10.18、今回は影響しない root を保持する調整を含む [2026.10.19](https://github.com/certifi/python-certifi/releases/tag/2026.10.19) を採用した。Debian 13 の最低版は `20250419+deb13u1`、Alpine は `20261020-r0`。Node 内蔵ストアと pip の vendored ストアにも更新が必要なため Node/pip も更新した。

### P2・P3

| ID / 優先度 / 対象 | 内容・実際の影響条件 | 対応状況・根拠 |
| --- | --- | --- |
| F11 / P2 / legacy JSON 管理 | quotes/reactions/ng-words/kuji の計 16 route に認証・global admin 判定がなかった。有効化されて外部到達可能なら第三者による参照・変更が可能。**両実環境の機能 flag は false** のため、稼働中の認証回避 P0 として数えない | 全 route にログインと DB の global admin を要求。匿名/無効ユーザー/viewer/admin を回帰試験。[main.py](../admin/main.py)、[auth.py](../admin/auth.py) |
| F12 / P2 / CSRF | SameSite=Lax に依存し、browser の更新操作に一貫した origin 検証がなかった。GET logout も状態変更する。利用者が攻撃サイトへ誘導された場合などに影響 | 正規 ADMIN_BASE_URL と Origin/Referer を比較、Host header を信頼しない。GET logout は確認表示、実処理は POST。header secret 認証の internal API は CSRF 対象外だが body 上限は適用。[security.py](../admin/security.py) |
| F13 / P2 / セッション・OAuth | 弱い placeholder secret、OAuth state の有効期限不足、Cookie 寿命/HTTPS 属性の不足。本番で secret が存在し 32 文字以上であることは確認したが、エントロピーは未判定 | production の短い/既知 placeholder secret を拒否、state は単回使用・10 分、成功時セッション再作成、Cookie max-age 8 時間 / HttpOnly / SameSite=Lax / HTTPS 時 Secure。既存秘密値は未変更。[auth.py](../admin/auth.py) |
| F14 / P2 / アップロード・rate limit | 本文全読み込みやサイズ確認前のファイルコピーによるメモリ/ディスク消費、公開 write / OAuth start の濫用余地 | Content-Length と chunked の双方で上限、画像 8 MiB + 1 byte 読み取り、音声 64 KiB chunk と超過時 cleanup。公開 write 20/分、OAuth start 30/分の process-local IP 制限を追加。複数 worker / distributed traffic / slow connection は edge 側対策が残る。[security.py](../admin/security.py)、[audio_assets.py](../admin/audio_assets.py) |
| F15 / P2 / サプライチェーン | Python 推移依存の未固定、Deno installer の直接実行、可変 action tag、広い既定 token 権限。pip 24 / setuptools 79.0.1 にも build/archive 関連 advisory がある（詳細は python-scan.json） | direct pin + 全推移依存 hash、公式 image digest / Node checksum、action full SHA、contents:read、checkout credentials 非保持。pip **26.2.2** / setuptools **83.0.0**。apt/apk の修正は更新可能に維持。[Dockerfile](../Dockerfile)、[requirements.in](../requirements.in)、[workflow](../.github/workflows/checks.yml) |
| F16 / P2 / コンテナ権限・mount | アプリは root、staging では repo 全体が RW mount。アプリ侵害が書き換えへ波及し得る。privileged / extra caps は app でなし。VPN の NET_ADMIN/TUN は機能上必要 | app に no-new-privileges を追加。UID/GID と既存 volume 権限を無検証で変更しない。root 移行・staging mount 縮小は残る。本番は immutable image + 必要 data/images RW + secrets RO を確認。[Compose](../docker-compose.yml) |
| F17 / P2 / PyNaCl 例外 | **CVE-2025-69277 / GHSA-mrfv-m5wm-5w6w**。1.5.0 の libsodium Ed25519 point validation、修正 **1.6.2**。discord.py 2.7.1 voice は `PyNaCl>=1.5,<1.6`。当 repo / voice 暗号処理で当該 API の使用を確認せず、直接到達は未確認 | **未修正・期限を伴う追跡が必要な例外**。無理に制約を破らず 1.5.0 を固定し、CI でこの GHSA だけ除外。voice の対応版が利用可能になった時点、または当該 API 使用を追加する前に再評価。[PyNaCl 1.6.2](https://github.com/pyca/pynacl/releases/tag/1.6.2)、[discord.py metadata](https://pypi.org/pypi/discord.py/2.7.1/json) |
| F18 / P2 / Minecraft BDS | BDS 1.26.36.1、可変 latest、UDP 19132 公開、ONLINE_MODE=true / ALLOW_LIST=false / 権限 member。認証は有効だが参加者 allowlist はない。公開参加を意図している可能性があり勝手に閉じない | BDS/ワールドは未変更。更新版と client/pack 互換性、参加許可ポリシーの確認が必要。**CVE-2026-55010 を自前 BDS の未修正 RCE と断定しない**。Microsoft は hosted service で修正済み・利用者操作不要と説明。[MSRC](https://msrc.microsoft.com/update-guide/vulnerability/CVE-2026-55010)、[26.52 changelog](https://feedback.minecraft.net/hc/en-us/articles/49175370527501-Minecraft-Bedrock-Edition-26-52-Hotfix-Changelog) |
| F19 / P2 / 認証ログ | OAuth / DB 接続例外をそのままログへ出しており、失敗内容に secret/DSN が含まれる場合に漏洩する可能性。過去ログの実漏洩を確認したわけではない | 既存の共通 redact_secrets を適用、DB URL は明示的 secret として追加。履歴全ログの forensic 調査は未実施。[auth.py](../admin/auth.py) |
| F20 / P3 / internal token | 不正な非 ASCII header token が compare_digest の例外により 500 になり得た | UTF-8 bytes の定時間比較へ変更し不一致は 401。許可される正規 token は変わらない。[ai_tasks_internal.py](../admin/ai_tasks_internal.py)、[minecraft_internal.py](../admin/minecraft_internal.py)、[control API](../scripts/minecraft/minecraft_control_api.py) |
| F21 / P3 / 継続検査 | 認証・本文上限・TLS の回帰検査や lock の advisory 検査が不足 | 軽量 pip-audit と管理画面 14 / media 6 の回帰テストを既存 CI に追加。全体スキャンの外部 SaaS 送信・監視契約は追加していない。[checks.yml](../.github/workflows/checks.yml) |

### 重要な非該当・条件付き判定

- **PHP:** 10 月 21 日の公式発表は CVE-2026-109618 / 109619、修正版 8.5.12 / 8.4.27 / 8.3.36 / 8.2.35。Web request を処理する PHP が対象で、mod_php の pre-application 攻撃が実証されている。当対象では未使用のため修正対象なし。[PHP 公式発表](https://www.php.net/archive/2026.php)
- **OpenSSL:** upstream の数字だけで判断しない。本番 Debian `3.5.7-1~deb13u3` は backport 済み、staging u2 は更新対象。ホストの Ubuntu `1.1.1f-1ubuntu2.24` は Ubuntu 保守状況と合わせて判断する。[公式情報](https://openssl-library.org/news/vulnerabilities/index.html)、[Debian source tracker](https://security-tracker.debian.org/tracker/source-package/openssl)
- **aiohttp / yt-dlp:** 稼働 aiohttp 3.14.4 は 3.14.2 以下の OOB、3.14.1 以下の request smuggling 修正より新しい。yt-dlp 2026.7.4 は GHSA-6v4j-43gg-vj32 修正後。古い advisory を現版へ一律適用しない。最終 lock は pip-audit で F17 以外の検出 0。
- **OS スキャンの Critical:** libxml2 の CVE-2026-6653 は scanner Critical でも Debian は minor/no-DSA、細工した XML による UAF/DoS 条件を記載。Perl CVE-2026-13221 は巨大な特定正規表現 trie の条件があり、DB で PL/Perl は未導入。curl CVE-2026-8286 は upstream Low、STARTTLS reuse が条件で当アプリに IMAP/POP3/SMTP/FTP/LDAP 利用を確認しない。scanner 最大値をそのまま「外部から即 RCE」と報告しない。[libxml2](https://security-tracker.debian.org/tracker/CVE-2026-6653)、[Perl](https://security-tracker.debian.org/tracker/CVE-2026-13221)、[curl](https://curl.se/docs/CVE-2026-8286.html)
- **FFmpeg の残件:** Debian 7.1.5 の複数 codec/HLS advisory は未修正・deferred を含む。入力形式制限はリスク低減になるが、許可したデコーダーの脆弱性まで修正するものではない。ABI を変える非公式 binary の置換は行わなかった。[Debian tracker](https://security-tracker.debian.org/tracker/source-package/ffmpeg)

### アプリケーション・運用の確認範囲

OAuth は state、Discord identity、DB の enabled/login/global/guild 権限を確認し、DB 照合失敗時はアクセスを拒否する。session は署名付きで暗号化ではないが、OAuth access token を保存する構成ではない。global admin と guild 権限の混同は F11 で修正した。

AI Task Runner は承認済みユーザーの開発権限で動作する設計であり、AGENTS が指定する通常のローカルコマンド実行を脆弱性扱いして禁止していない。受付権限、task identity、header token、claim/lease、公開先/SHA 照合をレビュー・既存試験で確認した。API の共有 token 自体は静的で有効期限がなく、task claim の期限とは別物。期限付き token への運用移行は今回実施していない。本番 service は ubuntu、NoNewPrivileges=yes、ProtectSystem/Home なし、観測時 ActiveState=activating。安定稼働の証明は未取得。

SQL はパラメーター束縛と固定選択肢による組み立てを、外部 process は argv と既存 quote 処理を中心に確認し、今回のレビューで確証付きの SQL/command injection は追加認定しなかった。テンプレートは autoescape、既存の path 解決・archive の traversal/symlink 防止を確認した。危険なファイル入力の到達先として FFmpeg と画像処理を優先修正した。全コード経路の形式検証を意味しない。

公開 GitHub webhook / Discord HTTP interaction endpoint は確認されず、Discord gateway はライブラリが処理する。内部 bridge は header secret 認証の API で、webhook 署名欠落の指摘は当てはめない。HTTPX 等で TLS 検証を無効にする `verify=False` は確認しなかった。内部 Control API の HTTP transport は loopback/tunnel/private network の配置確認を継続すべきで、共有 token の存在だけで通信経路を安全としない。

両ホストの SSH は password/keyboard-interactive 無効、pubkey 有効、root は password 禁止。UFW は inactive だが **iptables INPUT に許可/拒否規則が存在する**。Docker の DOCKER-USER は RETURN で、publish した port の制御を INPUT の規則だけに頼れない。クラウド側 firewall/ACL は未取得。DB の SSL off は同一 Docker network 内の通信と外部接続を区別して判断する必要がある。

## C. 実施した対策と検証

### ソース変更

1. Python direct dependency を [requirements.in](../requirements.in) に固定し、Linux/Python 3.11 向けの推移依存・hash を [requirements.txt](../requirements.txt) に生成した。uv 0.13.4 を利用。Windows の隔離 Python 3.11.17 環境にも hash 検証付きでインストールし、`uv pip check` が 54 package で成功した。
2. Python/Deno/Alpine image を version+digest に固定。Node の修正版 slim tag が調査時点では未公開だったため、[install_node_runtime.sh](../scripts/install_node_runtime.sh) で公式配布物を arch 別 SHA256 照合して node 本体を導入する。既存環境の Node/Python は置き換えていない。
3. CA、pip/setuptools、Pillow、aiohttp 等を更新。Deno に `DENO_TLS_CA_STORE=system`、OS CA に最低修正版の build check を追加した。
4. 認証/CSRF/session/body 上限/ログ、FFmpeg の入力・TLS 制限を修正。[check_admin_security.py](../scripts/check_admin_security.py)、[check_media_security.py](../scripts/check_media_security.py) を追加し、既存の音楽 pipeline check も補強した。
5. Compose の DB bind を loopback へ変更し、既知 default secret/DB URL の暗黙使用を廃止。既存 `.env` や保存済み設定は編集していない。`.env.example` は rate limit の説明を追加した。
6. Actions を公式 full SHA に固定、contents:read、checkout persist-credentials:false、Node/Python 修正版と hash lock を使用。pip-audit は分離 venv で実行し、監査ツール依存がアプリ依存を上書きしないようにした。setup-node の指定はアプリ用 Node を制御する。更新した action 自体は Node 24 を使うため、GitHub runner が提供する action runtime の修正版も CI 実行時に確認する必要がある。
7. `.dockerignore` で追加の env/証明書秘密鍵/VPN 設定を build context から除外。ソースから既存機能を削除する変更、DB migration、Minecraft pack/world の変更はない。

### 自動スキャン

| 検査 | 結果・限界 |
| --- | --- |
| pip-audit 2.10.1 | 本番 58 distribution の metadata を照合: 5 package、25 raw finding、GHSA を基準に重複除去すると 17 advisory。最終 lock は **未除外 0、除外 raw 2（F17 の同一 GHSA/CVE alias）**。pip/setuptools は build tooling として別途更新 |
| Trivy 0.76.1 OS | 実機 dpkg/apk の package/source version/epoch と OS release からローカルで CycloneDX を構成して照合。DB 更新時刻 `2026-10-21T07:13:45Z`。全 target の重複除去後 package/version/CVE 候補は **1,604**。到達可能な脆弱性の件数ではない |
| Trivy config | Dockerfile 2 つで root USER が High 各 1、HEALTHCHECK 不在が Low 各 1。VPN 権限、Compose healthcheck、既存 immutable deploy healthcheck と照合。root 移行は F16 として保留 |
| Bandit | 最終 532 candidate（High 2 / Medium 86 / Low 444）。High 2 は既存 test fixture の Jinja autoescape。SQL/subprocess/assert/random 等を文脈で精査し、scanner 件数をそのまま新規問題数に数えない。532 件の全経路安全性を証明したものではない |
| Gitleaks 8.30.1 | 最終 working tree 4 candidate、Git 履歴 581 commit の 3 candidate を確認。偽のテスト秘密鍵、placeholder、隣接する空の API_KEY 行の誤一致。今回 **実秘密の混入は確認されず**。出力は redact を有効化。実運用ログ全体の漏洩調査とは別 |
| Actions / Compose | workflow の token/checkout/action pin と Compose の port/capability/mount/env を手動レビュー。Compose の複数 profile と本番 overlay の `config --quiet` 成功 |

Trivy の Windows rootfs/image 読み取りでは OS 検出できず、その空結果は採用しなかった。代わりに実機 package metadata を照合したが、third-party SBOM では scanner 固有の context が失われ得る。実 image の再ビルド後に native Linux scan を行う必要がある。

| 稼働対象 | package 数 | 重複除去した scanner ID 数 | うち修正版あり | 備考 |
| --- | ---: | ---: | ---: | --- |
| Ubuntu ホスト（各） | 680 | 0 | 0 | **EOL 判定。0 件は安全の根拠にならない** |
| 本番アプリ image | 303 | 315 | 15 | Critical 1 / High 52。vendor severity・到達条件を別評価 |
| staging アプリ image（各） | 303 | 380 | 80 | 3 image の package inventory は同一 |
| PostgreSQL image（両環境共通） | 142 | 184 | 67 | PostgreSQL 本体だけでなく OS 依存を含む |
| BDS image | 112 | 242 | 107 | OS package の結果。非公開 BDS binary の完全解析ではない |
| VPN Alpine | 37 | 0 | 0 | **EOL 判定。OpenVPN 等の公式情報で補完** |

### テスト

- **Python checker 88 本成功、Node checker 7 本成功。** Python の結果には各 suite 内の platform 条件付き skip を含む。「すべての test case が無条件に実行済み」ではない。
- 新規 regression は管理画面 **14 ケース**、media **6 ケース**。匿名/権限不足/無効ユーザー、偽造 cookie、state expiry、CSRF、GET logout、chunked/body 上限、rate limit、Unicode token、音声 4 format、偽装 playlist、超過 upload cleanup、local file protocol 拒否、自己署名 HTTPS 拒否を確認した。
- Python compileall、`git diff --check`、Node installer の shell syntax、Compose の base/profile/本番 overlay 構成が成功。
- 隔離環境で Uvicorn を loopback 起動し `/login` の 200 と security header を確認、試験 process のみ終了した。Bot は既存 offline runtime fixture による検査。実 Discord への login/投稿/voice 接続は未実施。
- **DB 統合検査も成功**: [公式 Windows binary](https://www.enterprisedb.com/download-postgresql-binaries) の PostgreSQL 16.15 を一時領域に展開し、新規 loopback 専用 cluster で全 migration と `check_minecraft_cosmetics_db.py`、`check_guild_repository.py`、`check_message_router_db.py` を実施した。本番データ/接続情報を使用せず、検査後にこの cluster のみ停止した。migration の成功は checker 88 本とは別に数えている。
- **既存 checker 3 本は Windows で失敗**: `check_ai_task_backup_split.py` と `check_ai_task_backup_restore.py` の split-producer case は Python 3.11 Windows の Path.is_mount 未対応、`check_nethernet_diagnostics.py` は POSIX 0700 の検査が Windows 0777 になる差。backup restore は PostgreSQL 16.15 付きで 50 ケース中 47 成功 / 1 error / 2 skip。検査を弱めず Linux CI に残した。
- **1 本未実施**: `check_resource_pack_versions.py` は公開後の base/head SHA が必要（判定器の単体 check は成功、pack 変更なし）。
- 既存 3 suite は Windows の環境変数全消去で SSL 初期化に失敗したため、aiohttp を先に import して再実行し成功した。詳細は tests.json。
- **Docker 実ビルド・Linux CI・実バックアップの隔離復元・VPN 再接続・Minecraft クライアント QA は未完了**。ローカル Docker daemon は停止中で、既存 container を意図せず自動起動させないため起動していない。実 image が未生成のため「更新後 OS CVE 0 件」とは報告しない。

## D. 本番反映・残件の引き継ぎ

本番反映はこの作業に含めて実行していない。以下はレビュー可能な手順であり、実施には依頼どおり別の明示的承認が必要。

1. **公開と CI:** runner が本 branch の差分を commit/PR 化し、Linux の既存 CI を完走させる。基準 SHA と他者変更を照合する。レビュー・対象 SHA が確定するまで自動 merge/deploy しない。Docker build、更新 image の OS/Python scan、4 音声形式、管理画面/両 Bot、OAuth/権限を staging で確認する。
2. **バックアップ:** [deployment-standard](operations/deployment-standard.md)、[storage-capacity](operations/storage-capacity.md)、[storage-recovery](operations/storage-recovery.md) に従い、容量・lease/lock・現在 image SHA を確認し、直前バックアップと隔離復元を検証する。本番の実際の backup root は `/home/ubuntu/ichiyon-backups`、観測時 58 entry、mode 0700、最新 entry は 10 月 18 日相当。古い repo 内 backups と混同しない。entry の存在だけで DB/asset/world の復元可能性を証明していない。
3. **アプリ:** [ai_task_deploy_remote.sh](../scripts/ai_task_deploy_remote.sh) の immutable image/事前 backup/容量 guard/health/旧 image rollback を使用する。**ADMIN_BASE_URL がブラウザーの正規 origin と一致していないと新 CSRF guard が 403 を返す**ため事前確認する。TLS 終端と Discord redirect URI を合わせて切り替える。secret を弱い値に戻して起動を通さない。
4. **DB:** 通常の app deploy は `--no-deps` で DB を更新しない。16.15 は同じ major の別 maintenance とし、既存 volume を維持する。16.15 release の extension/reindex 等の条件を照合する。アプリ用権限と migration 用権限を分離し、必要 grant の試験後に変更する。パスワード変更は保存済み DB role と全 consumer を揃えて行い、POSTGRES_PASSWORD env の変更だけで永続 DB がローテーションされたと扱わない。
5. **ネットワーク・ホスト:** 管理画面 TLS/ACL、DB loopback、Docker DOCKER-USER とクラウド firewall を一体で検証する。Ubuntu の対応版移行または Pro/ESM、Docker/SSH/host OpenSSL を保守対象に戻す。rootless/non-root 移行は volume ownership と backup/recovery を試験してから行う。
6. **VPN:** Alpine 3.24 / OpenVPN 2.7.7 は本番投入前に隔離接続確認が必要。現設定には remote-cert-tls があるが comp-lzo/cipher もある。2.7 の送信圧縮廃止・受信互換、TLS 既定変更を確認し、通信/YouTube provider/失敗時の経路を試験する。旧 image/config に戻せるようにする。秘密鍵や VPN 設定値を report/log に出さない。
7. **Minecraft:** [managed-minecraft-release](operations/managed-minecraft-release.md) に従い、BDS、pack/client、バックアップ復元を別 release として確認する。今回ワールド更新・allowlist 変更を自動実行していない。更新前に固定 version/digest と rollback 可否を決める。
8. **残るアプリ課題:** 音楽 SSRF の接続先/redirect/DNS/egress 制御、PyNaCl と discord.py voice の対応、FFmpeg/libxml2 の vendor 修正待ち、edge rate/timeout と共有 token の lifecycle を追跡する。例外を増やして scan を無条件に成功扱いしない。

`compose down`、volume 削除、prune、DB/ワールドの再初期化は本手順に含めない。アプリ rollback と DB/ワールド rollback は別の手順・データ整合性確認が必要。
