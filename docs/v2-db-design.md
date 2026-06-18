# いちよんロボ ver2.0 DB設計

## 方針

ver2.0 では PostgreSQL への移行を想定します。
JSON管理は今後限界になりそうなため、RDBへ移行します。
Docker導入も検討しているため、DBはSQLiteではなくPostgreSQLを第一候補にします。

すべての運用データは基本的に `guild_id` を持たせ、サーバーごとに分離します。

## 共通カラム方針

多くのテーブルに以下のカラムを持たせます。

* `id`: 主キー
* `guild_id`: DiscordサーバーID
* `enabled`: 有効/無効
* `created_at`: 作成日時
* `updated_at`: 更新日時

表示順が必要なデータには `sort_order` を持たせます。
削除を復元したいデータでは、物理削除ではなく `deleted_at` の導入を検討します。

## テーブル候補

### guilds

管理対象サーバーを表します。

主な項目:

* `guild_id`
* `name`
* `enabled`
* `created_at`
* `updated_at`

### feature_flags

サーバー別の機能ON/OFFを管理します。

主な項目:

* `guild_id`
* `feature_key`
* `enabled`
* `updated_by_discord_user_id`
* `created_at`
* `updated_at`

`feature_key` 候補:

* `quotes`
* `kuji`
* `reactions`
* `ng_words`
* `modes`
* `auto_posts`
* `deck_search`
* `special_effect_tags`
* `destroy`

### quotes

名言をサーバー別に管理します。
文字、画像、文字+画像に対応します。

主な項目:

* `guild_id`
* `text`
* `image_path`
* `enabled`
* `created_at`
* `updated_at`

画像実体はDBに直接保存せず、保存先のパスまたはオブジェクトキーを持つ方針です。

### kuji_results

おみくじ結果をサーバー別に管理します。

主な項目:

* `guild_id`
* `name`
* `body`
* `image_path`
* `appearance_rate`
* `enabled`
* `created_at`
* `updated_at`

管理画面では `weight` や `重み` ではなく「出やすさ」と表示します。

### reactions

自動反応をサーバー別に管理します。

主な項目:

* `guild_id`
* `trigger_text`
* `response_text`
* `image_path`
* `emoji_internal`
* `match_type`
* `priority`
* `enabled`
* `created_at`
* `updated_at`

複数トリガーが同時一致した場合は、優先度が高いものを優先します。
同率ならランダムにします。

### reaction_special_effect_tags

自動反応と特殊効果タグの関連を管理します。

主な項目:

* `reaction_id`
* `special_effect_tag_id`

### ng_words

NGワードをサーバー別に管理します。

主な項目:

* `guild_id`
* `word`
* `enabled`
* `created_at`
* `updated_at`

NGワードを含む投稿には通常反応しません。

### modes

はゆすモード、成田モード、しこっちモードなどを「モード」という機能の配下で管理します。

主な項目:

* `guild_id`
* `mode_key`
* `display_name`
* `activation_type`
* `duration_seconds`
* `notify_channel_id`
* `reaction_channel_ids`
* `ignore_channel_ids`
* `enabled`
* `created_at`
* `updated_at`

`activation_type` 候補:

* `probability`
* `count_threshold`
* `manual`

チャンネルIDの複数指定は、初期実装ではJSONBまたは別テーブルのどちらかで検討します。

### mode_states

現在のモード状態やカウントを保持します。

主な項目:

* `guild_id`
* `current_mode_key`
* `active_until`
* `pseudo_offline_until`
* `narita_count`
* `narita_period_key`
* `narita_activated_in_period`
* `created_at`
* `updated_at`

モード中は他のモードに遷移しない仕様にします。

### auto_posts

自動投稿を管理します。
6/30自動投稿は単独機能ではなく、このテーブルの1レコードとして扱います。

主な項目:

* `guild_id`
* `name`
* `body`
* `image_path`
* `channel_id`
* `schedule_type`
* `schedule_value`
* `repeat_rule`
* `enabled`
* `last_posted_at`
* `created_at`
* `updated_at`

6/30の設定例:

```txt
名前: 6/30 サ終やめませんか？
日付: 毎年6月30日
本文: サ終やめませんか？
```

### special_effect_tags

自動反応に付与する特殊効果タグを管理します。

主な項目:

* `guild_id`
* `name`
* `effect_type`
* `effect_value`
* `enabled`
* `created_at`
* `updated_at`

効果タイプ候補:

* `probability_multiplier`
* `next_action_count_add`
* `count_add`
* `mode_lottery`
* `pseudo_offline_lottery`

### deck_settings

デッキ検索のサーバー別設定を管理します。

主な項目:

* `guild_id`
* `allowed_channel_ids`
* `result_limit`
* `default_format_policy`
* `forbidden_channel_message`
* `enabled`
* `created_at`
* `updated_at`

デッキ一覧CRUDは不要です。
Botにデッキを保存せず、Xから条件に合う最新ポストを検索して貼ります。

### admin_users

全体管理者やユーザー権限を管理します。

主な項目:

* `discord_user_id`
* `role`
* `created_at`
* `updated_at`

`role` 候補:

* `global_admin`
* `viewer`

### guild_admins

サーバー単位の権限を管理します。

主な項目:

* `guild_id`
* `discord_user_id`
* `role`
* `created_at`
* `updated_at`

`role` 候補:

* `guild_admin`
* `editor`
* `viewer`

## 移行方針

初期実装では、共通データは不要です。
名言やワードは基本サーバー別とします。

必要になった場合は、将来以下の形で追加します。

* 他サーバーへコピー
* 共通テンプレート
* 初期データプリセット

## 注意事項

秘密情報や本番データはコミットしません。
画像アップロード実体もコミット対象にしません。

Python 3.8対応を維持するため、DB層の実装時もPython 3.10前提の型ヒントは使いません。

