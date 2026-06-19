# ver2.0 自動反応DB管理画面

## 目的

`/guilds/{guild_id}/auto-reactions` は、DB上の `reactions` をver2.0管理画面から作成・編集するための土台です。

このPhaseでは既存Bot挙動、既存JSON処理、旧JSON編集画面は変更しません。DB上の自動反応をBot実行時へ反映する作業は後続Phaseで行います。

## 一覧

一覧では、有効状態、トリガー、返答テキスト概要、画像パスの有無、絵文字、一致方式、優先度、付与済み特殊効果タグを表示します。

検索・絞り込みは以下をクエリパラメータで保持します。

- `q`: トリガー、返答テキスト、絵文字の検索
- `enabled`: `all` / `true` / `false`
- `has_image`: `all` / `true` / `false`
- `has_effects`: `all` / `true` / `false`

## 作成・編集

`editor` 以上は通常の自動反応を作成・編集・ON/OFFできます。`viewer` は閲覧のみです。

入力項目は以下です。

- トリガー
- 返答テキスト
- 画像パス
- 絵文字内部表記
- 一致方式: `exact` / `prefix` / `regex` / `contains`
- 優先度
- 有効/無効

トリガーは必須です。返答テキスト、画像パス、絵文字のいずれか1つは必須です。同じ `guild_id` 内で `trigger_text + match_type` が重複する保存は不可にします。

現スキーマの `reactions` には `admin_only` カラムがないため、自動反応本体の管理者限定はこのPhaseでは扱いません。

## 特殊効果タグ付与

`/guilds/{guild_id}/auto-reactions/{reaction_id}/effects` で、自動反応に特殊効果タグを付与・解除できます。

- `target_type`: `auto_reaction`
- `target_id`: `reactions.id`
- 表示対象: `special_effect_tags.target_type = auto_reaction`

`editor` は通常タグを付与・解除できます。`admin_only=true` のタグは `guild_admin` 以上だけが付与・解除できます。重複付与は作らず、既存assignmentを有効化します。

## 想定例

しこっち抽選:

- `effect_type`: `counter_set`
- `effect_config_json`: `{"rate":{"numerator":1,"denominator":444},"counter_key":"shikocchi_count","value":1}`

成田カウント加算:

- `effect_type`: `counter_delta`
- `effect_config_json`: `{"counter_key":"narita_count","delta":1}`

ライオ9倍:

- `effect_type`: `probability_multiplier`
- `effect_config_json`: `{"target":"raio","multiplier":9}`

さくらんぼ2回:

- `effect_type`: `next_action_count`
- `effect_config_json`: `{"target":"sakuranbo","count":2}`
