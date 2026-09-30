# Minecraft Bedrock Dedicated Server 運用メモ

## 2026-09-30 現行実装

残件のsource of truthは[CURRENT_BACKLOG](../CURRENT_BACKLOG.md)。以下のホスト/構成の棚卸しはhistoricalで、現在の稼働状態や残タスク判定には使わない。Discord連携は`bot/services/minecraft_control.py`と`bot/services/minecraft_bridge.py`、`scripts/minecraft/minecraft_control_api.py`で実装済み。managed cosmetics配備と状態取得は実装済みで、実機QA・本番状態の確認とは区別する。

この文書は、`141.147.145.113` 上の Minecraft Bedrock Dedicated Server を、既存ワールドを壊さずに共同建築向けへ安定化するための棚卸しと手順です。

## 現在確認した構成

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

## Discord 連携（実装済み）

Botからは認証されたControl APIを呼び、Docker操作はホスト側APIが担当する。`/status`、`/restart`、managed cosmetics applyとNarita Bridgeは実装済み。バックアップは`/restart`内と運用スクリプトで作成し、独立した`/backup` endpointはない。今回の`/status`はloopback probeからversion/人数を取得し、外部直接IP・フレンド接続の成功は`not_tested`として区別する。終了コード0の応答判定は従来通り維持し、未知の出力形式ではversion/人数だけを不明にする。読み取り専用`/diagnostics`の設定と配備前確認は[MINECRAFT_DIAGNOSTICS](../MINECRAFT_DIAGNOSTICS.md)を参照。フレンド接続の障害調査と実機QAは[NetherNet診断](nethernet-diagnostics.md)へ分離する。
