# いちよんロボ ver2.0 JSON移行手順

## 目的

既存のJSONデータを、ver2.0のPostgreSQLスキーマへ投入します。
既存Botの挙動、既存JSON処理、管理画面実装は変更しません。

## 対象

移行するデータ:

* `data/quotes.json`
* `data/kuji.json`
* `data/reactions.json`
* `data/ng_words.json`

移行しないデータ:

* `data/responses.json`
* `data/state.json`

`responses.json` と `state.json` は移行先がまだ曖昧なため、後続Phaseで判断します。

## 方針

`quotes.json` は `mention_reactions` に「名言」枠を作り、`mention_reaction_choices` に候補を投入します。
名言枠は `is_system = true`、`is_deletable = false` として扱います。

`kuji.json` は `mention_reactions` に「おみくじ」枠を作り、`mention_reaction_choices` に候補を投入します。
既存の `weight` は「出やすさ」として `appearance_rate` に移行します。

`reactions.json` は ver2.0 スキーマ上の `reactions` に投入します。
設計上は自動反応データであり、JSON内の `trigger`、`response`、`image_path`、`emoji`、`priority`、`enabled` を可能な範囲で移行します。

`ng_words.json` は `ng_words` に投入します。

画像ファイル自体はコピーしません。
JSON内の `image_path` だけをDBへ保存します。

## 冪等性

再実行しても重複投入しない方針です。

* `mention_reactions`: `guild_id` + `reaction_key` で既存行を更新
* `mention_reaction_choices`: `guild_id` + `mention_reaction_id` + `name` で既存行を更新
* `reactions`: `guild_id` + `trigger_text` で既存行を更新
* `ng_words`: `guild_id` + `word` で既存行を更新

既存データがあれば更新し、投入できない空データはスキップします。

## 実行例

先にPostgreSQLと初期スキーマを用意します。

```bash
docker compose up -d postgres
python scripts/migrate.py --database-url postgresql://ichiyon_robot:ichiyon_robot_password@localhost:5432/ichiyon_robot
```

dry-run:

```bash
python scripts/migrate_json_to_db.py --guild-id 123456789012345678 --dry-run
```

実行:

```bash
python scripts/migrate_json_to_db.py --guild-id 123456789012345678 --guild-name ランセ地方 --database-url postgresql://ichiyon_robot:ichiyon_robot_password@localhost:5432/ichiyon_robot
```

## 注意

`.env`、Bot Token、秘密鍵、本番データ、`data/backups/` は触りません。
このスクリプトは既存JSONファイルを読み取るだけで、JSONファイル自体は変更しません。
