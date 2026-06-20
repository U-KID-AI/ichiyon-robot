# ver2.0 メンション反応管理画面

## 目的

`/guilds/{guild_id}/mention-reactions` は、サーバー単位でDB上の `mention_reactions` と `mention_reaction_choices` を確認するためのver2.0管理画面です。

このPhaseでは、既存Bot挙動、既存JSON処理、旧JSON編集画面は変更しません。DB上の一覧表示、検索・絞り込み、ON/OFF状態の管理だけを追加します。

## ランダム抽選と検索

メンション反応は大きく2種類に分けます。

- ランダム抽選: `reaction_kind=random` を管理画面では `random_draw` として表示します。名言、おみくじ、お前も〇〇よな？のように、`mention_reaction_choices` から候補を抽選して返す機能です。
- 検索: `reaction_kind=search` で表します。デッキ検索のように、システム側で固定された検索処理に接続する機能です。

検索型はシステム固定機能として扱い、管理画面から新規追加しません。デッキ検索は最初の機能一覧には表示せず、後続Phaseで「メンション反応 > 検索 > デッキ検索」に配置します。

## 固定機能と編集方針

名言のような既定のメンション反応は `is_system=true` として扱います。固定機能の枠そのものは削除不可ですが、候補は後続Phaseで編集できる方針です。

`is_system=true` でもON/OFFは可能です。通常反応は `editor` 以上、`admin_only=true` の反応は `guild_admin` 以上が切り替えられます。`viewer` は閲覧のみです。

## 検索・絞り込み

一覧では以下の条件をクエリパラメータで保持します。

- `q`: 反応名、キーワード、説明を部分一致で検索
- `kind`: `all` / `random_draw` / `search`
- `system`: `all` / `system` / `custom`
- `enabled`: `all` / `true` / `false`

例: `/guilds/{guild_id}/mention-reactions?q=くじ&kind=random_draw&enabled=true`

## キーワード重複と一致優先度

同一サーバー内で同じ `keyword` は重複登録しない方針です。DBスキーマでは `UNIQUE (guild_id, keyword)` により表現します。

`B` と `ABC` のように複数のキーワードが一致する場合は、長いキーワードを優先します。それでも同じ長さなら作成が古いものを優先します。Repositoryの取得順もこの方針に合わせて、`LENGTH(keyword) DESC, created_at ASC` を基本にします。

## Infoモーダル

各行のInfoボタンはクリックで開きます。ホバー依存にせず、PCとスマホのどちらでも確認できるようにします。

表示内容は、反応名、説明、種類、キーワード/パターン、一致方式、OFF時の挙動、固定機能かどうか、管理者限定かどうか、抽選候補数です。
