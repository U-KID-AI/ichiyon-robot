# ver2.0 デッキ検索 runtime

デッキ検索は「メンション反応 > 検索 > デッキ検索」に置く。
独立した機能一覧には出さない。
DB backend時だけ `mention_reactions.config_json.search_type = deck_search` を見て実行する。

## .env

実値やTokenはGitに入れない。

```env
X_SEARCH_ENABLED=false
X_SEARCH_MAX_RESULTS=10
X_BEARER_TOKEN=
```

- `X_SEARCH_ENABLED=false`: X APIを叩かず「デッキ検索はまだ無効」を返す
- `X_SEARCH_ENABLED=true`: `X_BEARER_TOKEN` がある時だけX APIを叩く
- `X_SEARCH_MAX_RESULTS`: X APIへ要求する件数。X API recent searchの範囲に合わせて10から100に丸める

## 管理画面設定

検索ロジック自体は管理画面から編集しない。
編集できるのは設定だけ。

```json
{
  "search_type": "deck_search",
  "allowed_channel_ids": ["123", "456"],
  "max_results": 3,
  "deny_message": "このチャンネルではデッキ検索は使えません。",
  "missing_format_behavior": "ask_format",
  "x_query_template": "({class_label} OR {class_en}) (デッキ OR deck OR QR OR コード) has:images",
  "include_retweets": false,
  "include_replies": false,
  "image_scan_limit": 8,
  "request_timeout_seconds": 10,
  "cache_ttl_seconds": 60,
  "result_format": "default",
  "class_filter_required": true
}
```

## 呼び出し

```text
@いちよんロボ デッキ エルフ
@いちよんロボ デッキ ロイヤル
@いちよんロボ デッキ ウィッチ
@いちよんロボ デッキ ドラゴン
@いちよんロボ デッキ ナイトメア
@いちよんロボ デッキ ビショップ
@いちよんロボ デッキ ネメシス
@いちよんロボ デッキ ニュートラル
```

日本語、英語、短い略称を吸収する。

## X API

Recent searchを使う。
公式docsでは `GET https://api.x.com/2/tweets/search/recent`、`query`、`max_results`、`tweet.fields`、`expansions=attachments.media_keys`、`media.fields=url,preview_image_url,type,width,height` を使う形。

Tokenはログに出さない。
API失敗、レート制限、タイムアウトではBotを落とさず「検索でエラー」を返す。

## 画像とQR

- 画像付きポストを優先
- 画像レスポンス以外はスキップ
- サイズ上限を超える画像はスキップ
- OpenCVの `cv2.QRCodeDetector` でQRを検出
- OpenCVがない場合はBot全体を落とさず「画像判定が使えません」を返す
- QR文字列は通常ログへ出さない

## 返答

- 結果なし: `見つからなかった`
- チャンネル不許可: `deny_message`
- クラス名不足: `クラス名も入れて`
- API無効: `デッキ検索はまだ無効`
- エラー: `検索でエラー`

結果には本文概要、投稿URL、検出クラス、QR検出済みを含める。

## キャッシュ

`guild_id + channel_id + query` で短時間メモリキャッシュする。
Bot再起動で消えてよい。

## ローカル確認

X APIを叩かないチェック:

```powershell
python -m compileall bot admin scripts
python scripts/check_deck_search_runtime.py
```

stg確認はまだ行わない。
