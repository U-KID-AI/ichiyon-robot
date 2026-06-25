# ver2.0 デッキ検索 runtime

デッキ検索は「メンション反応 > 検索 > デッキ検索」に置く固定機能。
DB backend時だけ `mention_reactions.config_json.search_type = deck_search` を見て動く。

## .env

TokenはGitに入れない。

```env
X_SEARCH_ENABLED=false
X_SEARCH_MAX_RESULTS=10
X_SEARCH_MODE=recent
X_SEARCH_LOOKBACK_DAYS=14
X_BEARER_TOKEN=
```

- `X_SEARCH_ENABLED=false`: X APIを呼ばず、`デッキ検索はまだ無効` を返す。
- `X_SEARCH_MODE`: `recent` または `full_archive`。
- `X_SEARCH_LOOKBACK_DAYS`: Full-Archiveの検索日数。stg推奨は14。

## 設定例

```json
{
  "search_type": "deck_search",
  "allowed_channel_ids": ["123", "456"],
  "max_results": 3,
  "x_search_max_results": 50,
  "deny_message": "このチャンネルではデッキ検索は使えません。",
  "not_found_message": "おい ないんだが",
  "missing_format_behavior": "ask_format",
  "x_query_template": "{class_search_query} {required_context_query} has:media",
  "required_context_terms": ["ビヨンド", "beyond"],
  "search_mode": "full_archive",
  "lookback_days": 14,
  "excluded_keywords": ["ドラゴンボール", "レジェンズ", "探索コード", "フレンドコード"],
  "include_retweets": false,
  "include_replies": false,
  "image_scan_limit": 30,
  "image_scan_concurrency": 2,
  "stop_after_candidates": true,
  "image_fetch_timeout_seconds": 5,
  "high_accuracy_enabled": true,
  "high_accuracy_x_search_max_results": 100,
  "high_accuracy_image_scan_limit": 100,
  "high_accuracy_image_scan_concurrency": 2,
  "high_accuracy_stop_after_candidates": false,
  "request_timeout_seconds": 10,
  "cache_ttl_seconds": 300,
  "class_filter_required": true
}
```

## 設定の意味

- `max_results`: Discordに返す候補数。
- `x_search_max_results`: 通常検索でXから取る投稿数。既定50、最大100。
- `image_scan_limit`: 通常検索でQR判定する画像数。既定30。
- `image_scan_concurrency`: 通常検索の同時確認数。既定2。
- `stop_after_candidates`: 候補が揃ったら残りの画像確認を止める。通常はtrue。
- `image_fetch_timeout_seconds`: 1画像ごとの取得待ち秒数。
- `high_accuracy_x_search_max_results`: 高精度時のX取得数。既定100。
- `high_accuracy_image_scan_limit`: 高精度時の画像確認数。既定100。
- `high_accuracy_image_scan_concurrency`: 高精度時の同時確認数。既定2。
- `high_accuracy_stop_after_candidates`: 高精度時も候補が揃ったら止めるか。既定false。
- `cache_ttl_seconds`: 同じ検索を再実行しない秒数。

## X API

- Recent Search: `/2/tweets/search/recent`
- Full-Archive Search: `/2/tweets/search/all`

送るパラメータ:

- `query`
- `max_results`
- `tweet.fields=created_at`
- `expansions=attachments.media_keys`
- `media.fields=url,preview_image_url,type`
- Full-Archive時のみ `start_time` / `end_time`

`sort_order` は送らない。

Full-Archive時は `end_time = now - 5 minutes`、`start_time = end_time - lookback_days`。
TokenとQR文字列はログに出さない。

## 検索語

本文必須語はクラス語と `ビヨンド / beyond`。`デッキ / QR / コード / レシピ / 構築` は必須にしない。
デッキ判定は画像のQR検出で行う。

未知語は追加検索語としてAND条件へ入れる。

例:

- `デッキ ビショップ アンリミテッド ロデオ`: `アンリミテッド` と `ロデオ` を検索語へ入れる。
- `デッキ エルフ リノ セッカ`: `リノ` と `セッカ` を検索語へ入れる。
- `デッキ 高精度 ロイヤル 連携`: `連携` を検索語へ入れる。`高精度` は入れない。

`ネメ` はネメシスの略。ナイトメアには含めない。

## 高精度

ユーザーが `高精度` を付けた時だけ重めの設定を使う。

例:

- `@Bot デッキ エルフ`
- `@Bot デッキ エルフ 高精度`
- `@Bot デッキ 高精度 エルフ`

高精度時:

- `x_search_max_results=100`
- `image_scan_limit=100`
- `image_scan_concurrency=2`
- `stop_after_candidates=false`

通常検索は軽量設定のまま。

## 安全化

- 画像取得にタイムアウトを入れる。
- 画像サイズ上限を超えるものはスキップ。
- OpenCVへ渡す前に画像を縮小。
- QR判定の例外は握りつぶし、検索全体は落とさない。
- 同じ画像URLの判定結果は短時間キャッシュ。
- `debug_tweet_id` は廃止。通常検索に特定tweet調査処理を混ぜない。

ログには件数と時間だけ出す。

```text
deck search stats: mode=full_archive, endpoint=full_archive, lookback_days=14, total_ms=..., x_api_ms=..., image_scan_ms=..., image_scan_concurrency=2, stopped_after_candidates=True, X results=..., media=..., downloaded=..., qr=..., candidates=...
```

## 返答

- 結果あり: 候補一覧。
- 結果なし: `おい ないんだが`
- Full-Archive権限なし: `過去検索が使えません`
- `X_SEARCH_ENABLED=false`: `デッキ検索はまだ無効`
- その他エラー: `検索でエラー`

## stg反映SQL

```sql
UPDATE mention_reactions
SET config_json = config_json
  || '{"search_mode":"full_archive","lookback_days":14,"max_results":3,"x_search_max_results":50,"image_scan_limit":30,"image_scan_concurrency":2,"stop_after_candidates":true,"image_fetch_timeout_seconds":5,"high_accuracy_enabled":true,"high_accuracy_x_search_max_results":100,"high_accuracy_image_scan_limit":100,"high_accuracy_image_scan_concurrency":2,"high_accuracy_stop_after_candidates":false,"cache_ttl_seconds":300,"not_found_message":"おい ないんだが","excluded_keywords":["ドラゴンボール","レジェンズ","探索コード","フレンドコード"]}'::jsonb
WHERE reaction_key = 'deck_search'
  AND reaction_kind = 'search';
```

## 確認

```powershell
python -m compileall bot admin scripts
python scripts/check_deck_search_runtime.py
```
