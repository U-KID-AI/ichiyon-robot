# ver2.0 DB Presets

`scripts/seed_v2_presets.py` は、ver2.0 の DB backend で使う初期プリセットを作成するためのスクリプトです。

既存Botの JSON backend には接続しません。`ICHIYON_DATA_BACKEND=db` に切り替えた時に必要になるDB行を、手作業なしで用意する目的です。

## 実行例

```powershell
python scripts/seed_v2_presets.py `
  --database-url postgresql://ichiyon_robot:ichiyon_robot_password@localhost:5432/ichiyon_robot `
  --guild-id 123456789012345678 `
  --guild-name いちよんラボ
```

dry-run は実DBへ接続して既存行を確認しますが、最後に rollback します。

```powershell
python scripts/seed_v2_presets.py `
  --database-url postgresql://ichiyon_robot:ichiyon_robot_password@localhost:5432/ichiyon_robot `
  --guild-id 123456789012345678 `
  --dry-run
```

既存プリセットを更新したい場合だけ `--force` を付けます。通常実行では既存行を skip するため、同じ `guild_id` に何度実行しても重複作成しません。

## 作成するもの

### カウント

- `narita_count`
  - 名前: 成田カウント
  - リセット: `monthly_day` / `22`
- `shikocchi_count`
  - 名前: しこっちカウント
  - リセット: `manual`
  - モード突入後のリセットは runtime 側で行う想定

### 特殊効果タグ

- ミニいちよん
  - target: `mention_reaction_choice`
  - effect_type: `probability_message`
  - probability: `1/32`
  - additional_message: `:yukkuri_itiyon: ｲﾔ〜{match_1}ﾈ〜`
- 成田カウント加算（自動反応）
  - target: `auto_reaction`
  - effect_type: `counter_delta`
  - counter_key: `narita_count`
  - delta: `1`
- 成田カウント加算（NGワード）
  - target: `ng_word`
  - effect_type: `counter_delta`
  - counter_key: `narita_count`
  - delta: `1`
- しこっち抽選
  - target: `auto_reaction`
  - effect_type: `counter_set`
  - probability: `1/444`
  - counter_key: `shikocchi_count`
  - value: `1`
- ライオ9倍
  - target: `auto_reaction`
  - effect_type: `probability_multiplier`
  - 現時点では設定だけ作成し、実行は後続Phase
- さくらんぼ2回
  - target: `auto_reaction`
  - effect_type: `next_action_count`
  - 現時点では設定だけ作成し、実行は後続Phase

現在の `special_effect_tags` は1行につき1つの `target_type` を持つため、成田カウント加算は自動反応用とNGワード用を別タグとして作ります。

### モード

- はゆすモード
  - `mode_key`: `hayusu`
  - behavior: `reply`
  - trigger: probability `1/112`
  - cooldown_config: once_per_period / monthly / month_start
  - reply choice: `チェルさんこれギャバいっすよ`
  - duration: 180秒
- 成田モード
  - `mode_key`: `narita`
  - behavior: `reply`
  - trigger: `narita_count >= 22`
  - cooldown_config: once_per_period / monthly / day 22
  - reply choices:
    - お金の代わりにデータを持つ時代が到来する
    - 稼ぐより踊れ
    - まねきねこアルゴリズム
    - 泥だんご
    - アートークン
- しこっちモード
  - `mode_key`: `shikocchi`
  - behavior: `offline`
  - trigger: `shikocchi_count >= 1`
  - duration: 14分
  - enter_text: `しこっち、きた。`

しこっち終了後の `まずは女子供から殺す` はコード側の固定プリセットです。DBで編集する項目にはしません。

### メンション反応

- 名言
  - `reaction_key`: `quotes`
  - random draw
  - `is_system = true`
  - `is_deletable = false`
  - 候補は `scripts/migrate_json_to_db.py` で投入する想定
- おみくじ
  - `reaction_key`: `kuji`
  - pattern: `くじ`
  - match_type: `exact`
- お前も〇〇よな？
  - `reaction_key`: `omae_mo_yona`
  - pattern: `お前も(.+?)よな？`
  - match_type: `regex`
  - choice: `いや〜{match_1}ね〜`
  - このchoiceにミニいちよんタグを付与
- デッキ検索
  - `reaction_key`: `deck_search`
  - search
  - `is_system = true`
  - pattern: `デッキ検索`
  - match_type: `prefix`
  - `config_json.search_type = deck_search`
  - 初期状態は `enabled = false`

デッキ検索は最初の機能一覧には出さず、メンション反応の検索型固定機能として扱います。実際のX検索、QR/クラス判定、画像判定ロジックは後続Phaseです。

### 自動反応 / NGワード

- 自動反応 `しこっち`
  - response_text: `しこっちきたぁぁぁ`
  - しこっち抽選タグを付与
- NGワード `お金`
  - 成田カウント加算（NGワード）を付与
- NGワード `データ`
  - 成田カウント加算（NGワード）を付与

DB runtime MVPではNGワード検知時に通常反応を止めます。`お金` / `データ` は成田カウント用の例として作るため、サーバー運用に合わない場合は管理画面で無効化してください。

## 再実行安全性

スクリプトは以下のキーで既存行を判定します。

- guild: `guild_id`
- counters: `guild_id + count_key`
- special_effect_tags: `guild_id + name`
- modes: `guild_id + mode_key`
- mention_reactions: `guild_id + reaction_key`
- mention_reaction_choices: `guild_id + mention_reaction_id + name`
- auto_reactions: `guild_id + trigger_text + match_type`
- ng_words: `guild_id + word`
- assignments: `special_effect_tag_id + target_type + target_id`

通常実行では既存行を更新せず `skipped` にします。`--force` の時だけ、プリセット定義に合わせて既存行を更新します。

## サーバー別の使い分け

- いちよんラボ
  - ver2.0 DB backendの検証用。まずdry-run、本実行、DB backend起動確認の順で使います。
- ランセ地方
  - 本番に近い動作確認用。既存JSON運用との差分を見ながら、必要なプリセットだけONにします。
- レストラン
  - 運用ルールが異なる可能性があるため、実行後にNGワードやデッキ検索の有効状態を必ず確認します。

## 本番注意

このスクリプトにBot Token、Discord OAuth Client Secret、秘密鍵は不要です。`.env` や本番データ、`data/backups` も触りません。

本番相当DBで使う場合は、必ず先に `--dry-run` を実行し、対象 `guild_id` が正しいことを確認してください。`--force` は既存プリセット行を更新するため、検証DBで結果を確認してから使います。
