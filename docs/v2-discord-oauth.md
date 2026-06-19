# いちよんロボ ver2.0 Discord OAuth認証

## 目的

管理画面用にDiscord OAuth2ログインの土台を追加します。
既存Bot挙動、既存JSON処理、既存管理画面の編集画面はまだDB運用や認証必須へ切り替えません。

## 環境変数

`.env.example` には以下の名前だけをダミー値で記載します。
実際の値は `.env` にだけ設定し、Gitには入れません。

* `ADMIN_BASE_URL`
* `ADMIN_SESSION_SECRET`
* `DISCORD_OAUTH_CLIENT_ID`
* `DISCORD_OAUTH_CLIENT_SECRET`
* `DISCORD_OAUTH_REDIRECT_URI`

Bot TokenはOAuthログインには使いません。

## Discord Developer Portal設定

Discord Developer PortalでOAuth2設定を開き、Redirect URIを登録します。

ローカル開発例:

```txt
http://localhost:8000/auth/discord/callback
```

公開環境例:

```txt
https://example.com/auth/discord/callback
```

本番ではHTTPSを推奨します。
現状HTTP公開で使う場合は、通信経路やCookieの扱いに注意してください。

## 追加ルート

* `GET /login`
* `GET /auth/discord`
* `GET /auth/discord/callback`
* `GET /logout`
* `POST /logout`
* `GET /me`

`/me` はログイン状態確認用の簡易ページです。
既存の名言、自動反応、NGワード、おみくじ編集画面にはまだログイン必須を適用しません。

## セッション

Starletteの `SessionMiddleware` を使います。
セッションには最低限のDiscordユーザー情報だけを保存します。

保存するもの:

* Discord user ID
* username
* global name
* avatar URL

保存しないもの:

* access token
* refresh token
* Bot Token

`ADMIN_SESSION_SECRET` が未設定の場合、開発環境では警告を出して開発用フォールバックを使います。
`APP_ENV=production` では起動時にエラーにします。

## 権限管理

本格的な権限管理はまだ実装しません。
今回追加するのは、ログイン必須依存関数とDiscordユーザー情報取得の土台です。

後続Phaseで `admin_users` や `guild_permissions` と接続し、サーバー別の権限判定を行います。
その接続土台として `PermissionRepository` を追加し、Discord user ID から全体管理者やサーバー別権限を取得できるようにします。

## 追加ライブラリ

`httpx` を追加します。
Discord OAuthのトークン交換とユーザー情報取得をHTTP通信で行うためです。

`itsdangerous` を追加します。
StarletteのSessionMiddlewareが署名付きCookieセッションに使用します。
