# いちよんロボ ver2.0 サーバー選択画面

## 目的

Discord OAuthログイン後に、ログインユーザーが管理可能なサーバー一覧を表示する土台を追加します。
既存Bot挙動、既存JSON処理、既存管理画面のDB切り替えはまだ行いません。

## 画面遷移

```txt
Discordログイン
↓
/servers
↓
/guilds/{guild_id}
```

`/guilds/{guild_id}` は仮のサーバー管理トップです。
本格的な機能一覧は次Phaseで実装します。

## 権限ごとの表示ルール

### global_admin

`admin_users.role = global_admin` のユーザーです。
`guilds` に登録されている全サーバーを表示します。

### guild_admin

`guild_permissions.role = guild_admin` のユーザーです。
権限があるサーバーだけ表示します。

### editor

`guild_permissions.role = editor` のユーザーです。
権限があるサーバーだけ表示します。
編集可能範囲は後続Phaseで画面ごとに制御します。

### viewer

`guild_permissions.role = viewer` のユーザーです。
権限があるサーバーだけ表示します。
閲覧のみの制御は後続Phaseで画面ごとに制御します。

## Discord APIについて

このPhaseでは、Discord APIから所属サーバー一覧を取得しません。
DB上の `guilds`、`admin_users`、`guild_permissions` を前提に表示します。

## 未ログイン時

`/servers` と `/guilds/{guild_id}` はログイン必須です。
未ログインの場合は `/login` へリダイレクトします。

## 権限なし

ログイン済みでも対象サーバーへの権限がない場合、`/guilds/{guild_id}` は403を返します。
