# ver2.0 特殊効果タグ管理画面

## 目的

`/guilds/{guild_id}/special-effects` は、ver2.0管理画面でDB上の `special_effect_tags` を作成・編集するための土台です。

このPhaseでは既存Bot挙動、既存JSON処理、旧JSON編集画面は変更しません。DBに登録した特殊効果タグをBot実行時へ反映する作業は後続Phaseで行います。

## 一覧

一覧では、タグの有効状態、タグ名、タグ色、効果タイプ、管理者限定、追加投稿の有無、クールタイム、Infoを表示します。

検索・絞り込みは以下をクエリパラメータで保持します。

- `q`: タグ名、説明のキーワード検索
- `effect_type`: 効果タイプ
- `enabled`: `all` / `true` / `false`
- `admin_only`: `all` / `true` / `false`

## 作成・編集

`editor` 以上は通常タグを作成・編集できます。`admin_only=true` のタグは `guild_admin` 以上だけが作成・編集・ON/OFFできます。`viewer` は閲覧のみです。

入力項目は、タグ名、説明、タグ色、有効/無効、管理者限定、優先度、付与可能対象、発動タイミング、効果タイプ、効果設定JSON、追加投稿テキスト、追加投稿タイミング、有効期限、クールタイムです。

タグ色は `#RRGGBB` 形式です。効果設定JSONはJSONオブジェクトである必要があります。追加投稿テキストは空白のまま保存できます。

## 付与可能対象

特殊効果タグの付与可能対象は以下に限定します。

- `mention_reaction_choice`
- `auto_reaction`
- `ng_word`

メンション反応本体とモードは付与対象にしません。

## 危険度

危険度 `danger_level` は持ちません。危険な効果や強い効果は `admin_only` と権限で制御します。

## プリセット例

ミニいちよん:

- `effect_type`: `probability_message`
- `effect_config_json`: `{"rate":{"numerator":1,"denominator":32},"message":"..." }`
- 付与されたイベントが呼び出されるたびに抽選し、当選時だけ追加テキストを投稿する想定です。通常返答は先に実行します。

しこっち抽選:

- `effect_type`: `counter_set`
- `effect_config_json`: `{"rate":{"numerator":1,"denominator":444},"counter_key":"shikocchi_count","value":1}`
- 1/444で当選したら `shikocchi_count` を1にする想定です。

成田カウント加算:

- `effect_type`: `counter_delta`
- `effect_config_json`: `{"counter_key":"narita_count","delta":1}`
- 成田系の条件判定に使うカウント加算の例です。
