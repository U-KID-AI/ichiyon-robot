# ver2.0 デッキ検索 runtime

デッキ検索は「メンション反応 > 検索 > デッキ検索」に置く固定機能。
DB backend時だけ `mention_reactions.config_json.search_type = deck_search` を見て実行する。

## .env

TokenはGitに入れない。

```env
X_SEARCH_ENABLED=false
X_SEARCH_MAX_RESULTS=10
X_SEARCH_MODE=recent
X_SEARCH_LOOKBACK_DAYS=14
X_BEARER_TOKEN=
```

- `X_SEARCH_ENABLED=false`: X APIを叩かず「デッキ検索はまだ無効」を返す。
- `X_SEARCH_ENABLED=true`: `X_BEARER_TOKEN` がある時だけX APIを叩く。
- `X_SEARCH_MODE`: `recent` または `full_archive`。
- `X_SEARCH_LOOKBACK_DAYS`: Full-Archiveの検索対象日数。stg推奨は14。

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
  "result_format": "default",
  "class_filter_required": true
}
```

設定の意味:

- `max_results`: Discordに返す候補数。
- `x_search_max_results`: Xから取得する投稿数。最大100。
- `image_scan_limit`: QR判定する画像数。
- `image_scan_concurrency`: 画像DL/QR判定の同時確認数。1から10。
- `stop_after_candidates`: 候補が揃ったら残りの画像確認を止める。
- `image_fetch_timeout_seconds`: 1画像ごとの取得待ち秒数。
- `high_accuracy_enabled`: `高精度` 指定を使う。
- `high_accuracy_x_search_max_results`: 高精度時にXから取得する投稿数。デフォルト100。
- `high_accuracy_image_scan_limit`: 高精度時に確認する画像数。デフォルト100。
- `high_accuracy_image_scan_concurrency`: 高精度時の同時確認数。デフォルト2。
- `high_accuracy_stop_after_candidates`: 高精度時も候補が揃ったら止めるか。デフォルトfalse。
- `request_timeout_seconds`: X APIへのリクエスト待ち秒数。
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

`sort_order` は付けない。

Full-Archive時は `end_time = now - 5 minutes`、`start_time = end_time - lookback_days`。
X側の取り込み遅延で現在時刻ギリギリの `end_time` が400になるのを避ける。

TokenとQR文字列はログに出さない。

## 高速化

今回の高速化:

- Xから取得する投稿数は最大100のまま維持。
- X検索結果を本文スコアで並べ替えて、デッキらしい投稿の画像から確認。
- 画像DL/QR判定を `image_scan_concurrency` で並列化。
- `stop_after_candidates=true` の時、候補が揃ったら残りの画像確認をキャンセル。
- 画像取得は `image_fetch_timeout_seconds` で短めに切る。
- キャッシュは維持。

## 高精度

ユーザーが `高精度` を付けた時だけ、画像確認を多めにする。

例:

- `@Bot デッキ エルフ`
- `@Bot デッキ エルフ 高精度`
- `@Bot デッキ 高精度 エルフ`

`高精度` はクラス名解析から除外する。`デッキ 高精度` のようにクラス名がない場合は通常通り `クラス名も入れて` を返す。

高精度時のデフォルト:

- `stop_after_candidates=false`
- `image_scan_limit=100`
- `image_scan_concurrency=2`
- `x_search_max_results=100`

ログには `high_accuracy=True` / `precision_mode=True` を出す。TokenとQR文字列は出さない。

スコアは画像確認順を変えるだけ。低スコア投稿も完全除外しない。

処理時間ログ:

```text
deck search stats: mode=full_archive, endpoint=full_archive, lookback_days=14, total_ms=..., x_api_ms=..., image_scan_ms=..., x_search_max_results=50, image_scan_limit=30, image_scan_concurrency=2, stop_after_candidates=True, stopped_after_candidates=True, X results=..., media=..., downloaded=..., qr=..., candidates=...
```

## 返答

- 結果あり: 既存形式で返す。
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

## stg管理画面 systemd

本番の `ichiyon-admin` は触らない。stgは `ichiyon-admin-stg` として分離。

`/etc/systemd/system/ichiyon-admin-stg.service` 例:

```ini
[Unit]
Description=Ichiyon Robot Admin STG
After=network.target

[Service]
Type=simple
WorkingDirectory=/home/ubuntu/ichiyon-robot
EnvironmentFile=/home/ubuntu/ichiyon-robot/.env
Environment=PYTHONUNBUFFERED=1
ExecStart=/home/ubuntu/ichiyon-robot/.venv/bin/python -m uvicorn admin.main:app --host 0.0.0.0 --port 8080
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

反映:

```bash
sudo systemctl daemon-reload
sudo systemctl enable ichiyon-admin-stg
sudo systemctl restart ichiyon-admin-stg
sudo systemctl status ichiyon-admin-stg --no-pager
journalctl -u ichiyon-admin-stg -f
```

## ローカル確認

```powershell
python -m compileall bot admin scripts
python scripts/check_deck_search_runtime.py
```

## 後続issue

さらにやるなら別issueで扱う。

- 検索結果のスコア順ロジック調整
- `stop_after_candidates` の運用値調整
- 処理時間ログを管理画面やメトリクスへ出す
