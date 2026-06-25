# ver2.0 本番後の軽微修正

## 内容

- メンション後の文が他機能に一致しない時、単体メンション反応へ戻す。
- 本文なしの候補、自動反応を安全に扱う。画像だけ、リアクションだけ、特殊効果だけでも実行対象。
- デッキ検索の画像確認を並列化し、候補が揃ったら止める。
- デッキ検索の `debug_tweet_id` は廃止。特定tweet調査は通常検索に混ぜない。
- `next_action_count` と `probability_multiplier` は、付与した反応ではなく次の反応へ効かせる。
- `probability_multiplier` は共通抽選処理で扱い、複数ある場合は乗算する。
- くじ候補に `result_label` を追加し、「大吉」「中吉」などを表示・返信に使う。
- デッキ検索の結果なし文言は `おい ないんだが`。
- スマホ表示のダークテーマを `admin/static/style.css` へ正式反映。
- 同じリアクションが一定数ついた時に返信するDB設定を追加。
- 特定ユーザーのメンションに「さん」付けを求める特殊効果 `mention_suffix_guard` を追加。
- メンション文の「テスト」で反応を止める古い抑止を撤去。
- 起動時の古い表示名ログを簡素化。

## probability_multiplier

`probability_multiplier` はライオ専用ではなく、次に発生する確率抽選へ使う。

対象:

- メンション反応候補の重み付き抽選
- モード返答候補の重み付き抽選
- モード発動条件の確率抽選
- 特殊効果 `probability_message` などの確率抽選

複数の倍率が有効な場合は加算ではなく乗算。

例:

- 9倍が1つ: 9倍
- 9倍が2つ: 81倍
- 32倍 + 1/32抽選: 実質確定

倍率対象が指定されている場合は、対象種別とIDが一致した抽選だけに効く。対象指定がない場合は、次の確率抽選へ効く。

## リアクション返信

同じメッセージに同じ絵文字リアクションが閾値以上ついた時、Botが返信する。

DB:

- `reaction_threshold_rules`
- `reaction_threshold_events`

`reaction_threshold_rules.config_json` 例:

```json
{
  "enabled": true,
  "threshold": 5,
  "reply_message": "同じリアクションが{threshold}個ついた",
  "allowed_channel_ids": [],
  "ignored_channel_ids": [],
  "target_emojis": [],
  "ignored_emojis": [],
  "once_per_message_emoji": true
}
```

`once_per_message_emoji=true` の時、同じ `message_id + emoji + threshold` では二重返信しない。

管理画面では「リアクション返信」から設定。

## さん付け制御

特殊効果タグ `mention_suffix_guard` を追加。

限定機能で特定ユーザーへ付与すると、そのユーザーのBotメンションに「さん」がない時だけ通常メンション処理を止める。

`effect_config_json` 例:

```json
{
  "enabled": true,
  "target_user_ids": ["1290338867685363764"],
  "required_suffix": "さん",
  "bot_display_names": ["いちよんロボ", "いちよんロボ-stg"],
  "accepted_patterns": [],
  "warn_every": 3,
  "warning_message": "さんを付けろよ"
}
```

動き:

- 対象ユーザーが「さん」なしでメンション: 通常メンション処理を止める。
- 3回ごとに `さんを付けろよ` を返す。
- 「さん」付きメンション: 通常処理へ通す。
- 対象外ユーザー: 影響なし。

カウントは `counters / counter_states` を使い、`mention_suffix_guard:<tag_id>:<user_id>` で管理。

## migration

通常は migration を実行。

```bash
python scripts/migrate.py --database-url "$DATABASE_URL"
```

追加された migration:

- `018_post_release_reaction_fixes.sql`
- `020_reaction_threshold_and_mention_guard.sql`

`020` の内容:

- `special_effect_tags.effect_type` に `mention_suffix_guard` を追加。
- `reaction_threshold_rules` を追加。
- `reaction_threshold_events` を追加。

既存データは削除しない。

## 本番DB SQL

デッキ検索の結果なし文言を補正する場合:

```sql
UPDATE mention_reactions
SET config_json = COALESCE(config_json, '{}'::jsonb)
  || '{"not_found_message":"おい ないんだが"}'::jsonb,
    updated_at = NOW()
WHERE reaction_key = 'deck_search'
  AND reaction_kind = 'search';
```

くじの何吉を旧JSONから補完する場合:

```bash
python scripts/migrate_json_to_db.py --database-url "$DATABASE_URL" --guild-id "<guild_id>"
```

## stg確認

```bash
python -m compileall bot admin scripts
python scripts/check_deck_search_runtime.py
python scripts/check_special_effects_runtime.py
python scripts/check_reaction_runtime.py
python scripts/check_reaction_threshold_runtime.py
python scripts/check_mention_guard_runtime.py
python scripts/check_modes_runtime.py
python scripts/check_auto_posts_runtime.py
python scripts/check_v2_db_integration.py --database-url "$DATABASE_URL" --guild-id 1515983621461245972
```

Discordで見ること:

- `@Bot なんでもない文章` が単体メンション反応になる。
- `@Bot おみくじ` はおみくじを優先。
- `@Bot デッキ エルフ` はデッキ検索を優先。
- `@Bot テスト 名言` でも通常通り反応する。
- ライオ系倍率を2つ重ねた時、倍率が乗算される。
- 同じ絵文字リアクション5個で返信し、同じメッセージと絵文字では二重返信しない。
- せるえす用 `mention_suffix_guard` 付与後、「さん」なし3回目で警告、「さん」付きは通常処理。
- ログにToken、QR文字列、`debug_tweet_id` が出ない。

管理画面:

- スマホ幅で背景、カード、フォーム、ヘッダーがダークテーマ。
- 権限不足の操作ボタンは無効表示。
- 「リアクション返信」設定を作成・編集できる。
- 特殊効果タグで `さん付け確認` を選べる。

## 本番反映

1. mainへマージ。
2. アプリを停止、またはメンテ状態へ。
3. migrationを実行。
4. 必要ならデッキ検索文言SQLとJSON再移行を実行。
5. Botと管理画面を再起動。
6. stg確認と同じ観点で軽く疎通確認。
