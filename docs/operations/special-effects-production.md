# 本番特殊効果タグ棚卸し

調査日: 2026-07-30

本番DBを読み取り専用で確認した結果です。Discord Token、Cookie、`.env` 実値、個人を特定するDiscord IDは記載しません。

## 概要

本番の特殊効果は `special_effect_tags` と `special_effect_assignments` に通常データとして登録されています。モード突入系は特殊効果が `current_mode_id` を直接変更するのではなく、`counter_set` / `counter_delta` で状態を更新し、`mode_trigger_conditions` と `enter_mode_if_needed()` が正式なモード遷移を行います。

## 12タグの実体

| 表示上の呼び方 | 本番DB上のタグ名 | effect_type | 主な発動元 | 通常応答抑止 |
| --- | --- | --- | --- | --- |
| おい　あるんだが | おい あるんだが | `probability_message` | 自動反応 `自我ある？` | なし |
| さんを付けろよ | さんを付けろよ / さん付けガード | `mention_suffix_guard` | 特殊ユーザーメンションルール | 条件不一致時に抑止 |
| タケツミロボ | タケツミロボ突入カウンター | `counter_set` | 自動反応 `記憶パ` | なし |
| ホンモノ検知 | ホンモノ検知メッセージ | `message` | 特殊ユーザーメンションルール | なし |
| ミニいちよん | ミニいちよん | `probability_message` | メンション反応選択肢 `お前も〇〇よな？` | なし |
| 竜ヶ崎ヒイロ | 竜ヶ崎ヒイロ突入カウンター | `counter_set` | 自動反応 `シャドバ` / `スマホ` | なし |
| しこっち抽選 | しこっち抽選 | `counter_set` | 自動反応 `しこっち` | なし |
| 成田カウント | 成田カウント加算（自動反応） / 成田カウント加算（NGワード） | `counter_delta` | 自動反応 / NGワード | なし |
| さくらんぼ | さくらんぼ / さくらんぼ2回 | `next_action_count` | 自動反応 `さくらんぼ` | なし |
| ライオ9倍 | ライオ9倍 | `probability_multiplier` | 自動反応 `ライオ` | なし |
| 破壊 | 破壊 | `destroy` | メンション反応選択肢 `お前も〇〇よな？` / 特殊ユーザーメンションルール候補 | なし |
| 成田カウント表示 | 成田カウント表示 | `message` | 自動反応 `成田カウント` | なし |

## 状態変更経路

- `counter_delta`: `CounterRepository.increment()` でカウンターを加算します。
- `counter_set`: `CounterRepository.set_value()` でカウンターを指定値にします。
- `しこっち抽選`: `shikocchi_count` を `counter_set` し、`mode_trigger_conditions.counter_threshold` を `enter_mode_if_needed()` が評価します。
- `タケツミロボ`: `taketsumi_count` を `counter_set` し、モード側条件で突入します。
- `竜ヶ崎ヒイロ`: `hiiro_count` を `counter_set` し、モード側条件で突入します。

特殊効果から `mode_states.current_mode_id` を直接変更する経路はありません。

## 特殊効果として妥当なもの

次は特殊効果として妥当です。

- 追加メッセージ: `message`, `probability_message`
- 追加リアクション: `reaction`
- 確率付き追加リアクション: `reaction` + `effect_config_json.probability`
- カウンター変更: `counter_delta`, `counter_set`
- 一時倍率: `probability_multiplier`
- 次回処理予約: `next_action_count`

## 将来専用機能へ移す候補

- 成田カウント / 成田カウント表示: 将来の成田クイズ実装時に、得点・表示・集計を専用Serviceへ移す候補です。
- モード突入の成立条件そのもの: 状態所有や正式遷移は引き続きModeService側へ寄せます。

## 運用メモ

- `さんを付けろよ` は、対象ユーザーが必要な敬称条件を満たさない場合に警告し、通常メンション応答を抑止します。
- `破壊` は既存の `destroy` effect として維持します。
- `しこっち抽選` は特殊効果からモードへ直接入らず、カウンター経由の正式経路を維持します。
- `random_reaction_settings` は既存データ保持のため残します。専用ランダムリアクション経路を完全に置換できる場合のみ、将来deprecatedとして実行参照から外します。
