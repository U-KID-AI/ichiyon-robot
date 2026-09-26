# Minecraft Bedrock Dedicated Server 運用メモ

この文書は、`141.147.145.113` 上の Minecraft Bedrock Dedicated Server を、既存ワールドを壊さずに共同建築向けへ安定化するための棚卸しと手順です。

## 現在確認した構成

以下は過去の棚卸し記録であり、現在の稼働状態・バージョンの証拠ではありません。

- ホスト: `ichiyon-robot-stg`
- OS: Ubuntu 20.04
- CPU: 2 vCPU 相当
- メモリ: 約 1 GiB、swap 約 2 GiB
- Docker Compose project: `/home/ubuntu/minecraft-bedrock`
- Compose service: `bedrock`
- Container: `minecraft-bedrock-stg`
- Image: `itzg/minecraft-bedrock-server:latest`
- Image arch: `amd64`
- Port: UDP `19132`
- Restart policy: `unless-stopped`
- Health: `healthy`
- World: `ichiyon-lab-stg`
- World path: `/home/ubuntu/minecraft-bedrock/data/worlds/ichiyon-lab-stg`
- World size: 約 180 KiB
- LevelDB: `data/worlds/ichiyon-lab-stg/db`

## 不具合の主因

ブロック設置・破壊ができない、または結果が反映されないように見える主因は、現在の設定が共同建築用クリエイティブではなく survival 寄りになっていたことです。

確認値:

- `gamemode=survival`
- `force-gamemode=false`
- `difficulty=normal`
- `allow-cheats=false`
- `default-player-permission-level=member`
- `view-distance=16`
- `tick-distance=4`
- `online-mode=true`
- `allow-list=false`
- `permissions.json` は空

リソース面では、調査時点で CPU、メモリ、ディスク、inode、restart count、health に明確な異常はありませんでした。`view-distance` は既に 16 で、当初懸念されていた 32 ではありません。

## 反映する安全設定

既存ワールド `ichiyon-lab-stg` と `level-name` は維持します。

推奨設定:

- `GAMEMODE=creative`
- `FORCE_GAMEMODE=true`
- `DIFFICULTY=peaceful`
- `ALLOW_CHEATS=true`
- `MAX_PLAYERS=10`
- `IMMUTABLE_WORLD=false`
- `ONLINE_MODE=true`
- `DEFAULT_PLAYER_PERMISSION_LEVEL=member`
- `VIEW_DISTANCE=16`
- `TICK_DISTANCE=4`

`spawn-protection` は現在の `server.properties` には明示されていません。スポーン地点だけ設置・破壊できない実症状が残る場合に限り、`SPAWN_PROTECTION=0` を追加します。

## 禁止事項

- 既存ワールド削除
- `level-name` の無確認変更
- 別ワールドへの切替
- `worlds` ディレクトリ削除
- LevelDB ファイルの直接編集
- `online-mode=false`
- `docker compose down`
- Docker volume 削除
- bot/admin/db の stop/recreate

## バックアップ

稼働中 LevelDB の単純 tar は整合性を保証できません。バックアップ時は Minecraft service だけを通常停止し、停止後に `data` と Compose 設定を保存してから同じ service だけを起動します。

Windows からの実行例:

```powershell
.\scripts\minecraft\backup_bedrock.ps1 -HostName 141.147.145.113 -KeyPath "C:\Users\syoub\.ssh\ssh-key-2026-06-20.key"
.\scripts\minecraft\backup_bedrock.ps1 -HostName 141.147.145.113 -KeyPath "C:\Users\syoub\.ssh\ssh-key-2026-06-20.key" -Apply
```

`-Apply` なしは dry-run です。

## 設定反映

既存 world を維持し、Minecraft service だけを対象にします。

```powershell
.\scripts\minecraft\apply_bedrock_creative_settings.ps1 -HostName 141.147.145.113 -KeyPath "C:\Users\syoub\.ssh\ssh-key-2026-06-20.key"
.\scripts\minecraft\apply_bedrock_creative_settings.ps1 -HostName 141.147.145.113 -KeyPath "C:\Users\syoub\.ssh\ssh-key-2026-06-20.key" -Apply
```

`-Apply` 実行時の流れ:

1. `/home/ubuntu/minecraft-bedrock/docker-compose.yml` と `data/server.properties` を timestamp 付きでバックアップ
2. Minecraft service `bedrock` だけを停止
3. 停止後に world と設定を tar backup
4. Compose の bedrock environment を共同建築向けへ更新
5. `docker compose up -d bedrock`
6. health、restart count、UDP listen、world path を確認

## 復元

復元は world の差し替えを伴うため、既定は必ず dry-run です。実行には `-Apply` と `-ConfirmWorldReplace` の両方を要求します。

```powershell
.\scripts\minecraft\restore_bedrock.ps1 -HostName 141.147.145.113 -KeyPath "C:\Users\syoub\.ssh\ssh-key-2026-06-20.key" -BackupPath /home/ubuntu/minecraft-bedrock/backups/bedrock-backup-YYYYmmdd-HHMMSS.tar.gz
```

## スーパーフラット移行準備

現在の world が superflat でない場合でも、この作業では既存 world を切り替えません。

候補:

- 新 world name: `ichiyon-creative-flat`
- `LEVEL_TYPE=FLAT`
- `GAMEMODE=creative`
- `FORCE_GAMEMODE=true`
- `DIFFICULTY=peaceful`

移行する場合は、事前に次をユーザーが確認します。

1. 現行 world のバックアップ
2. 新 world の保存先容量
3. ロールバック手順
4. 停止時間
5. 実クライアントで建築物が残ること

## Discord 連携の将来案

Bot から Docker socket を直接操作させる設計は避けます。将来実装する場合は、許可された Minecraft 操作だけを受け付ける小さな管理サービス、または sudo command allow-list 方式で、状態表示・オンライン人数・バックアップ・起動/停止/再起動通知を実装します。

## フレンド経由接続失敗の切り分け（2026-09-21）

Android 1.26.51 / Pixel 9a / Wi-Fi での `InitialConnection-I15`、
`NetherNet:2193`、world `ichiyon-creative-flat` の報告について、原因は未確定。
本調査では本番・stagingへ接続していない。MCXboxBroadcastの配備定義・実装は
このリポジトリ内に見つからず、稼働buildを決めつけて変更しない。

上流の [Build 154の変更](https://github.com/MCXboxBroadcast/Broadcaster/commit/5dc1f86)
には、26.50対応としてcodecのprotocolVersionを2193にする変更が含まれる。
[Build 155](https://github.com/MCXboxBroadcast/Broadcaster/releases/tag/155)
にはlibdatachannelへの移行が含まれる。これらは稼働buildやAndroid 1.26.51との
実接続成功を証明しない。古いbuildが稼働していれば互換性調査の候補になるが、
エラー番号だけで原因や更新先を確定しない。

Control APIの `bds.version` はコンテナ内loopbackに対するmc-monitorの
成功応答から取得する。設定値 `VERSION=LATEST` や残存バイナリ名からの推測は廃止。
[上流出力形式](https://github.com/itzg/mc-monitor/blob/master/bedrock_status.go)
と一致しない応答・失敗・停止時はnullとする。これはサーバーが広告するversionであり、
インストール済みartifactのbuild番号やNetherNet protocolの証明ではない。
`bridge.probe_scope=container_loopback` と `connectivity` で確認範囲を示す。
`server_status=ONLINE` やhealth成功だけで、外部ゲームポート、直接IPログイン、
フレンド経由接続の成功と解釈しない。後二者は `not_tested` のままとする。
生のstdout/stderrは状態レスポンスへ返さない。

人間の運用担当者が次の証拠を同じ再現時刻に揃える必要がある。認証情報や完全な
Docker inspect、signaling payload、候補IP一覧をタスクへ貼り付けず、非秘密の要約にする。

| 確認対象 | 必要な証拠・判定限界 |
| --- | --- |
| BDS実バージョンと更新 | 現在の起動ログのversion、直近再起動の前後のversion・時刻、ダウンロード記録。コンテナimageタグだけではBDS更新を確定しない |
| MCXboxBroadcast | 起動ログのversion/buildと配備artifact識別情報を上流commitと照合 |
| 接続ログ | 同一試行のCONNECTREQUEST / CONNECTRESPONSE / CANDIDATEADDの有無・順序、ICE状態、signalingエラー種別と時刻。行の存在だけで成功と判断しない |
| コンテナ・BDSログ | 両コンテナのstate/health/restart count/起動時刻、BDSの同時刻の接続・切断・エラー要約 |
| ゲームポート | 管理対象の実ポートへの外部UDP/RakNet応答。TCP疎通や内部loopback成功で代替しない |
| 直接IP接続 | 同じPixel・同じWi-Fi・同じゲームversionで対象worldへのログイン成功/失敗を実測。未実施なら不明 |
| フレンド経由 | 直接IPと同条件で比較。直接IP成功かつフレンド失敗ならbroadcast/転送経路を優先調査 |

現在の結論は `needs_human`。この診断修正は接続障害そのものの復旧を意味しない。
Runnerによる本番確認、更新、再起動、world操作は行わない。BDS変更・ダウングレードは
既存worldの互換性と検証済み復旧手段が未確認のため実施しない。
ローカル回帰テストは `python3 scripts/check_minecraft_control_status.py`。
FastAPIやprocess設定を読み込まず、実装から抽出した状態取得関数に合成応答を与える。
回帰テスト7件、変更したPythonファイルの `py_compile`、`git diff --check` は成功。
保護対象の `.github/workflows/checks.yml` は変更しない。この回帰テストは既存CIの
実行対象には追加しておらず、CIでの実行確認は未実施。PR・merge・deploy・再起動も
このRunnerの許可範囲外のため未実施。
