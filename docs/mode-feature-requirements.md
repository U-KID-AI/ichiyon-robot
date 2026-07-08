# モード機能 要望調査メモ

## 前提

本メモは、モード要望を `mode_trigger_conditions` へ直接追加する前に、既存の特殊効果基盤でどこまで実現できるかを調査したものです。実装変更は行っていません。

調査対象:

- `special_effect_tags`
- `special_effect_assignments`
- 自動反応 / メンション反応 / NGワードからの特殊効果発火
- `modes`
- `mode_trigger_conditions`
- `mode_exit_conditions`
- `mode_reply_choices`
- Bot実行時の `bot/services/runtime_db.py`
- 管理画面の `admin/special_effects.py`, `admin/auto_reactions.py`, `admin/mention_reactions.py`, `admin/ng_words_db.py`, `admin/modes.py`

## 重要な調査結果

### 特殊効果の付与先

特殊効果タグは管理画面から以下へ付与できます。

- メンション反応の抽選候補
- 自動反応
- NGワード

したがって、「特定キーワードを含む投稿」を自動反応で拾い、その自動反応に特殊効果を付ける流れは既存の管理画面導線で作れます。

### mode_enter / mode_roll の状態

DB制約、管理画面の選択肢、ラベルには `mode_enter` / `mode_roll` が存在します。

ただし、実行時の `execute_effects()` には `mode_enter` / `mode_roll` の処理が見当たりません。現状では、これらを特殊効果タグとして作れても、タグ発火時に直接モードへ入る処理は動かない可能性が高いです。

現時点で実運用上確実なルートは、既存のしこっち方式です。

1. 自動反応やメンション反応で特殊効果を発火
2. 特殊効果 `counter_set` または `counter_delta` でカウンターを変更
3. モード側の `counter_threshold` 条件が満たされる
4. メッセージ処理の最後で `enter_mode_if_needed()` がモードへ入る

### 特殊効果側の確率

一部の特殊効果は `probability` を持てます。特に `counter_set` は `probability_hit_with_multiplier()` を通っており、以下のような分数確率を扱えます。

```json
{
  "probability": {
    "numerator": 14,
    "denominator": 141414
  },
  "counter_key": "ichiyon_almost_count",
  "value": 1
}
```

したがって、「何かの発火を起点に、14/141414でカウンターを立て、モードへ入る」は既存特殊効果で表現できます。

ただし、「全投稿に対して常に14/141414で抽選する」には、全投稿に必ず反応する安全なトリガーが必要です。現状の自動反応は呼び出しワードが必要で、空トリガーの常時発火は通常設定としては用意されていません。

### モード中の返信

`behavior_type=reply` のモード中は、`mode_reply_choices` から返信候補を重み付き抽選して送ります。候補を1件だけにすれば固定文言モードになります。

特定Discordユーザーの投稿をオウム返しする `echo_user_message` / `mimic_user` 相当の返信タイプは未実装です。

### duration

`modes.duration_seconds` が主設定です。`duration_seconds=180` で3分モードを表現できます。

### bot_id + guild_id 分離

モード、特殊効果、自動反応、メンション反応、NGワードはいずれもRepository側で `bot_id` + `guild_id` を見ています。少なくともコード上は、Bot別・サーバー別の設定分離に乗せられます。

### メンション事故対策

`send_text_or_image()` は現状 `channel.send(content)` を使っています。`discord.AllowedMentions.none()` のような全体的なメンション抑止は見当たりません。

固定文言を管理者が安全に書く場合は運用で避けられますが、ユーザー発言をコピーするオウム返し機能を追加する場合は、以下が必須です。

- `@everyone` / `@here` 抑止
- ユーザーメンション、ロールメンションの抑止
- Bot自身や他Botの投稿をコピーしない
- 対象ユーザーIDを明示する

## 要望別の実現可否

### 1. 「記憶パ」の話題が出た瞬間にタケツミロボモードになる

結論: `mode_enter` 直結ではなく、既存特殊効果のカウンター経由なら設定だけで実現できる可能性が高いです。

想定設定:

1. モード作成
   - `mode_key`: `taketsumi`
   - `name`: `タケツミロボモード`
   - `behavior_type`: 必要に応じて `reply` または `offline`
   - `duration_seconds`: 任意
   - `enabled`: ON

2. モード発動条件
   - 条件種類: `counter_threshold`
   - 条件JSON:

```json
{
  "counter_key": "taketsumi_count",
  "operator": ">=",
  "threshold": 1
}
```

3. 自動反応作成
   - 呼び出しワード: `記憶パ`
   - 一致方式: `contains`
   - 返信文言: 空または運用上問題ない文言
   - 有効: ON

4. 特殊効果タグ作成
   - 付与できる対象: `auto_reaction`
   - 発動タイミング: `auto_reaction_triggered`
   - 効果の種類: `counter_set`
   - 詳細設定:

```json
{
  "counter_key": "taketsumi_count",
  "value": 1
}
```

5. 自動反応「記憶パ」に上記特殊効果タグを付与

この流れなら、`記憶パ` を含む通常投稿 → 自動反応発火 → `taketsumi_count=1` → モードの `counter_threshold` 成立 → `enter_mode_if_needed()` でモード突入、という既存ルートに乗ります。

注意:

- 自動反応が空返信を許すか、または返信なしで特殊効果だけ動かせるかは実データで確認が必要です。既存処理上は `send_text_or_image()` が空なら送らず、効果は実行される流れです。
- 使い終わったカウンターはモード突入時に `reset_counter_thresholds()` でリセットされます。
- `mode_enter` 効果を使う設定は現状おすすめしません。選択肢はありますが実行処理が未実装です。

追加実装が必要になる場合:

- `mode_enter` / `mode_roll` を本来の特殊効果として実装する場合
- 自動反応を完全な「無言トリガー」として管理画面上わかりやすく扱いたい場合

### 2. 14/141414 の確率でいちよんほぼモードになる

結論: 何かのトリガーに紐づく低確率モード発動は、既存特殊効果のカウンター経由で実現できます。全投稿常時抽選は追加設計が必要です。オウム返しは未実装です。

設定だけでできる部分:

1. モード作成
   - `mode_key`: `ichiyon_almost`
   - `name`: `いちよんほぼモード`
   - `behavior_type`: 既存固定返信なら `reply`
   - `duration_seconds`: 任意
   - `enabled`: ON

2. モード発動条件
   - 条件種類: `counter_threshold`
   - 条件JSON:

```json
{
  "counter_key": "ichiyon_almost_count",
  "operator": ">=",
  "threshold": 1
}
```

3. 特殊効果タグ作成
   - 付与できる対象: 自動反応、メンション反応候補、NGワードのいずれか
   - 効果の種類: `counter_set`
   - 詳細設定:

```json
{
  "probability": {
    "numerator": 14,
    "denominator": 141414
  },
  "counter_key": "ichiyon_almost_count",
  "value": 1
}
```

4. 抽選を行いたい対象へ特殊効果を付与

既存のはゆすモードとの違い:

- 旧 `bot/hayusu.py` はJSON外の専用ロジックで確率発動します。
- v3 DB基盤では、しこっち系のように特殊効果 `counter_set` + モード `counter_threshold` の方が自然です。

既存だけでは難しい部分:

- 「全投稿ごとに14/141414で抽選する」には、全投稿に安全に紐づくトリガーが必要です。
- 現状の自動反応は呼び出しワード前提です。
- 特定ユーザーの発言をオウム返しする返信タイプは未実装です。

追加実装候補:

- 特殊効果 `mode_enter` / `mode_roll` の実行処理を実装
- もしくは「全投稿トリガー」専用の安全な特殊効果発火ポイントを追加
- `echo_user_message` / `mimic_user` 返信タイプを追加
- 送信時に `AllowedMentions.none()` 相当を使う

### 3. ヒイロロボモードになり、3分間「シャドバすっげー楽しい！」しか言わなくなる

結論: モード中の3分固定返信は既存設定だけで可能です。突入も、何らかの自動反応やメンション反応を起点にするなら特殊効果カウンター経由で可能です。

設定だけでできる部分:

1. モード作成
   - `mode_key`: `hiiro`
   - `name`: `ヒイロロボモード`
   - `behavior_type`: `reply`
   - `duration_seconds`: `180`
   - `enabled`: ON

2. モード返信候補
   - 候補名: 任意
   - 本文: `シャドバすっげー楽しい！`
   - 出やすさ: `1`
   - 有効: ON

3. モード発動条件
   - 条件種類: `counter_threshold`
   - 条件JSON:

```json
{
  "counter_key": "hiiro_count",
  "operator": ">=",
  "threshold": 1
}
```

4. 特殊効果タグ
   - 効果の種類: `counter_set`
   - 詳細設定:

```json
{
  "counter_key": "hiiro_count",
  "value": 1
}
```

5. 発動元の自動反応・メンション候補・NGワードに特殊効果タグを付与

この設定により、特殊効果発火後に3分間は固定文言だけ返すモードにできます。

## 管理画面から設定できるか

設定可能です。

- 自動反応の作成・編集
- 自動反応への特殊効果付与
- 特殊効果タグ作成
- モード作成
- モード発動条件作成
- モード返答候補作成
- モード duration 設定

ただし、条件JSONや効果JSONは手入力です。管理画面UIとしては作れますが、事故防止のためにはプリセット化・フォーム化した方が安全です。

## 追加実装の最小案

### A. mode_enter / mode_roll を特殊効果として実装

最小変更:

- `execute_effects()` に `mode_enter` を追加
- `effect_config_json` 例:

```json
{
  "mode_key": "taketsumi"
}
```

- 対象モードを `bot_id + guild_id + mode_key` で取得
- 既にモード中なら何もしない
- `duration_seconds` を見て `mode_states` に入れる
- `enter_message` / ニックネーム / アイコン / ステータス変更を既存関数で実行

メリット:

- 「自動反応 → 特殊効果 → モード突入」が直感的になる
- カウンター用の中間設定が不要になる

DB migration:

- 既に `mode_enter` はDB制約・管理画面選択肢にあるため、基本的には不要です。

### B. echo_user_message / mimic_user を追加

最小変更候補:

- `modes.behavior_type` は増やさず、`appearance_config_json` に `reply_type` を持たせる

```json
{
  "reply_type": "echo_user_message",
  "target_user_ids": ["..."],
  "ignore_bots": true,
  "sanitize_mentions": true
}
```

- `handle_active_mode()` で `reply_type` を見て分岐
- 対象外ユーザー、Bot、自分自身は無視
- `@everyone` / `@here` / ユーザー/ロールメンションを無害化
- 送信時は `discord.AllowedMentions.none()` 相当

DB migration:

- JSON設定で始めるなら不要です。
- `behavior_type` に新値を追加するならCHECK制約変更が必要です。

### C. 管理画面フォーム改善

最小変更:

- 特殊効果 `counter_set` の確率・カウンターキー・値をフォーム化
- モード発動条件 `counter_threshold` と `probability` をフォーム化
- `mode_enter` 実装後は `mode_key` 選択UIを追加

DB migration:

- 不要です。

## 要望別まとめ

| 要望 | 特殊効果で実現できるか | 設定だけでできるか | 補足 |
| --- | --- | --- | --- |
| 「記憶パ」でタケツミロボモード | 可能 | 可能性高 | 自動反応「記憶パ」+ 特殊効果 `counter_set` + モード `counter_threshold` |
| 14/141414でいちよんほぼモード | 一部可能 | トリガーがあるなら可能 | `counter_set` に `probability` を設定。全投稿常時抽選は追加設計が必要 |
| 3分固定文言のヒイロロボモード | 可能 | 可能 | `duration_seconds=180` + 返信候補1件。突入は特殊効果カウンター経由 |
| いちよん本人の発言をオウム返し | 不可 | 不可 | `echo_user_message` / `mimic_user` 追加が必要 |

