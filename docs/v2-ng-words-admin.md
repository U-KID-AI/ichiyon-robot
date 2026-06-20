# ver2.0 NGワードDB管理画面

## 目的

`/guilds/{guild_id}/ng-words` は、DB上の `ng_words` をver2.0管理画面から作成・編集するための土台です。

このPhaseでは既存Bot挙動、既存JSON処理、旧JSON編集画面は変更しません。DB上のNGワードをBot実行時へ反映する作業は後続Phaseで行います。

## 一覧

一覧では、有効状態、ワード、付与済み特殊効果タグを表示します。

検索・絞り込みは以下をクエリパラメータで保持します。

- `q`: ワード検索
- `enabled`: `all` / `true` / `false`
- `has_effects`: `all` / `true` / `false`

## 作成・編集

`editor` 以上はNGワードを作成・編集・ON/OFFできます。`viewer` は閲覧のみです。

入力項目はワードと有効/無効です。ワードは必須です。同じ `guild_id` 内で同じワードは保存できません。編集時は自分自身を除外して重複チェックします。

## 特殊効果タグ付与

`/guilds/{guild_id}/ng-words/{word_id}/effects` で、NGワードに特殊効果タグを付与・解除できます。

- `target_type`: `ng_word`
- `target_id`: `ng_words.id`
- 表示対象: `special_effect_tags.target_type = ng_word`

`editor` は通常タグを付与・解除できます。`admin_only=true` のタグは `guild_admin` 以上だけが付与・解除できます。重複付与は作らず、既存assignmentを有効化します。

## 想定例

成田カウント加算:

- `effect_type`: `counter_delta`
- `effect_config_json`: `{"counter_key":"narita_count","delta":1}`

NGワード検知時の通常反応停止に加えて、特殊効果タグを付与することでカウント加算や一時状態の付与などを後続Phaseで扱えるようにします。
