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

* `mention_reactions`
* `reactions`
* `ng_words`
* `modes`
* `auto_posts`
* `deck_search`
* `special_effect_tags`
* `destroy`

デッキ検索は最初の機能一覧には表示しません。
表示位置は「メンション反応 > 検索 > デッキ検索」です。

### mention_reactions

メンション反応本体をサーバー別に管理します。
「ランダム抽選」と「検索」に分けます。

主な項目:

* `guild_id`
* `reaction_key`
* `keyword`
* `display_name`
* `reaction_type`
* `system_locked`
* `deletable`
* `enabled`
* `created_at`
* `updated_at`

`reaction_type` 候補:

* `random`
* `search`

名言のメンション反応枠は `system_locked = true`、`deletable = false` とします。
同じサーバー内で既存と同じ `keyword` は保存不可です。

キーワード一致時は、長いキーワードを優先し、それでも同じなら作成が古いものを優先します。
B と ABC のようなキーワードがある場合、ABC入力時はABCを優先します。

### mention_reaction_choices

メンション反応のランダム抽選候補を管理します。
名言、おみくじ、お前も〇〇よな？はこの抽選候補で実現します。

主な項目:

* `guild_id`
* `mention_reaction_id`
* `body`
* `image_path`
* `appearance_rate`
* `enabled`
* `sort_order`
* `created_at`
* `updated_at`

特殊効果タグは、メンション反応本体ではなく抽選候補に付与します。

### mention_search_handlers

検索型メンション反応のシステム固定機能を管理します。
管理画面から新規追加はできません。

主な項目:

* `guild_id`
* `mention_reaction_id`
* `handler_key`
* `settings`
* `enabled`
* `created_at`
* `updated_at`

デッキ検索は `handler_key = deck_search` の検索型として扱います。

### quotes

名言は単独機能ではなく、メンション反応のランダム抽選枠として扱います。
既存移行時の互換用テーブルにするか、`mention_reaction_choices` へ統合するかは実装時に決めます。
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

おみくじは単独機能ではなく、メンション反応のランダム抽選枠として扱います。
既存移行時の互換用テーブルにするか、`mention_reaction_choices` へ統合するかは実装時に決めます。

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

### mention_reaction_choice_special_effect_tags

メンション反応の抽選候補と特殊効果タグの関連を管理します。

主な項目:

* `mention_reaction_choice_id`
* `special_effect_tag_id`

### ng_word_special_effect_tags

NGワードと特殊効果タグの関連を管理します。

主な項目:

* `ng_word_id`
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
モードはタイプ固定ではなく、発動条件、クールタイム、モード中挙動、返答候補、終了条件、通知、見た目、チャンネル設定で構成します。

主な項目:

* `guild_id`
* `mode_key`
* `name`
* `activation_settings`
* `cooldown_settings`
* `behavior_type`
* `end_condition_settings`
* `notification_settings`
* `appearance_settings`
* `duration_seconds`
* `enter_notify_channel_id`
* `exit_notify_channel_id`
* `reaction_channel_ids`
* `ignore_channel_ids`
* `enabled`
* `created_at`
* `updated_at`

`behavior_type` 候補:

* `reply`
* `offline`

モード基本情報に優先度は不要です。
モード時表示名はモード名と同じなので個別設定不要です。
通常時表示名と通常時アイコンはBot固定値へ戻すため、個別設定不要です。
見た目設定ではモード時アイコン画像を設定できるようにします。

設定系カラムはJSONB想定です。
チャンネルIDの複数指定は、初期実装ではJSONBまたは別テーブルのどちらかで検討します。

### mode_reply_choices

返答モード中の返答候補を管理します。
はゆすと成田は返答候補からランダムに返します。
はゆすは返答候補を1つ登録すればよいです。

主な項目:

* `guild_id`
* `mode_id`
* `body`
* `image_path`
* `appearance_rate`
* `enabled`
* `sort_order`
* `created_at`
* `updated_at`

### mode_counts

カウント到達条件で使うカウント定義を管理します。
既存カウントを使うだけでなく、新規カウントを自動作成できます。

主な項目:

* `guild_id`
* `count_name`
* `count_key`
* `description`
* `initial_value`
* `reset_type`
* `created_at`
* `updated_at`

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
* `shikocchi_count`
* `count_states`
* `created_at`
* `updated_at`

モード中は他のモードに遷移しない仕様にします。
モード中は他の機能を一切使いません。

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

メンション反応の抽選候補、自動反応、NGワードに付与する特殊効果タグを管理します。
メンション反応本体とモードには付与しません。

主な項目:

* `guild_id`
* `name`
* `color`
* `admin_only`
* `effect_type`
* `effect_settings`
* `additional_text`
* `enabled`
* `created_at`
* `updated_at`

危険度は不要です。
タグ色は自由に設定できます。
管理者限定を持ちます。
効果設定はJSONB想定で柔軟にします。
追加投稿テキストは空白可です。

効果タイプ候補:

* `probability_multiplier`
* `next_action_count_add`
* `count_add`
* `mode_lottery`
* `pseudo_offline_lottery`
* `mini_ichiyon`
* `shikocchi_lottery`

ミニいちよんは特殊効果タグとして扱います。
付与されたイベントが呼び出されるたびに抽選し、当選した場合だけ追加テキストを投稿します。
通常返答は先に実行します。

しこっち抽選は特殊効果タグとして扱います。
1/444で当選したら `shikocchi_count` を1にします。

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

### guild_permissions

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
