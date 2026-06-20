# いちよんロボ ver2.0 複数サーバー対応土台

## 目的

ver2.0では、すべての運用データを基本的に `guild_id` で分離します。
今回の土台では、Discordサーバー情報を `guilds` に登録し、今後のBot/管理画面から参照できるRepositoryとヘルパーを用意します。

既存Bot挙動、既存JSON処理、管理画面実装はまだ切り替えません。

## guild_id 方針

`guild_id` はDiscordのIDを文字列として扱います。
DB上も `TEXT` として保存します。

理由:

* Discord IDは数値だが、アプリケーション内では文字列として扱うほうが安全
* Python/JavaScript/DB間で桁落ちや型差異を避けやすい
* 既存のver2.0スキーマが `guild_id TEXT` 前提

## guilds

`guilds` は管理対象サーバーの基本情報を保持します。

主な項目:

* `guild_id`
* `name`
* `icon_url`
* `enabled`
* `created_at`
* `updated_at`

`GuildRepository` は通常の `upsert` に加えて、Discordのguildオブジェクトから `guild_id`、`name`、`icon_url` を取り出して登録できます。

## guild_id ヘルパー

`bot/guild_context.py` に、Discordメッセージやguildオブジェクトから `guild_id` を取り出すヘルパーを追加します。

* `message.guild` がある場合は `str(message.guild.id)` を返す
* DMなど `message.guild` がない場合は `None` を返す

このヘルパーはまだ既存Bot処理には組み込みません。

## サーバー別初期化

新しいguildを検知した時に、将来的には以下を行います。

* `guilds` へサーバー情報を登録
* 必要な `feature_flags` 初期値を作成

ただし、既存Bot起動時に勝手にDBへ登録する処理はまだ入れません。
初期化関数は用意しますが、呼び出しは後続Phaseで行います。

## guild_idで分離する対象

主な対象:

* メンション反応
* メンション反応の抽選候補
* 自動反応
* NGワード
* 特殊効果タグ
* モード
* モード状態
* カウント
* 自動投稿
* デッキ検索設定
* 機能ON/OFF
* サーバー別権限

## 想定サーバー

### いちよんラボ

開発・検証用サーバーです。
新機能、危険機能、DB移行検証を先に試す場所として扱います。

### ランセ地方

メイン運用サーバーです。
既存の名言、おみくじ、自動反応、NGワード、モード、自動投稿などを安定運用する想定です。

### レストラン

シャドバ用サーバーです。
デッキ検索を中心にし、暴れる機能やネタ寄り機能は基本OFF想定です。

## 権限管理

OAuth前の段階では、権限管理はまだ実装しません。
`guilds` と `feature_flags` の土台だけを用意し、Discord OAuth2ログイン後の権限判定は後続Phaseで扱います。

## 確認

確認用に `scripts/check_guild_repository.py` を用意します。

```bash
python scripts/check_guild_repository.py --database-url postgresql://ichiyon_robot:ichiyon_robot_password@localhost:5432/ichiyon_robot
```

本番データやBot Tokenは使いません。
