# YouTube Home VPN Proxy

YouTube抽出だけを自宅OpenVPN経路へ逃がすためのsidecar構成です。Botコンテナ全体の通信経路は変更しません。

## 構成

- `youtube-vpn-proxy` service がOpenVPNで自宅Archer AX10へ接続します。
- 同じコンテナ内のtinyproxyが `http://youtube-vpn-proxy:8888` でHTTP CONNECT proxyを提供します。
- BotはYouTube抽出とFFmpegのYouTube stream取得時だけ、このproxyを使います。
- proxyが落ちている場合、Bot起動は止めず、既存の直通 + `YTDLP_COOKIES_FILE` 経路へfallbackします。

## 環境変数

```env
YOUTUBE_HOME_VPN_ENABLED=true
YOUTUBE_HOME_VPN_PROXY_URL=http://youtube-vpn-proxy:8888
YOUTUBE_HOME_VPN_CONNECT_TIMEOUT_SECONDS=5
YOUTUBE_HOME_VPN_EXTRACT_TIMEOUT_SECONDS=30
YOUTUBE_HOME_VPN_FALLBACK_ENABLED=true
YOUTUBE_HOME_VPN_OVPN_PATH=/home/ubuntu/OpenVPN-Config.ovpn
YOUTUBE_HOME_VPN_TUN_MTU=1400
YOUTUBE_HOME_VPN_MSSFIX=1360
YTDLP_COOKIES_FILE=/app/secrets/youtube-cookies.txt
```

`YTDLP_COOKIES_FILE` はVPN失敗時の直通fallbackで使います。Cookie実体はGit管理しません。

## OpenVPN設定

Archer AX10から取得したOpenVPN設定ファイルはホスト上へ配置します。

```sh
sudo install -m 600 OpenVPN-Config.ovpn /home/ubuntu/OpenVPN-Config.ovpn
```

Composeでは `/home/ubuntu/OpenVPN-Config.ovpn:/vpn/client.ovpn:ro` として読み取り専用でmountします。証明書、秘密鍵、Cookieはリポジトリに入れないでください。

## 起動

Bot serviceに `depends_on` は付けていません。proxyのhealthcheckが失敗してもBotは起動でき、YouTube抽出時に直通Cookie経路へfallbackします。

```sh
docker compose --profile bot --profile youtube-vpn up -d youtube-vpn-proxy bot
```

イルシアも同じimage/sidecarを使います。

```sh
docker compose --profile irsia --profile youtube-vpn up -d youtube-vpn-proxy bot-irsia
```

## 動作ログ

安全なログだけを出します。

- `route=home_vpn`
- `route=direct_cookie`
- `youtube_extract_fallback`
- `reason=<classified status>`
- `ffmpeg_proxy=enabled`

ログに出さないもの:

- OpenVPN設定内容
- 証明書、秘密鍵
- Cookie本文
- YouTube stream URL全体
- Discord token
- Spotify secret/access token

## 本番反映時の確認

1. `/home/ubuntu/OpenVPN-Config.ovpn` が存在し、権限が `600` であること。
2. `.env` に `YOUTUBE_HOME_VPN_ENABLED=true` を設定すること。
3. `docker compose --profile bot --profile youtube-vpn config --quiet` で構成を確認すること。
4. `youtube-vpn-proxy` のhealthcheckを確認すること。
5. YouTube再生ログで `route=home_vpn` が出ること。
6. proxy停止時に `youtube_extract_fallback ... to_route=direct_cookie` が出て既存Cookie経路で再生できること。
