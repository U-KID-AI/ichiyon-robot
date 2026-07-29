# 外部HTTP Provider方針

## 共通方針

- connect timeout、read timeout、total相当のtimeoutをProviderごとに明示する。
- retryは429と5xx、timeout、transport errorに限定する。
- `Retry-After` がある場合は尊重する。
- backoffは指数的に増やし、最大待機を制限する。
- User-Agentを明示する。
- 環境proxyを使うかどうかをProviderごとに明示する。
- Token、Cookie、署名付きURL、Authorization headerはログに出さない。
- 例外をDiscordへそのまま表示しない。
- cancellationを妨げる長時間同期処理は避ける。
- cache hook、rate-limit hookはProvider単位で追加する。

## 現在の共通基盤

`bot/services/external_http.py` に薄い共通policyを追加した。

- `ExternalHttpPolicy`
- `fetch_json`
- `retry-after` / 429 / 5xx retry
- `trust_env` の明示
- URLログ用のquery除去 helper

## 適用済みProvider

- JMA天気取得: `bot/services/jma_weather.py`

JMAはpublic JSON APIで、TokenやCookieを使わないため、最初の移行対象として低リスク。

## 今回移行しないProvider

- YouTube / yt-dlp: Cookie fallback、home VPN proxy、EJS challenge、FFmpeg連携があるため現状維持。
- Spotify公開ページ: HTML解析・キャッシュ・候補探索への影響が大きいため現状維持。
- X検索: API token、rate limit、既存の検索モード分岐があるため現状維持。

## 次回以降の候補

1. Spotify公開ページ取得を共通policyへ移行する。
2. X検索の429 / timeout分類を共通policyと揃える。
3. YouTube抽出はyt-dlp option生成とstage timingを優先し、HTTP client共通化の対象外にする。
