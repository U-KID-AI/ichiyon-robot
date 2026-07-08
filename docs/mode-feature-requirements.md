# モード機能 要望調査メモ

## 前提

本メモは、既存の `modes` / `mode_trigger_conditions` / `mode_exit_conditions` / `mode_reply_choices` と Bot 実行時処理だけで、追加要望をどこまで実現できるかを調査したものです。実装変更は行っていません。

調査対象の主な実装:

- `admin/modes.py`
- `admin/templates/mode_form.html`
- `bot/repositories/modes.py`
- `bot/services/runtime_db.py`
- `bot/messages.py`
- `migrations/001_initial_schema.sql`

## 既存モード機能の範囲

### モード定義

`modes` は Bot別・サーバー別に分離されています。現在のRepositoryは `bot_id` と `guild_id` を条件にしてモード、返信候補、発動条件、終了条件を取得します。

主な設定:

- `behavior_type`
  - `reply`: モード中、`mode_reply_choices` から返信する
  - `offline`: モード中、通常処理を止める
- `duration_seconds`
  - モード継続秒数。未設定時は `mode_exit_conditions` の duration が fallback
- `enter_message` / `exit_message`
- ニックネーム、アイコン、疑似オフライン表示
- `reaction_channel_ids` / `ignore_channel_ids`
- `cooldown_config_json`

### 発動条件

管理画面で選べる発動条件は以下です。

- `probability`
- `counter_threshold`
- `period_not_triggered`
- `manual`
- `schedule`

実行時に実際の発動判定として評価されているのは以下です。

- `probability`
- `counter_threshold`
- `period_not_triggered`

`manual` / `schedule` はUI・DB定義にはありますが、通常メッセージ処理時の `mode_triggers_met()` では actionable 条件に含まれていません。

### 確率指定

確率はJSONで指定できます。

```json
{
  "probability": {
    "numerator": 14,
    "denominator": 141414
  }
}
```

`parse_probability()` は `numerator` / `denominator` を整数として読み、`probability_hit_with_multiplier()` が判定します。したがって、`14/141414` のような分数確率は既存実装だけで表現できます。

### 終了条件・自動解除

`modes.duration_seconds` が主設定です。`duration_seconds=180` で3分モードを表現できます。

期限切れ復帰は既存の期限切れ監視・メッセージ処理時判定の両方で扱われています。

### モード中返信

`behavior_type=reply` のモード中は、`mode_reply_choices` から重み付きで1件選び、本文/画像を送ります。候補を1件だけ登録すれば、固定文言のみ返すモードとして使えます。

現状の返信タイプは「登録済み本文/画像の送信」のみです。特定Discordユーザーの発言をオウム返しする `mimic_user` / `echo_user_message` のような reply type はありません。

### メンション事故対策

`send_text_or_image()` は通常の `channel.send(content)` を使っています。現状では `discord.AllowedMentions.none()` のような全体的なメンション抑止は見当たりません。

固定文言を管理者が安全に書く限り大きな問題にはなりにくいですが、ユーザー発言をオウム返しする機能を追加する場合は、`@everyone` / `@here` / ユーザーメンションの事故防止が必須です。

## 要望別の実現可否

### 1. 「記憶パ」の話題が出た瞬間にタケツミロボモードになる

結論: 現状のモード設定だけでは不可です。

理由:

- `mode_trigger_conditions` にキーワード条件がありません。
- 実行時の `mode_triggers_met()` はメッセージ本文を見ず、確率・カウンター・期間内未発動だけを評価しています。
- 自動反応で「記憶パ」を検知することは既存機能で可能ですが、それを直接「モード突入」に接続する設定が、通常管理画面だけで完結するかは限定的です。

既存特殊効果に `mode_enter` / `mode_roll` があるため、もし自動反応の特殊効果割り当てで `mode_enter` が正しく対象モードを指定できるなら、「自動反応: 記憶パ」→「特殊効果: mode_enter」で代替できる可能性があります。ただし、純粋な `mode_trigger_conditions` のキーワード条件では実現できません。

追加実装候補:

- `mode_trigger_conditions.condition_type = "keyword"` を追加
- 設定例:

```json
{
  "keywords": ["記憶パ"],
  "match_type": "contains",
  "ignore_bots": true
}
```

- `mode_triggers_met()` またはその前段に、メッセージ本文を渡して判定する
- 管理画面の発動条件UIに「キーワード」を追加

DB migrationは必要です。既存CHECK制約に `keyword` を追加する必要があります。

### 2. 14/141414 の確率でいちよんほぼモードになる

結論: 確率発動そのものは既存設定で可能です。オウム返しは追加実装が必要です。

設定だけでできる部分:

- モードを `behavior_type=reply` で作成
- 発動条件に `probability` を追加
- 条件JSONに `{"probability":{"numerator":14,"denominator":141414}}` を設定
- 必要なら `duration_seconds` を設定
- 固定返信候補を `mode_reply_choices` に登録

既存のはゆすモード系との関係:

- 旧 `bot/hayusu.py` には `random.randrange(config.HAYUSU_TRIGGER_RATE)` のJSON外ロジックがあります。
- DBモード側にはより汎用的な `probability` 条件があります。
- v3系ではDBモードの `probability` 条件を使う方が自然です。

足りない部分:

- 「いちよん本人の発言をオウム返しする返答タイプ」は未実装です。
- 現状の `mode_reply_choices` は固定本文/画像の選択のみです。

追加実装候補:

- `modes.behavior_type` に `mimic_user` を追加する、または `appearance_config_json` / `mode_reply_choices` に `reply_type` を持たせる
- 設定例:

```json
{
  "reply_type": "echo_user_message",
  "target_user_ids": ["..."],
  "sanitize_mentions": true,
  "ignore_bots": true,
  "ignore_self": true,
  "fallback_body": ""
}
```

- 実行時は対象ユーザーの投稿だけをコピーし、それ以外は無視または固定返信に fallback
- 送信時は `AllowedMentions.none()` 相当、または本文中の `@everyone` / `@here` を無害化

DB migrationは設計次第です。`behavior_type` のCHECK制約へ値を追加する場合はmigrationが必要です。JSON設定だけで表現するならmigrationなしでも可能ですが、管理画面のUI追加は必要です。

### 3. ヒイロロボモードになり、3分間「シャドバすっげー楽しい！」しか言わなくなる

結論: モード突入条件次第ですが、モード中の3分固定返信は既存設定だけで可能です。

設定だけでできる部分:

- モード:
  - `mode_key = hiiro` など
  - `behavior_type = reply`
  - `duration_seconds = 180`
- 返信候補:
  - `mode_reply_choices` を1件だけ作成
  - `body = "シャドバすっげー楽しい！"`
  - `appearance_rate = 1`
  - `enabled = true`

これにより、モード中の通常メッセージに対して固定文言のみ返す挙動になります。

注意:

- モード突入条件を「確率」「カウンター」「期間内未発動」でよいなら既存設定で可能です。
- 「特定キーワードでヒイロロボモードへ突入」したい場合は、要望1と同じくキーワード発動条件または自動反応＋特殊効果連携の確認/実装が必要です。

## bot_id + guild_id 分離

モード関連Repositoryは `bot_id` + `guild_id` をWHERE条件に含めています。

確認済みの範囲:

- `modes`
- `mode_reply_choices`
- `mode_trigger_conditions`
- `mode_exit_conditions`
- `mode_states`
- `mode_trigger_history`

したがって、同一 `guild_id` でも `ichiyon` と `irsia` で別モード設定として扱える設計です。

## 追加実装候補まとめ

優先度高:

1. キーワード発動条件
   - `condition_type = "keyword"`
   - メッセージ本文・match_type・除外bot・対象チャンネル設定
   - 管理画面UI追加
   - CHECK制約migrationが必要

2. オウム返し返信タイプ
   - `echo_user_message` / `mimic_user`
   - 対象DiscordユーザーID
   - bot/self除外
   - `@everyone` / `@here` / メンション抑止
   - 送信時 `AllowedMentions.none()` 相当

優先度中:

3. モード返信の送信安全化
   - 固定文言・画像送信も含めて `allowed_mentions` を明示
   - 既存挙動への影響確認が必要

4. 管理画面の発動条件フォーム改善
   - 現状は条件JSONを直接入力する形
   - probability / counter / keyword などをフォームで入力できると事故が減る

## 要望別まとめ

| 要望 | 既存設定だけで可能か | 補足 |
| --- | --- | --- |
| 「記憶パ」でタケツミロボモード | 不可 | モード条件にキーワード判定がない。自動反応＋特殊効果で代替可能か追加確認が必要 |
| 14/141414でいちよんほぼモード | 一部可能 | 確率発動は可能。オウム返しは未実装 |
| 3分間固定文言のヒイロロボモード | 可能 | `duration_seconds=180` + 返信候補1件で実現可能。突入条件がキーワードなら追加実装が必要 |

