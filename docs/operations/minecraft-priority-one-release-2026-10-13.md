# 優先度① 3機能の本番反映記録（2026-10-13）

対象は `MC-B01`（モクローのリード）、`MC-M07`（モルカーの浮き輪・水上走行）、
`MC-M03`（モクローの羽ばたき低速落下・落下無傷）。

## マージ・配備の証跡

- [PR #130](https://github.com/U-KID-AI/ichiyon-robot/pull/130) を2026-10-13にmainへマージ。
- マージSHA・適用SHA：`e7288a2a07ab19791de4c8b6bd2b454f309e78d2`。
- [PR CI](https://github.com/U-KID-AI/ichiyon-robot/actions/runs/38343337363)と
  [マージ後main CI](https://github.com/U-KID-AI/ichiyon-robot/actions/runs/38348230149)は
  `minecraft-cosmetics` / `python-and-compose` とも成功。成功済み検査の再実行はしていない。
- 既存Linux Runnerの[managed release](managed-minecraft-release.md)が配備。
  観測側から追加のrelease POST、helper起動、再起動、再試行を発行していない。
- operation：`83335c3b-9c54-546c-bf86-e36c58b05f3f`。
  2026-10-13 **14:00:33 JST**（05:00:33 UTC）に `succeeded`、`installed=true`。
- exact-SHAのoperation状態GETで、生成catalog、active catalog、現在DBのdigestが
  `d3a06b64e1c6a78e46226d3e932831ace11f05ae854332b207d975acc59f8698` に一致。
  これは配備前に読んだactive digestとも一致し、ID・素材blob・metadataを含む既存素材の保持を確認した。
- 稼働アプリのREVISIONとAdmin/Bot 2体のimage revisionも適用SHAと一致。

## 観測したパック版

各managed packのmanifest header・全module・world参照のバージョン一致を確認。
authoringの固定版ではなく、本番applyが選択した実バージョンである。

| パック | 本番バージョン |
|---|---|
| `ichiyon_avatar_bp` / `import_structures` | **1.1.216**（両方） |
| `ichiyon_core_rp` | **1.0.6** |
| `ichiyon_accessories_rp` | **1.0.2** |
| `ichiyon_mannequin_skins_rp` | 1.0.7 |
| `ichiyon_posters_rp` | 1.0.9 |
| `ichiyon_video_rp` / `ichiyon_records_rp` / `ichiyon_video_akki_rp` | 1.0.1 |
| `ichiyon_video_big_rp` | 1.0.2 |
| `ichiyon_aquarium_glass_rp` | 1.2.1 |

world参照はBP 3件/RP 9件。既存Rhodes建築用BP 1.0.0の参照も保持。
既存ワールドのlevel.datとDB directoryの存在、server.propertiesとpermissions.jsonの
配備前後のhash一致を確認。LevelDBの直接編集、固定Git packでの上書き、設定/ポート変更、
バックアップ削除は行っていない。個々の建築の描画を実クライアントで巡回したわけではない。

## 稼働状態・ログ・確認範囲

- BDS **1.26.52.3**。コンテナ `running / healthy`、restart count **0**。
  05:00:14 UTCの起動後、30分以上経過した照会でもONLINEとBedrock応答を確認。
- 起動ログにAvatar BP/Bridge 1.1.216のPack Stack、Mokuro/MolcarBoost/main.jsのロードを確認。
  起動後ログに新機能由来のScript例外・JSONロード失敗・watchdog停止は見つからなかった。
- **既存のNetherNet設定エラーは継続**。`connection type is not set to NetherNet` は
  配備前02:09 UTCと配備後05:00 UTCの両方に存在し、今回の3機能で新規発生したものではない。
  設定は変更していない。UDP応答や健康状態を、Switch/スマートフォンの外部接続成功の証明にはしない。
- 既存ワールドバックアップのアーカイブ可読性、素材DBを含むアプリバックアップのchecksumと
  pg_restoreによるダンプ可読性を配備前に確認。今回のSHA用バックアップのREADYも確認。
- 以前の隔離BDS 48項目と自動回帰テストの結果は[実装記録](../minecraft-priority-one.md)を参照。
  今回の本番確認と混同せず、実プレイヤーの騎乗・加速・滑空を本番で操作済みとは扱わない。

残作業は[現行台帳](../tasks/ichiyon-lab-impact-effort-2026-10-12.md)の実機QA。
手持ち/フェンスのリード描画、浮き輪と既存装備の表示、操作感・端末負荷、落下と着地、
従来滑空、流水/岸、複数クライアント同期、チャンク出入り・再ログインを確認する。
