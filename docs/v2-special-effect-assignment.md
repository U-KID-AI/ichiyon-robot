# ver2.0 特殊効果タグ付与UI

## 目的

特殊効果タグを、DB上のイベントや候補へ付与するための管理画面土台です。

このPhaseでは既存Bot挙動、既存JSON処理、旧JSON編集画面は変更しません。付与状態は `special_effect_assignments` に保存しますが、Bot実行時への反映は後続Phaseで行います。

## 付与対象

特殊効果タグの付与対象は以下の3種類に限定します。

- `mention_reaction_choice`
- `auto_reaction`
- `ng_word`

メンション反応本体とモードは付与対象にしません。

## 今回の実装範囲

今回は `mention_reaction_choice`、つまりメンション反応の抽選候補への付与から実装します。

メンション反応編集画面では、各抽選候補に付与済みの特殊効果タグを表示します。タグ名、タグ色、効果タイプを確認でき、「特殊効果を編集」から付与画面へ移動できます。

付与画面では、`target_type = mention_reaction_choice` のタグだけを表示します。検索、効果タイプ、管理者限定、無効タグを含めるかどうかで絞り込めます。

## 権限

`viewer` は閲覧のみです。

`editor` は通常タグを付与・解除できます。

`admin_only=true` のタグは `guild_admin` 以上だけが表示・付与・解除できます。`global_admin` は全操作可能です。

## 重複付与

同じタグを同じ候補に重複付与しません。DBでは `UNIQUE (special_effect_tag_id, target_type, target_id)` を使い、再付与時は既存 assignment を有効化します。解除時は assignment を無効化します。

## 想定例

「お前も〇〇よな？」の抽選候補に、ミニいちよん用の特殊効果タグを付与する想定です。

- 抽選候補: お前も〇〇よな？の候補
- 特殊効果タグ: ミニいちよん
- `target_type`: `mention_reaction_choice`
- `effect_type`: `probability_message`
- 効果設定例: `{"rate":{"numerator":1,"denominator":32},"message":"..." }`

通常返答を先に実行し、タグの効果判定に当選した場合だけ追加テキストを投稿する流れを後続Phaseで実装します。
