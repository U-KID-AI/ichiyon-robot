# サービス別環境変数マトリクス

値は記載しない。キー名と必要サービスだけを整理する。

## 共通

| Key | admin | bot | bot-irsia | db | youtube-vpn-proxy | voicevox-engine |
| --- | --- | --- | --- | --- | --- | --- |
| `APP_ENV` | yes | yes | yes | no | no | no |
| `BOT_DATA_BACKEND` | yes | yes | yes | no | no | no |
| `ICHIYON_DATA_BACKEND` | yes | yes | yes | no | no | no |
| `DATABASE_URL` / `DATABASE_URL_DOCKER` | yes | yes | yes | no | no | no |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | no | no | no | yes | no | no |

## Discord / OAuth

| Key | admin | bot | bot-irsia | note |
| --- | --- | --- | --- | --- |
| `ADMIN_BASE_URL` | yes | no | no | 管理画面のみ |
| `ADMIN_SESSION_SECRET` | yes | no | no | 管理画面のみ |
| `DISCORD_OAUTH_CLIENT_ID` | yes | no | no | 管理画面OAuth |
| `DISCORD_OAUTH_CLIENT_SECRET` | yes | no | no | 管理画面OAuth |
| `DISCORD_OAUTH_REDIRECT_URI` | yes | no | no | 管理画面OAuth |
| `ICHIYON_DISCORD_TOKEN` | no | yes | no | いちよんロボのみ |
| `IRSIA_DISCORD_TOKEN` | no | no | yes | イルシアのみ |
| `DISCORD_TOKEN` / `DISCORD_BOT_TOKEN` | no | fallback | no | 既存互換のためいちよん側のみ |

## 外部API / 音楽

| Key | admin | bot | bot-irsia | youtube-vpn-proxy | note |
| --- | --- | --- | --- | --- | --- |
| `X_SEARCH_ENABLED` | yes | yes | yes | no | adminは設定確認、botは実行 |
| `X_BEARER_TOKEN` | yes | yes | yes | no | 現状互換。将来はadminから分離候補 |
| `YTDLP_COOKIES_FILE` | yes | yes | yes | no | adminは状態確認、botは抽出fallback |
| `YOUTUBE_HOME_VPN_ENABLED` | no | yes | yes | no | YouTube抽出経路 |
| `YOUTUBE_HOME_VPN_PROXY_URL` | no | yes | yes | no | YouTube抽出経路 |
| `YOUTUBE_HOME_VPN_*TIMEOUT_SECONDS` | no | yes | yes | no | YouTube抽出経路 |
| `YOUTUBE_HOME_VPN_FALLBACK_ENABLED` | no | yes | yes | no | YouTube抽出経路 |
| `YOUTUBE_HOME_VPN_OVPN_PATH` | no | no | no | yes | OpenVPN sidecarのみ |
| `YOUTUBE_HOME_VPN_TUN_MTU` / `YOUTUBE_HOME_VPN_MSSFIX` | no | no | no | yes | OpenVPN sidecarのみ |
| `YOUTUBE_HOME_VPN_DATA_CIPHERS*` | no | no | no | yes | OpenVPN sidecarのみ |
| `SPOTIFY_*` resolver settings | no | yes | yes | no | Spotify公開ページ解決設定。secretではない |

Spotify Premium / Developer App / Web API credentials は撤去済みで、Composeに渡さない。

## TTS / VOICEVOX

| Key | admin | bot | bot-irsia | voicevox-engine | note |
| --- | --- | --- | --- | --- | --- |
| `TTS_RUNTIME_ENABLED` | no | yes | yes | no | 未設定/falseで実行停止 |
| `VOICEVOX_ENGINE_URL` | no | yes | yes | no | BotからEngineを見るURL |
| `VOICEVOX_TIMEOUT_SECONDS` | no | yes | yes | no | Bot側HTTP timeout |
| `VOICEVOX_ENGINE_IMAGE` | no | no | no | yes | Compose image指定 |

`voicevox-engine` は `voicevox` profileを明示したときだけ起動する。
