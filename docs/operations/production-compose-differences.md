# 本番 docker-compose.yml 差分棚卸し

確認日: 2026-07-30

## 確認方法

本番 `/home/ubuntu/ichiyon-robot` で読み取り専用の範囲だけ確認した。

- `git status --short`
- `git diff --stat -- docker-compose.yml`
- `git stash list`
- `git stash show --stat`
- `docker compose config --services`
- `docker compose ps`

`.env`、Cookie、OpenVPN設定、Token、秘密鍵、secrets の中身は確認・記録していない。

## 現在の状態

- 本番HEAD: `7d139a95c7ddbe77257f77846355d4148b9c00c7`
- tracked差分: `docker-compose.yml` のみ
- 未追跡ファイル: あり
- stash: 7件
- 稼働中: `admin`, `bot`, `bot-irsia`, `db`, `youtube-vpn-proxy`
- `voicevox-engine`: 通常運用では起動しない前提

## 差分分類

### mainへ取り込むべき共通修正

- VOICEVOXを通常起動から分離し、明示profileでだけ起動する構成
- VOICEVOX healthcheckを内部URL `/version` で確認する構成
- bot / bot-irsia がTTS停止中でも `voicevox-engine` へ強制依存しない構成

現行mainでは、上記はすでに `voicevox` profile として反映済み。

### production固有の正当な差分

- 本番専用のservice起動profile運用
- 本番専用container名またはport割り当て
- `youtube-vpn-proxy` のOpenVPN関連mount
- 本番volume、network、restart policy、healthcheckの運用差分
- 本番に残す未追跡backupやアップロード済み画像

これらはmainへ無条件に取り込まない。

### 過去対応の残骸候補

- 複数の `docker-compose.yml.bak-*`
- 過去のSpotify/TTS作業前backup
- `.venv/`

今回削除しない。整理する場合は、別作業で中身を確認し、秘密値や復旧に必要なものがないことを確認してから行う。

### 不明で触らないもの

- 本番stash 7件のうち、過去データやCSSを含むもの
- 未追跡画像・backup類

## stash分類

- `stash@{0}`〜`stash@{4}`: production compose差分または過去デプロイ作業の退避と推定
- `stash@{5}`: CSSとJSONデータを含むため、過去データ移行または未完了作業の可能性
- `stash@{6}`: v1時代のdata backup

stashは削除・適用・popしていない。

## 運用ルール

- 本番 `docker-compose.yml` は無条件上書きしない。
- main更新時は、更新前HEAD版、本番ローカル版、origin/main版の三者を確認する。
- `docker compose down` とvolume削除は禁止。
- 通常recreate対象は `bot`, `bot-irsia`。admin変更時のみ `admin` を含める。
- `db`, `youtube-vpn-proxy`, `voicevox-engine` は必要時以外recreateしない。
