# ver2.0 モード期間内発動制限

DB backendでは、`mode_trigger_conditions.condition_type = period_not_triggered` を実際に判定する。
対象は自然発動するモードの「この期間ではまだ発動していない」条件。

## 期間の種類

### monthly / month_start

- 毎月1日 00:00を期間開始にする
- はゆすモード用
- 例: 2026年6月中は `2026-06-01 00:00` 以降が同じ期間

設定例:

```json
{"period": "monthly", "reset": "month_start"}
```

### monthly / day 22

- 毎月22日 00:00を期間開始にする
- 22日より前なら、前月22日 00:00からの期間として扱う
- 成田モード用

設定例:

```json
{"period": "monthly", "reset": {"type": "day", "day": 22}}
```

または:

```json
{"period": "monthly", "day": 22}
```

## 発動履歴

モード突入時に `mode_trigger_history` へ記録する。

- `guild_id + mode_id + period_key` で一意
- 同じ期間内に履歴があれば `period_not_triggered` は false
- 次の期間に入ると、新しい `period_key` になるため true に戻る
- Bot再起動後もDB上の履歴で判定する

## モード別

- はゆす: `probability 1/112` と `monthly / month_start`
- 成田: `narita_count >= 22` と `monthly / day 22`
- しこっち: 期間内発動制限は使わない

## 注意

期間境界は日本時間の 00:00 基準で計算する。
DBには期間キーと発動日時を保存し、実行側だけが参照する。
