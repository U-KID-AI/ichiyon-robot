# ver2.0 自動投稿 Bot実行側

DB backend時だけ、DBの `auto_posts` をBot実行側で読む。
`ICHIYON_DATA_BACKEND=json` または未設定では、既存のJSON側6/30投稿を使う。

## 実行条件

- Bot起動時に `db_auto_post_task` を開始
- 1分ごとに有効な `auto_posts` を確認
- `feature_flags.auto_posts` がOFFのguildは実行しない
- 日付と時刻はJST基準

## 対応スケジュール

- `yearly`: 毎年指定月日
- `monthly`: 毎月指定日
- `weekly`: 毎週指定曜日
- `daily`: 毎日
- `once`: 指定月日の一回投稿扱い

管理画面の `schedule_value` に保存されたJSONを読む。
例:

```json
{"type": "yearly", "month": 6, "day": 30, "time": "09:00", "timezone": "Asia/Tokyo"}
```

## 6/30投稿

6/30の「サ終やめませんか？」は、DBでは自動投稿1件として作る。

- 投稿名: `6/30 サ終やめませんか？`
- 本文: `サ終やめませんか？`
- 種類: `毎年`
- 月日: `6月30日`
- 時刻: 任意
- タイムゾーン: `Asia/Tokyo`
- 投稿先チャンネルID: guildごとに設定

## 二重投稿防止

`auto_post_delivery_history` に投稿済み履歴を保存する。

- `auto_post_id + due_key` で一意
- Bot再起動後も同じ `due_key` は投稿しない
- 投稿成功後に `last_posted_at` も更新

`due_key` の例:

- `yearly:2026-06-30`
- `monthly:2026-06-20`
- `daily:2026-06-20`

## エラー時

- 投稿先チャンネルID未設定ならログを出してスキップ
- チャンネルが見つからない場合もログを出してスキップ
- 送信失敗時はBot全体を落とさない
- 送信成功前は履歴を記録しない

## stg確認手順

1. `ICHIYON_DATA_BACKEND=db` を設定
2. stg用DBでmigrationを実行
3. 管理画面で対象guildの自動投稿をON
4. `6/30 サ終やめませんか？` を作成
5. 投稿先チャンネルIDと時刻をstg確認用に設定
6. Botを起動
7. 指定時刻に投稿されることを確認
8. Botを再起動し、同じ日・同じ予定キーで再投稿されないことを確認

DB移行リハーサルは別作業で行う。
