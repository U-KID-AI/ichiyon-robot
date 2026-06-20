# ver2.0 特殊効果タグ実行側

DB backend時だけ、付与済み特殊効果タグをBot実行側で処理する。
`ICHIYON_DATA_BACKEND=json` または未設定では既存JSON運用のまま。

## 既存効果

- `probability_message`: 確率に当たった時だけ追加投稿
- `counter_delta`: カウントを加算
- `counter_set`: カウントを指定値へ設定

## 追加した効果

### message

確率抽選なしで追加メッセージを投稿する。
本文は `additional_text` を優先し、なければ `effect_config_json.message` などを使う。

例:

```json
{"message": "追加で{match_1:hankaku}と言う"}
```

### reaction

メッセージへ絵文字リアクションを付ける。
Unicode絵文字とDiscordカスタム絵文字内部表記を想定。
失敗してもBot全体は止めない。

例:

```json
{"emoji": "🍒"}
```

```json
{"emoji_internal": "<:name:123456789012345678>"}
```

### probability_multiplier

抽選候補や自動反応の重みを倍率補正する。
`mention_reaction_choice` は候補抽選時に使用する。
`auto_reaction` は同じ優先度で複数一致した時だけ、候補選択の重みに使う。
対象が存在しない、または不正な場合はログを出して通常倍率のまま扱う。

例:

```json
{
  "multiplier": 9,
  "target": {
    "type": "mention_reaction_choice",
    "id": 123
  }
}
```

タグが候補自身に付与されている場合、`target` は省略できる。

```json
{"multiplier": 9}
```

### next_action_count

特殊効果が発動した後、同種アクションを追加で繰り返す。
無限ループ防止のため、追加回数は最大5回まで。

例:

```json
{"count": 2, "target_action": "same"}
```

`target_action` は以下を想定。

- `same`
- `mention_reaction_choice`
- `auto_reaction`

不正な値はログを出してスキップする。

## テンプレート

追加投稿は既存のテンプレート変換を使う。

- `{match_1}`
- `{match_2}`
- `{user_name}`
- `{user_mention}`
- `{message_text}`
- `{match_1:hankaku}`
- `{match_1:mini_ichiyon}`

未知の変換指定や存在しない変数ではBot全体を止めない。

## 確認

ローカルの軽い確認:

```powershell
python scripts/check_special_effects_runtime.py
```

DB移行リハーサルやstg反映は別作業で行う。
