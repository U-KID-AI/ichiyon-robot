# ver2.0 Effects and Modes Runtime MVP

## Scope

This phase only runs when:

```env
ICHIYON_DATA_BACKEND=db
```

`json` or an unset value keeps the existing JSON runtime path.

## Runtime Order

The DB backend processes guild messages in this order:

1. resolve `guild_id`
2. expire active mode if its duration has passed
3. if a mode is active, run only that mode behavior
4. check NG words
5. if an NG word matches, stop normal reactions and run only assigned NG-word effects
6. run mention reaction or auto reaction
7. run assigned effects for the matched reaction target
8. evaluate mode trigger conditions

During an active mode, mention reactions, auto reactions, NG-word normal handling, and special effect rolls are not executed.

## Special Effects MVP

Supported target types:

- `mention_reaction_choice`
- `auto_reaction`
- `ng_word`

Supported effect types:

- `probability_message`
- `counter_delta`
- `counter_set`

### probability_message

If the probability roll succeeds, `additional_text` is posted after the normal response.

Supported probability config shapes:

```json
{"probability": {"numerator": 1, "denominator": 32}}
{"numerator": 1, "denominator": 32}
{"chance_denominator": 32}
```

Template values use the same MVP values as DB reactions:

- `{user_name}`
- `{user_mention}`
- `{message_text}`
- `{match_1}`, `{match_2}`, ...

Unknown placeholders remain unchanged.

### counter_delta

Increments a counter by `delta`, `amount`, or `value`.

Example:

```json
{"counter_key": "narita_count", "delta": 1}
```

If the counter does not exist, the runtime creates it with `initial_value=0`.

### counter_set

Sets a counter to `set_value`, `value`, or `count`.

Example:

```json
{"counter_key": "shikocchi_count", "value": 1, "chance_denominator": 444}
```

The probability roll is applied before setting the value.

## Modes MVP

Supported active behavior:

- `reply`: choose an enabled `mode_reply_choices` row by weight and reply
- `offline`: do nothing and stop all other bot reactions

Supported trigger condition types:

- `counter_threshold`
- `probability`
- `period_not_triggered` is treated as pass-through in this MVP and logged as a limitation

Unsupported trigger types in this phase:

- `manual`
- `schedule`

Supported exit condition:

- `duration_elapsed`

The runtime stores active mode state in `mode_states.current_mode_id` and `mode_states.active_until`.

## Mode Entry

On entry:

- `mode_states` is updated
- `enter_message` is posted when set
- `enter_gif_path` is sent through the existing media helper when possible

Not yet implemented:

- bot icon change from `mode_icon_path`
- bot display name change
- notification channel routing

## Mode Exit

When `active_until` has passed:

- `mode_states` is cleared
- `exit_message` is posted when set
- `exit_gif_path` is sent through the existing media helper when possible
- shikocchi mode also posts the fixed recovery preset `まずは女子供から殺す`

That fixed preset is not managed by the admin UI.

## MVP Examples

Mini ichiyon:

- target: `mention_reaction_choice`
- effect type: `probability_message`
- config: `{"chance_denominator": 32}`
- additional text can use `{match_1}`

Narita count:

- target: `auto_reaction` or `ng_word`
- effect type: `counter_delta`
- config: `{"counter_key": "narita_count", "delta": 1}`
- mode trigger: `narita_count >= 22`

Shikocchi roll:

- target: `auto_reaction`
- effect type: `counter_set`
- config: `{"counter_key": "shikocchi_count", "value": 1, "chance_denominator": 444}`
- mode trigger: `shikocchi_count >= 1`

Hayusu MVP:

- mode trigger can use `probability` such as `{"denominator": 112}`
- period gating is not fully enforced yet

## Safety

Special effect and mode runtime errors are caught and logged. They should not stop the bot process.

Production DB runtime cutover remains a later phase.
